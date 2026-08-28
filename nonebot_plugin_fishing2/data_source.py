import asyncio
import copy
import random
import time
import json

from collections.abc import Hashable
from sqlalchemy import select, update, delete
from sqlalchemy.sql.expression import func
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot_plugin_orm import get_session

from .config import config
from .model import FishingRecord, SpecialFishes
from .fish_helper import *

# ---- nonebot-plugin-value optional integration ----
_value_available = False
_currency_id = None

try:
    from nonebot import require
    require("nonebot_plugin_value")
    from nonebot_plugin_value.api.api_balance import (
        get_or_create_account as _get_or_create_account,
        add_balance as _add_balance,
        del_balance as _del_balance,
    )
    from nonebot_plugin_value.api.api_currency import (
        get_or_create_currency as _get_or_create_currency,
    )
    _value_available = True
except Exception:
    _value_available = False


def _get_fishing_currency_id():
    global _currency_id
    if _currency_id:
        return _currency_id
    return "fishing_" + config.fishing_coin_name.replace(" ", "_").lower()


async def init_currency():
    global _currency_id
    if not _value_available:
        return

    from nonebot import logger
    from nonebot_plugin_orm import get_session as _get_session
    from nonebot_plugin_value.api.api_currency import CurrencyData
    from nonebot_plugin_value.models.currency import CurrencyMeta
    from sqlalchemy import select as _select

    session = _get_session()
    async with session.begin():
        stmt = _select(CurrencyMeta).where(
            CurrencyMeta.display_name == config.fishing_coin_name
        )
        result = await session.execute(stmt)
        currencies = result.scalars().all()

        if currencies:
            existing_currency = currencies[0]
            _currency_id = existing_currency.id
            logger.info(
                f"Reusing existing currency '{config.fishing_coin_name}' (ID: {_currency_id})"
            )
            if len(currencies) > 1:
                logger.warning(
                    f"Found {len(currencies)} duplicate currencies with display_name "
                    f"'{config.fishing_coin_name}'. Using first one (ID: {_currency_id})"
                )
        else:
            _currency_id = _get_fishing_currency_id()
            currency_data = CurrencyData(
                id=_currency_id,
                display_name=config.fishing_coin_name,
                symbol=config.fishing_coin_name[0] if config.fishing_coin_name else "\u5e01",
                default_balance=0.0,
                allow_negative=False,
            )
            await _get_or_create_currency(currency_data)

    # Migrate old coin data to nonebot-plugin-value
    await _migrate_old_coin_data()


async def _migrate_old_coin_data():
    if not _value_available:
        return

    from nonebot import logger

    session = get_session()
    async with session.begin():
        stmt = select(FishingRecord).where(FishingRecord.coin > 0)
        result = await session.execute(stmt)
        records = result.scalars().all()

        if not records:
            return

        logger.info(f"Migrating coin data for {len(records)} users to nonebot-plugin-value...")
        for record in records:
            if record.coin > 0:
                await _add_balance(
                    record.user_id, record.coin, "migration_from_coin_column", _get_fishing_currency_id()
                )
                logger.info(
                    f"Migrated {record.coin} {config.fishing_coin_name} for user {record.user_id}"
                )
                # Zero out the old coin column after migration
                await session.execute(
                    update(FishingRecord)
                    .where(FishingRecord.id == record.id)
                    .values(coin=0)
                )

        await session.commit()
        logger.info("Coin data migration complete.")


# ---- Balance abstraction layer ----

async def get_user_balance(user_id):
    if _value_available:
        currency_id = _get_fishing_currency_id()
        account = await _get_or_create_account(user_id, currency_id)
        return int(account.balance)
    else:
        session = get_session()
        async with session.begin():
            record = await session.scalar(
                select(FishingRecord).where(FishingRecord.user_id == user_id)
            )
            return record.coin if record else 0


async def add_user_balance(user_id, amount, source="fishing"):
    if _value_available:
        currency_id = _get_fishing_currency_id()
        await _add_balance(user_id, amount, source, currency_id)
        account = await _get_or_create_account(user_id, currency_id)
        return int(account.balance)
    else:
        time_now = int(time.time())
        session = get_session()
        async with session.begin():
            record = await session.scalar(
                select(FishingRecord).where(FishingRecord.user_id == user_id)
            )
            if record:
                new_coin = record.coin + amount
                await session.execute(
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(coin=new_coin)
                )
                await session.commit()
                return new_coin
            new_record = FishingRecord(
                user_id=user_id,
                time=time_now,
                frequency=0,
                fishes="{}",
                special_fishes="{}",
                coin=amount,
                achievements="[]",
            )
            session.add(new_record)
            await session.commit()
            return amount


async def del_user_balance(user_id, amount, source="fishing"):
    if _value_available:
        currency_id = _get_fishing_currency_id()
        await _del_balance(user_id, amount, source, currency_id)
        account = await _get_or_create_account(user_id, currency_id)
        return int(account.balance)
    else:
        session = get_session()
        async with session.begin():
            record = await session.scalar(
                select(FishingRecord).where(FishingRecord.user_id == user_id)
            )
            if record:
                new_coin = record.coin - amount
                await session.execute(
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(coin=new_coin)
                )
                await session.commit()
                return new_coin
            return 0


def get_key_by_index(dict, index, default=None):
    key_list = list(dict.keys())
    return key_list[index] if index < len(key_list) else default


async def can_fishing(user_id):
    time_now = int(time.time())
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        return True if not record else record.time < time_now


async def can_catch_special_fish(probability_add):
    session = get_session()
    async with session.begin():
        records = await session.execute(select(SpecialFishes))
        return (
            len(records.all()) != 0
            and random.random() <= config.special_fish_probability + probability_add
        )


async def check_tools(user_id, tools=None, check_have=True):
    if not tools or tools == []:
        return None

    for tool in tools:
        fish = get_fish_by_name(tool)
        if not fish:
            return f"你在用什么钓鱼……？{tool}？"

        props = fish.props
        if not props or props == []:
            return f"搞啥嘞！{tool}既不是工具也不是鱼饵！"

    if len(tools) == 2:
        if get_fish_by_name(tools[0]).type == get_fish_by_name(tools[1]).type:
            return "你为啥要用两个类型一样的东西？"

    if check_have:
        session = get_session()
        async with session.begin():
            select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
            fishes_record = await session.scalar(select_user)
            if fishes_record:
                loads_fishes = json.loads(fishes_record.fishes)
                for tool in tools:
                    if tool not in loads_fishes:
                        return f"你哪来的{tool}？"

    return None


async def remove_tools(user_id, tools=None):
    if not tools or tools == []:
        return None

    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        fishes_record = await session.scalar(select_user)
        if fishes_record:
            loads_fishes = json.loads(fishes_record.fishes)
            for tool_name in tools:
                if tool_name == "永恒鱼竿":
                    continue
                loads_fishes[tool_name] -= 1
                if loads_fishes[tool_name] == 0:
                    del loads_fishes[tool_name]
            dump_fishes = json.dumps(loads_fishes)
            user_update = (
                update(FishingRecord)
                .where(FishingRecord.user_id == user_id)
                .values(fishes=dump_fishes)
            )
            await session.execute(user_update)
            await session.commit()


def get_adjusts_from_tools(tools=None):
    no_add = 0
    sp_add = 0
    adjusts = []

    if tools:
        for tool in tools:
            adjusts += get_fish_by_name(tool).props

    for adjust in adjusts:
        if adjust.type == "special_fish":
            sp_add += adjust.value
        if adjust.type == "no_fish":
            no_add += adjust.value

    return adjusts, no_add, sp_add


def adjusted(adjusts=None):
    adjusted_fishes = copy.deepcopy(can_catch_fishes)

    for adjust in adjusts:
        if adjust.key and adjust.key not in adjusted_fishes:
            continue
        match adjust.type:
            case "normal_fish":
                for key, weight in can_catch_fishes.items():
                    if weight >= config.rare_fish_weight and key in adjusted_fishes:
                        adjusted_fishes[key] += adjust.value
            case "rare_fish":
                for key, weight in can_catch_fishes.items():
                    if weight < config.rare_fish_weight and key in adjusted_fishes:
                        adjusted_fishes[key] += adjust.value
            case "fish":
                adjusted_fishes[adjust.key] += adjust.value
            case "rm_fish":
                adjusted_fishes.pop(adjust.key)
            case "special_fish" | "no_fish":
                pass
            case _:
                pass

    adjusted_fishes_list = list(adjusted_fishes.keys())
    adjusted_weights = list(adjusted_fishes.values())

    for i in range(len(adjusted_weights)):
        if adjusted_weights[i] < 0:
            adjusted_weights[i] = 0

    return adjusted_fishes_list, adjusted_weights


def choice(adjusts=None):
    adjusted_fishes_list, adjusted_weights = adjusted(adjusts)
    choices = random.choices(
        adjusted_fishes_list,
        weights=adjusted_weights,
    )
    return choices[0]


async def get_fish(user_id, tools=None):
    adjusts, no_add, sp_add = get_adjusts_from_tools(tools)

    if random.random() < config.no_fish_probability + no_add:
        await asyncio.sleep(random.randint(10, 20))
        return "QAQ你空军了，什么都没钓到"

    if await can_catch_special_fish(sp_add):
        special_fish_name = await random_get_a_special_fish()
        await asyncio.sleep(random.randint(10, 20))
        await save_special_fish(user_id, special_fish_name)
        result = f"你钓到了别人放生的 {special_fish_name}"
        return result

    fish = choice(adjusts)
    sleep_time = get_fish_by_name(fish).sleep_time
    result = f"钓到了一条{fish}, 你把它收进了背包里"
    await asyncio.sleep(sleep_time)
    await save_fish(user_id, fish)
    return result


def predict(tools=None):
    no = config.no_fish_probability
    sp = config.special_fish_probability
    sp_price = config.special_fish_price
    result = ""

    adjusts, no_add, sp_add = get_adjusts_from_tools(tools)
    sp_t = min(max(sp + sp_add, 0), 1)
    no_t = min(max(no + no_add, 0), 1)

    adjusted_fishes_list, adjusted_weights = adjusted(adjusts)

    adjusted_fishes_value = []
    for fish_name in adjusted_fishes_list:
        fish = get_fish_by_name(fish_name)
        adjusted_fishes_value.append(int(fish.price * fish.amount))

    total_weight = sum(adjusted_weights)
    probabilities = [w / total_weight for w in adjusted_weights]
    expected_value = sum(v * p for v, p in zip(adjusted_fishes_value, probabilities))

    result += f"鱼列表：[{', '.join(adjusted_fishes_list)}]\n"
    result += f'''概率列表: [{', '.join([str(round(w * 100, 2)) + "%" for w in probabilities])}]\n'''
    result += f"特殊鱼概率：{round(sp_t * (1 - no_t), 6)}\n"
    result += f"空军概率：{round(no_t, 6)}\n"

    expected_value = expected_value * (1 - no_t)
    result += f"无特殊鱼时期望为：{expected_value:.3f}\n"

    expected_value = expected_value * (1 - sp_t) + sp_price * sp_t * (1 - no_t)
    result += f"有特殊鱼期望为：{expected_value:.3f}"

    return result


async def random_get_a_special_fish():
    session = get_session()
    async with session.begin():
        random_select = select(SpecialFishes).order_by(func.random())
        data = await session.scalar(random_select)
        return data.fish


async def check_achievement(user_id):
    session = get_session()
    async with session.begin():
        record = await session.scalar(
            select(FishingRecord).where(FishingRecord.user_id == user_id)
        )
        if not record:
            return None
        fishing_frequency = record.frequency
        user_fishes = json.loads(record.fishes)
        achievements = config_achievements
        result_list = []
        for achievement in achievements:
            achievement_name = achievement.name
            if await is_exists_achievement(user_id, achievement_name):
                continue
            if (
                achievement.type == "fishing_frequency"
                and achievement.data <= fishing_frequency
            ) or (achievement.type == "fish_type" and achievement.data in user_fishes):
                await save_achievement(user_id, achievement_name)
                result_list.append(
                    f"""达成成就: {achievement_name}\n{achievement.description}"""
                )
        return result_list if result_list != [] else None


async def is_exists_achievement(user_id, achievement_name):
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        if record:
            loads_achievements = json.loads(record.achievements)
            return achievement_name in loads_achievements
        return False


async def save_achievement(user_id, achievement_name):
    time_now = int(time.time())
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        if record:
            loads_achievements = json.loads(record.achievements)
            loads_achievements.append(achievement_name)
            dump_achievements = json.dumps(loads_achievements)
            user_update = (
                update(FishingRecord)
                .where(FishingRecord.user_id == user_id)
                .values(achievements=dump_achievements)
            )
            await session.execute(user_update)
            await session.commit()
            return
        data = []
        dump_achievements = json.dumps(data)
        new_record = FishingRecord(
            user_id=user_id,
            time=time_now,
            frequency=0,
            fishes="{}",
            special_fishes="{}",
            achievements=dump_achievements,
        )
        session.add(new_record)
        await session.commit()


async def save_fish(user_id, fish_name):
    time_now = int(time.time())
    fishing_cooldown = random.randint(
        config.fishing_cooldown_time_min, config.fishing_cooldown_time_max
    )
    amount = get_fish_by_name(fish_name).amount
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        if record:
            loads_fishes = json.loads(record.fishes)
            try:
                loads_fishes[fish_name] += amount
            except KeyError:
                loads_fishes[fish_name] = amount
            dump_fishes = json.dumps(loads_fishes)
            new_frequency = record.frequency + 1
            user_update = (
                update(FishingRecord)
                .where(FishingRecord.user_id == user_id)
                .values(
                    time=time_now + fishing_cooldown,
                    frequency=new_frequency,
                    fishes=dump_fishes,
                )
            )
            await session.execute(user_update)
            await session.commit()
            return
        data = {fish_name: amount}
        dump_fishes = json.dumps(data)
        new_record = FishingRecord(
            user_id=user_id,
            time=time_now + fishing_cooldown,
            frequency=1,
            fishes=dump_fishes,
            special_fishes="{}",
            achievements="[]",
        )
        session.add(new_record)
        await session.commit()


async def save_special_fish(user_id, fish_name):
    time_now = int(time.time())
    fishing_cooldown = random.randint(
        config.fishing_cooldown_time_min, config.fishing_cooldown_time_max
    )
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        if record:
            loads_fishes = json.loads(record.special_fishes)
            try:
                loads_fishes[fish_name] += 1
            except KeyError:
                loads_fishes[fish_name] = 1
            dump_fishes = json.dumps(loads_fishes)
            user_update = (
                update(FishingRecord)
                .where(FishingRecord.user_id == user_id)
                .values(
                    time=time_now + fishing_cooldown,
                    frequency=record.frequency + 1,
                    special_fishes=dump_fishes,
                )
            )
            await session.execute(user_update)
        else:
            data = {fish_name: 1}
            dump_fishes = json.dumps(data)
            new_record = FishingRecord(
                user_id=user_id,
                time=time_now + fishing_cooldown,
                frequency=1,
                fishes="{}",
                special_fishes=dump_fishes,
                achievements=[],
            )
            session.add(new_record)
        select_fish = (
            select(SpecialFishes)
            .where(SpecialFishes.fish == fish_name)
            .order_by(SpecialFishes.id)
            .limit(1)
        )
        record = await session.scalar(select_fish)
        fish_id = record.id
        delete_fishes = delete(SpecialFishes).where(SpecialFishes.id == fish_id)
        await session.execute(delete_fishes)
        await session.commit()


async def sell_fish(user_id, name_or_index, quantity=1, as_index=False, as_special=False):
    if quantity <= 0:
        return "你在卖什么 w(ﾟДﾟ)w"

    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        fishes_record = await session.scalar(select_user)
        if fishes_record := fishes_record:
            loads_fishes = json.loads(fishes_record.fishes)
            loads_fishes = {
                key: loads_fishes[key] for key in fish_list if key in loads_fishes
            }

            spec_fishes = json.loads(fishes_record.special_fishes)
            spec_fishes = dict(sorted(spec_fishes.items()))

            if as_index:
                if not name_or_index.isdigit():
                    return "你完全不看帮助是吗 ￣へ￣"
                load_dict = loads_fishes if not as_special else spec_fishes
                fish_name = get_key_by_index(load_dict, int(name_or_index))
                if not fish_name:
                    return "查无此鱼"
            else:
                fish_name = name_or_index

            if (
                not as_special
                and fish_name in loads_fishes
                and loads_fishes[fish_name] > 0
            ):
                if fish_name not in can_sell_fishes:
                    return f"这个 {fish_name} 不可以卖哦~"
                if loads_fishes[fish_name] < quantity:
                    return f"你没有那么多 {fish_name}"
                fish_price = get_fish_by_name(fish_name).price
                loads_fishes[fish_name] -= quantity
                if loads_fishes[fish_name] == 0:
                    del loads_fishes[fish_name]
                dump_fishes = json.dumps(loads_fishes)
                user_update = (
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(fishes=dump_fishes)
                )
                await session.execute(user_update)
                await session.commit()

                earned = fish_price * quantity
                await add_user_balance(user_id, earned, f"sell_fish_{fish_name}")

                return (
                    f"你以 {fish_price} {fishing_coin_name} / 条的价格卖出了 {quantity} 条 {fish_name}, "
                    f"你获得了 {earned} {fishing_coin_name}"
                )
            elif fish_name in spec_fishes and spec_fishes[fish_name] > 0:
                fish_price = config.special_fish_price
                if spec_fishes[fish_name] < quantity:
                    return f"你没有那么多 {fish_name}"
                spec_fishes[fish_name] -= quantity
                if spec_fishes[fish_name] == 0:
                    del spec_fishes[fish_name]
                dump_fishes = json.dumps(spec_fishes)
                user_update = (
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(special_fishes=dump_fishes)
                )
                await session.execute(user_update)
                await session.commit()

                earned = fish_price * quantity
                await add_user_balance(user_id, earned, f"sell_special_fish_{fish_name}")

                return (
                    f"你以 {fish_price} {fishing_coin_name} / 条的价格卖出了 {quantity} 条 {fish_name}, "
                    f"获得了 {earned} {fishing_coin_name}"
                )
            else:
                return "查无此鱼"
        else:
            return "还没钓鱼就想卖鱼?"


async def buy_fish(user_id, fish_name, quantity=1):
    if quantity <= 0:
        return "别在渔具店老板面前炫耀自己的鱼 (..-˘ ˘-.#)"
    if fish_name not in can_buy_fishes:
        return "商店不卖这个！"

    fish = get_fish_by_name(fish_name)
    total_price = int(fish.buy_price * fish.amount * quantity)

    balance = await get_user_balance(user_id)
    if balance < total_price:
        coin_less = str(total_price - balance)
        return f"你没有足够的 {fishing_coin_name}, 还需 {coin_less} {fishing_coin_name}"

    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        fishes_record = await session.scalar(select_user)
        if fishes_record := fishes_record:
            loads_fishes = json.loads(fishes_record.fishes)
            try:
                loads_fishes[fish_name] += fish.amount * quantity
            except KeyError:
                loads_fishes[fish_name] = fish.amount * quantity
            dump_fishes = json.dumps(loads_fishes)
            user_update = (
                update(FishingRecord)
                .where(FishingRecord.user_id == user_id)
                .values(
                    fishes=dump_fishes,
                    total_spent=(fishes_record.total_spent or 0) + total_price,
                )
            )
            await session.execute(user_update)
            await session.commit()

    await del_user_balance(user_id, total_price, f"buy_fish_{fish_name}")
    return f"你用 {total_price} {fishing_coin_name} 买入了 {quantity} 份 {fish_name}"


async def free_fish(user_id, fish_name):
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        fishes_record = await session.scalar(select_user)
        if fishes_record:
            spec_fishes = json.loads(fishes_record.special_fishes)
            if fish_name in spec_fishes and spec_fishes[fish_name] > 0:
                spec_fishes[fish_name] -= 1
                if spec_fishes[fish_name] == 0:
                    del spec_fishes[fish_name]
                new_record = SpecialFishes(user_id=user_id, fish=fish_name)
                session.add(new_record)
                dump_fishes = json.dumps(spec_fishes)
                user_update = (
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(special_fishes=dump_fishes)
                )
                await session.execute(user_update)
                await session.commit()
                return f"你再次放生了 {fish_name}, 未来或许会被有缘人钓到呢"
            else:
                if fish_name in fish_list:
                    return "普通鱼不能放生哦~"

                balance = await get_user_balance(user_id)
                if balance < config.special_fish_free_price:
                    special_fish_coin_less = str(
                        config.special_fish_free_price - balance
                    )
                    return f"你没有足够的 {fishing_coin_name}, 还需 {special_fish_coin_less} {fishing_coin_name}"

                new_record = SpecialFishes(user_id=user_id, fish=fish_name)
                session.add(new_record)
                await session.commit()

    await del_user_balance(user_id, config.special_fish_free_price, f"free_fish_{fish_name}")
    return f"你花费 {config.special_fish_free_price} {fishing_coin_name} 放生了 {fish_name}, 未来或许会被有缘人钓到呢"


async def lottery(user_id):
    session = get_session()
    time_now = int(time.time())
    fishing_cooldown = random.randint(
        config.fishing_cooldown_time_min, config.fishing_cooldown_time_max
    )
    balance = await get_user_balance(user_id)

    if balance < 0:
        new_coin = random.randrange(1, 50)
        await add_user_balance(user_id, new_coin - balance, "lottery_negative_balance")
        return f"你是不是被哪个坏心眼的神惩罚了……河神帮你还完了欠款"
    if balance <= 30:
        new_coin = random.randrange(1, 50)
        await add_user_balance(user_id, new_coin, "lottery_poor")
        return f"你穷得连河神都看不下去了，给了你 {new_coin} {fishing_coin_name} w(ﾟДﾟ)w"
    new_coin = abs(balance) / 3
    new_coin = random.randrange(5000, 15000) / 10000 * new_coin
    new_coin = int(new_coin) if new_coin > 1 else 1
    new_coin *= random.randrange(-1, 2, 2)

    if new_coin >= 0:
        await add_user_balance(user_id, new_coin, "lottery_win")
    else:
        await del_user_balance(user_id, abs(new_coin), "lottery_loss")

    return f'你{"获得" if new_coin >= 0 else "血亏"}了 {abs(new_coin)} {fishing_coin_name}'


async def give(user_id, name_or_index, quantity=1, as_index=False, as_special=False):
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        if record:

            if name_or_index == "coin" or name_or_index == fishing_coin_name:
                if quantity >= 0:
                    await add_user_balance(user_id, quantity, "admin_give_coin")
                else:
                    await del_user_balance(user_id, abs(quantity), "admin_take_coin")
                return f"使用滥权之力成功为 {user_id} {'增加' if quantity >= 0 else '减少'} {abs(quantity)} {fishing_coin_name} ヾ(≧▽≦*)o"

            loads_fishes = json.loads(record.fishes)
            spec_fishes = json.loads(record.special_fishes)

            if as_index:
                if not name_or_index.isdigit():
                    return "你完全不看帮助是吗 ￣へ￣"
                load_dict = loads_fishes if not as_special else spec_fishes
                fish_name = get_key_by_index(load_dict, int(name_or_index))
                if not fish_name:
                    return "查无此鱼，你再看看这人背包呢？"
            else:
                fish_name = name_or_index

            if not as_special and fish_name in fish_list:
                try:
                    loads_fishes[fish_name] += quantity
                except KeyError:
                    loads_fishes[fish_name] = quantity
                if loads_fishes[fish_name] <= 0:
                    del loads_fishes[fish_name]
                dump_fishes = json.dumps(loads_fishes)
                user_update = (
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(fishes=dump_fishes)
                )
                await session.execute(user_update)
                await session.commit()
            else:
                try:
                    spec_fishes[fish_name] += quantity
                except KeyError:
                    spec_fishes[fish_name] = quantity
                if spec_fishes[fish_name] <= 0:
                    del spec_fishes[fish_name]
                dump_fishes = json.dumps(spec_fishes)
                user_update = (
                    update(FishingRecord)
                    .where(FishingRecord.user_id == user_id)
                    .values(special_fishes=dump_fishes)
                )
                await session.execute(user_update)
                await session.commit()

            fish_name = (
                fish_name[:20] + "..." + str(len(fish_name) - 20)
                if len(fish_name) > 20
                else fish_name
            )
            return f"使用滥权之力成功为 {user_id} {'增加' if quantity >= 0 else '减少'} {abs(quantity)} 条 {fish_name} ヾ(≧▽≦*)o"
        return "未查找到用户信息, 无法执行滥权操作 w(ﾟДﾟ)w"


async def get_all_special_fish():
    session = get_session()
    async with session.begin():
        random_select = select(SpecialFishes.fish).order_by(SpecialFishes.fish.asc())
        data = await session.scalars(random_select)
        pool = data.all()

    result = dict()
    for fish in pool:
        try:
            result[fish] += 1
        except KeyError:
            result[fish] = 1

    return result


async def remove_special_fish(name_or_index, as_index=False):
    pool = await get_all_special_fish()

    if as_index:
        if not name_or_index.isdigit():
            return "你完全不看帮助是吗 ￣へ￣"
        fish_name = get_key_by_index(pool, int(name_or_index))
        if not fish_name:
            return "查无此鱼"
    else:
        fish_name = name_or_index
        if fish_name not in pool:
            return "查无此鱼"

    session = get_session()
    async with session.begin():
        delete_fishes = delete(SpecialFishes).where(SpecialFishes.fish == fish_name)
        await session.execute(delete_fishes)
        await session.commit()

    fish_name = (
        fish_name[:20] + "..." + str(len(fish_name) - 20)
        if len(fish_name) > 20
        else fish_name
    )

    return f"已成功捞出 {fish_name}"


async def get_pool(name_limit=30, page_limit=200):
    messages = []
    pool = await get_all_special_fish()
    messages.append(
        MessageSegment.text(f"现在鱼池里面有 {sum(list(pool.values()))} 条鱼。")
    )

    msg = "鱼池列表：\n"
    i = 0
    j = 1
    for fish, num in pool.items():
        if len(msg) > page_limit:
            fish = (
                fish[:name_limit] + "..." + str(len(fish) - name_limit)
                if len(fish) > name_limit
                else fish
            )
            msg += f"{i}. {fish} x {num}\n"
            msg += f"【第 {j} 页结束】"
            messages.append(MessageSegment.text(msg))
            msg = ""
            i += 1
            j += 1
        else:
            fish = (
                fish[:name_limit] + "..." + str(len(fish) - name_limit)
                if len(fish) > name_limit
                else fish
            )
            msg += f"{i}. {fish} x {num}\n"
            i += 1
    else:
        messages.append(MessageSegment.text(msg))

    return messages


async def get_fishing_achievement_stats(user_id):
    """暴露给银趴插件成就扫描的钓鱼统计（可选集成）。

    返回字段：
    - frequency: 累计钓鱼次数
    - caught_all_catchable: 是否集齐所有可钓取的鱼
    - has_special: 是否钓到过特殊鱼
    - total_spent: 商店累计消费
    - has_eternal_rod: 是否拥有永恒鱼竿
    """
    session = get_session()
    async with session.begin():
        record = await session.scalar(
            select(FishingRecord).where(FishingRecord.user_id == user_id)
        )
        if not record:
            return {
                "frequency": 0,
                "caught_all_catchable": False,
                "has_special": False,
                "total_spent": 0,
                "has_eternal_rod": False,
            }
        frequency = record.frequency or 0
        fishes = json.loads(record.fishes or "{}")
        special_fishes = json.loads(record.special_fishes or "{}")
        total_spent = record.total_spent or 0
        can_catch_names = list(can_catch_fishes.keys())
        caught_all_catchable = all(name in fishes for name in can_catch_names)
        return {
            "frequency": frequency,
            "caught_all_catchable": caught_all_catchable,
            "has_special": bool(special_fishes),
            "total_spent": total_spent,
            "has_eternal_rod": "永恒鱼竿" in fishes,
        }


async def get_stats(user_id):
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        fishing_record = await session.scalar(select_user)
        if fishing_record:
            return f"🐟你钓上了 {fishing_record.frequency} 条鱼"
        return "🐟你还没有钓过鱼，快去钓鱼吧"


async def get_balance(user_id):
    balance = await get_user_balance(user_id)
    return f"🪙你有 {balance} {fishing_coin_name}"


async def get_backpack(user_id, limit=None):
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        fishes_record = await session.scalar(select_user)
        if fishes_record:
            loads_fishes = json.loads(fishes_record.fishes)
            loads_fishes = {
                key: loads_fishes[key] for key in fish_list if key in loads_fishes
            }
            spec_fishes = json.loads(fishes_record.special_fishes)
            if spec_fishes:
                spec_fishes = dict(sorted(spec_fishes.items()))
                if limit:
                    return print_backpack(loads_fishes, spec_fishes, limit)
                else:
                    return print_backpack(loads_fishes, spec_fishes)
            return (
                ["🎒你的背包里空无一物"]
                if loads_fishes == {}
                else print_backpack(loads_fishes)
            )
        return ["🎒你的背包里空无一物"]


def print_backpack(backpack, special_backpack=None, limit=None):
    i = 0
    result = []
    for fish_name, quantity in backpack.items():
        result.append(f"{i}. {fish_name}×{str(quantity)}")
        i += 1

    if special_backpack:
        i = 0
        special_result = []
        for fish_name, quantity in special_backpack.items():
            if limit:
                special_result.append(
                    f"{i}. {fish_name[:limit] + '...' + str(len(fish_name) - limit) if len(fish_name) > limit else fish_name}×{str(quantity)}"
                )
            else:
                special_result.append(f"{i}. {fish_name}×{str(quantity)}")
            i += 1
        return [
            "🎒普通鱼:\n" + "\n".join(result),
            "🎒特殊鱼:\n" + "\n".join(special_result),
        ]
    return ["🎒普通鱼:\n" + "\n".join(result)]


async def get_achievements(user_id):
    session = get_session()
    async with session.begin():
        select_user = select(FishingRecord).where(FishingRecord.user_id == user_id)
        record = await session.scalar(select_user)
        if record:
            achievements = json.loads(record.achievements)
            return "已完成成就:\n" + "\n".join(achievements)
        return "你甚至还没钓过鱼 (╬▔皿▔)╯"


async def get_board():
    if _value_available:
        from nonebot_plugin_value.api.api_balance import list_accounts
        currency_id = _get_fishing_currency_id()
        accounts = await list_accounts(currency_id)
        top_users_list = [(account.id, int(account.balance)) for account in accounts]
        top_users_list.sort(key=lambda user: user[1], reverse=True)
        return top_users_list[:10]
    else:
        session = get_session()
        async with session.begin():
            select_users = (
                select(FishingRecord).order_by(FishingRecord.coin.desc()).limit(10)
            )
            record = await session.scalars(select_users)
            if record:
                top_users_list = []
                for user in record:
                    top_users_list.append((user.user_id, user.coin))
                top_users_list.sort(key=lambda user: user[1], reverse=True)
                return top_users_list
            return []


def get_shop():
    messages = []

    messages.append(MessageSegment.text("===== 钓鱼用具店 ====="))

    for fish in config_fishes:
        if fish.can_buy:
            total_price = int(fish.buy_price * fish.amount)
            messages.append(
                MessageSegment.text(
                    f"商品名：{fish.name} \n单份数量：{fish.amount}\n单价：{fish.buy_price} {fishing_coin_name}\n"
                    f"单份总价：{total_price} {fishing_coin_name}\n描述：{fish.description}"
                )
            )

    return messages
