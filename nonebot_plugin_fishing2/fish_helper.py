from typing import Literal, Optional, Union
from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw, ImageFont

from .config import config


# Unicode Emoji_Presentation（Unicode 15.0.0）压缩区间：
# 只有这些字符默认按 emoji 渲染（已核对 NotoEmoji 对其覆盖零缺口）；
# 其余如 ♡ ♠ ★ 等文本符号交给文本/符号字体，避免路由到 emoji 字体后渲染成方块
_EMOJI_PRESENTATION_RANGES = (
    (0x231A, 0x231B),
    (0x23E9, 0x23EC),
    (0x23F0, 0x23F0),
    (0x23F3, 0x23F3),
    (0x25FD, 0x25FE),
    (0x2614, 0x2615),
    (0x2648, 0x2653),
    (0x267F, 0x267F),
    (0x2693, 0x2693),
    (0x26A1, 0x26A1),
    (0x26AA, 0x26AB),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26CE, 0x26CE),
    (0x26D4, 0x26D4),
    (0x26EA, 0x26EA),
    (0x26F2, 0x26F3),
    (0x26F5, 0x26F5),
    (0x26FA, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2705, 0x2705),
    (0x270A, 0x270B),
    (0x2728, 0x2728),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2795, 0x2797),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x1F004, 0x1F004),
    (0x1F0CF, 0x1F0CF),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F1E6, 0x1F1FF),
    (0x1F201, 0x1F201),
    (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F),
    (0x1F232, 0x1F236),
    (0x1F238, 0x1F23A),
    (0x1F250, 0x1F251),
    (0x1F300, 0x1F320),
    (0x1F32D, 0x1F335),
    (0x1F337, 0x1F37C),
    (0x1F37E, 0x1F393),
    (0x1F3A0, 0x1F3CA),
    (0x1F3CF, 0x1F3D3),
    (0x1F3E0, 0x1F3F0),
    (0x1F3F4, 0x1F3F4),
    (0x1F3F8, 0x1F43E),
    (0x1F440, 0x1F440),
    (0x1F442, 0x1F4FC),
    (0x1F4FF, 0x1F53D),
    (0x1F54B, 0x1F54E),
    (0x1F550, 0x1F567),
    (0x1F57A, 0x1F57A),
    (0x1F595, 0x1F596),
    (0x1F5A4, 0x1F5A4),
    (0x1F5FB, 0x1F64F),
    (0x1F680, 0x1F6C5),
    (0x1F6CC, 0x1F6CC),
    (0x1F6D0, 0x1F6D2),
    (0x1F6D5, 0x1F6D7),
    (0x1F6DC, 0x1F6DF),
    (0x1F6EB, 0x1F6EC),
    (0x1F6F4, 0x1F6FC),
    (0x1F7E0, 0x1F7EB),
    (0x1F7F0, 0x1F7F0),
    (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945),
    (0x1F947, 0x1F9FF),
    (0x1FA70, 0x1FA7C),
    (0x1FA80, 0x1FA88),
    (0x1FA90, 0x1FABD),
    (0x1FABF, 0x1FAC5),
    (0x1FACE, 0x1FADB),
    (0x1FAE0, 0x1FAE8),
    (0x1FAF0, 0x1FAF8),
)


def _is_emoji(character: str) -> bool:
    codepoint = ord(character)
    if codepoint in (0xFE0E, 0xFE0F):  # 变体选择符（NotoEmoji 内含零宽字形）
        return True
    for start, end in _EMOJI_PRESENTATION_RANGES:
        if start <= codepoint <= end:
            return True
    return False


def _is_symbol(character: str) -> bool:
    """是否属于 Noto Sans Symbols 覆盖的符号（非 emoji）。"""
    codepoint = ord(character)
    if _is_emoji(character):
        return False
    return (
        # 带圈/括号数字字母（①②③ Ⓐ ⓐ ⑴ ⒈ …）
        0x2460 <= codepoint <= 0x24FF
        # 罗马数字（Ⅰ ⅱ …）
        or 0x2160 <= codepoint <= 0x2188
        # 基本箭头（← ↑ → ↓ ↔ ↕ ↖ ↗ ↘ ↙）
        or 0x2190 <= codepoint <= 0x2199
        # 负号 −
        or codepoint == 0x2212
    )


# 插件字体目录（nonebot_plugin_fishing2/source/）
_FONT_DIR = Path(__file__).resolve().parent / "source"

# 中文字体候选（Noto Sans CJK SC，字重 400）
_CJK_FONT_CANDIDATES = [
    _FONT_DIR / "NotoSansCJKsc-VF.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto-cjk-otf/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

# Emoji 字体候选（Noto Emoji）
_EMOJI_FONT_CANDIDATES = [
    _FONT_DIR / "NotoEmoji.ttf",
    "/usr/share/fonts/noto/NotoEmoji-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoEmoji-Regular.ttf",
]

# 符号字体候选（Noto Sans Symbols）
_SYMBOL_FONT_CANDIDATES = [
    _FONT_DIR / "NotoSansSymbols.ttf",
    "/usr/share/fonts/noto/NotoSansSymbols-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansSymbols-Regular.ttf",
]


def _set_weight_400(font: "ImageFont.FreeTypeFont") -> None:
    """将可变字体字重设置为 400（Regular）。"""
    try:
        font.set_variation_by_name("Regular")
        return
    except Exception:
        pass
    try:
        font.set_variation_by_axes([400])
    except Exception:
        pass


def _load_cjk_font(size: int):
    """加载中文字体（字重 400）。"""
    for path in _CJK_FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(str(path), size)
            _set_weight_400(font)
            return font
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _load_emoji_font(size: int):
    """加载 Emoji 字体。"""
    for path in _EMOJI_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return None


def _load_symbol_font(size: int):
    """加载符号字体（Noto Sans Symbols）。"""
    for path in _SYMBOL_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return None


def _char_kind(character: str) -> int:
    """返回字符类型：0=普通文本，1=符号，2=emoji。"""
    if _is_emoji(character):
        return 2
    if _is_symbol(character):
        return 1
    return 0


def _split_runs(text: str):
    """将文本按字符类型切成 (kind, segment) 序列，用于多字体渲染。"""
    runs = []
    buf = ""
    cur_kind = None
    for ch in text:
        kind = _char_kind(ch)
        if kind == cur_kind:
            buf += ch
        else:
            if buf:
                runs.append((cur_kind, buf))
            buf = ch
            cur_kind = kind
    if buf:
        runs.append((cur_kind, buf))
    return runs


def text_to_image(text: str) -> bytes:
    """将文本渲染为图片（PNG 字节），与银趴插件保持一致的展示方式。"""
    fontSize = 20
    max_line_len = 100
    max_lines = 200
    lines = [line[:max_line_len] for line in text.split("\n")][:max_lines]
    font = _load_cjk_font(fontSize)
    emoji_font = _load_emoji_font(fontSize)
    symbol_font = _load_symbol_font(fontSize)
    # 没有可用的 emoji 字体时，去掉 emoji，避免 CJK 字体下渲染成方块
    if emoji_font is None:
        lines = ["".join(ch for ch in line if not _is_emoji(ch)).rstrip() for line in lines]

    def _get_font(kind: int):
        if kind == 2:  # emoji
            return emoji_font
        if kind == 1:  # 符号
            return symbol_font or font
        return font

    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    line_widths = []
    for line in lines:
        if font is None and emoji_font is None and symbol_font is None:
            line_widths.append(len(line) * fontSize)
            continue
        width = 0
        for kind, seg in _split_runs(line):
            f = _get_font(kind)
            width += tmp.textlength(seg, font=f) if f else len(seg) * fontSize
        line_widths.append(width)
    width = int(max(line_widths, default=0)) + 24
    width = max(width, 1)
    height = len(lines) * (fontSize + 6) + 12
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    # 不同字体（CJK/符号/emoji）垂直度量差异很大，统一用基线锚点 "ls" 对齐，
    # 基线偏移取主字体的 ascent，使整行文本落在原来的纵向区间内
    ascent = font.getmetrics()[0] if font else fontSize
    y = 6
    for line in lines:
        x = 12
        for kind, seg in _split_runs(line):
            f = _get_font(kind)
            if f is not None:
                draw.text((x, y + ascent), seg, font=f, fill=(0, 0, 0), anchor="ls")
            x += tmp.textlength(seg, font=f) if f else len(seg) * fontSize
        y += fontSize + 6
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


class Achievement:
    type: Literal["fishing_frequency", "fish_type"] = None
    name: str = None
    data: Union[str, int] = (None,)
    description: int = None

    def __init__(self, achievemrnt: dict):
        self.type = achievemrnt["type"]
        self.name = achievemrnt["name"]
        self.data = achievemrnt["data"]
        self.description = achievemrnt["description"]


class Property:
    type: Literal[
        "rare_fish", "normal_fish", "fish", "rm_fish", "special_fish", "no_fish"
    ] = None
    key: Optional[str] = None
    value: Optional[int] = None

    def __init__(self, property: dict):
        self.type = property["type"]
        self.key = property["key"] if property.get("key") else None
        self.value = property["value"] if property.get("value") else None

    # fmt:off
    def __str__(self) -> str:
        match self.type:
            case "normal_fish":
                result = f"普通鱼权重{'增加' if self.value > 0 else '减少'}{abs(self.value)}"
            case "rare_fish":
                result = f"稀有鱼权重{'增加' if self.value > 0 else '减少'}{abs(self.value)}"
            case "fish":
                result = f"{self.key}权重{'增加' if self.value > 0 else '减少'}{abs(self.value)}"
            case "rm_fish":
                result = f"不会钓到{self.key}\n"
            case "special_fish":
                result = f"特殊鱼概率{'增加' if self.value > 0 else '减少'}{abs(self.value)}"
            case "no_fish":
                result = f"空军概率{'增加' if self.value > 0 else '减少'}{abs(self.value)}"
            case _:
                pass
        return result
    # fmt:on


class Fish:
    type: Literal["fish", "item"] = "fish"
    name: str = ""
    price: int = 0
    props: list[Property] = []
    description: str = ""
    can_catch: bool = False
    sleep_time: Optional[int] = 0
    weight: Optional[int] = 0
    can_buy: bool = False
    buy_price: Optional[int] = 0
    amount: Optional[int] = 0
    can_sell: bool = False
    only_with: Optional[str] = None
    catch_probability: Optional[float] = None
    daily_limit: Optional[str] = None
    rare: bool = False
    aliases: list[str] = []

    def __init__(self, fish_dict: dict):
        self.type = fish_dict["type"] if fish_dict.get("type") else "fish"
        self.name = fish_dict["name"]  # 鱼名字都不填，搁着虚空造鱼呢？
        self.price = fish_dict["price"] if fish_dict.get("price") else 15
        self.amount = fish_dict["amount"] if fish_dict.get("amount") else 1
        self.description = (
            fish_dict["description"]
            if fish_dict.get("description")
            else "没有人知道这条鱼的信息。"
        )
        self.can_catch = fish_dict["can_catch"]
        self.can_buy = fish_dict["can_buy"]
        self.can_sell = fish_dict["can_sell"]
        self.only_with = fish_dict.get("only_with") or None
        self.catch_probability = (
            fish_dict["catch_probability"]
            if fish_dict.get("catch_probability") is not None
            else None
        )
        self.daily_limit = fish_dict.get("daily_limit") or None
        self.aliases = fish_dict.get("aliases") or []

        self.sleep_time = fish_dict["sleep_time"] if fish_dict.get("sleep_time") else 60
        self.weight = fish_dict["weight"] if fish_dict.get("weight") else 0

        if fish_dict.get("rare") is not None:
            self.rare = bool(fish_dict["rare"])
        else:
            # 未显式标注时沿用权重分界线判断
            self.rare = self.can_catch and self.weight < config.rare_fish_weight

        self.buy_price = (
            fish_dict["buy_price"]
            if fish_dict.get("buy_price")
            else int(fish_dict["price"] * config.buy_rate)
        )

        self.props = []
        if fish_dict.get("props") and fish_dict["props"] != []:
            for property in fish_dict["props"]:
                self.props.append(Property(property))

    def print_info(self) -> list[MessageSegment]:
        message = []

        message1 = ""
        message1 += f"▶ 名称：{self.name}\n"
        message1 += f"▶ 基准价格：{self.price} {fishing_coin_name}\n"
        message1 += f"▶ 单份数量：{self.amount}\n"
        message1 += f"▶ 描述：{self.description}\n"
        message1 += f'▶ {"可钓鱼获取" if self.can_catch else "不可钓鱼获取"}，'
        message1 += f'{"可购买" if self.can_buy else "不可购买"}，'
        message1 += f'{"可出售" if self.can_sell else "不可出售"}'
        message.append(MessageSegment.text(message1))
        if self.can_catch:
            message2 = ""
            message2 += f"▶ 钓鱼信息：\n"
            if self.catch_probability is not None:
                message2 += f"  ▷ 独立概率：{self.catch_probability * 100:g}%"
                if self.only_with:
                    message2 += f"（仅限{self.only_with}）"
                if self.daily_limit == "utc":
                    message2 += "（全服每个 UTC 日限 1 条）"
                message2 += f"，上钩时间：{self.sleep_time}s"
            else:
                message2 += f"  ▷ 基础权重：{self.weight}，"
                message2 += f"上钩时间：{self.sleep_time}s"
            message.append(MessageSegment.text(message2))
        if self.can_buy:
            message3 = ""
            message3 += f"▶ 商店信息：\n"
            message3 += f"  ▷ 购买价格：{self.buy_price}\n"
            message.append(MessageSegment.text(message3))
        if self.props != []:
            message4 = ""
            message4 += f"▶ 道具信息：\n"
            message4 += f'  ▷ 道具类型：{"鱼饵" if self.type == "fish" else "道具"}\n'
            message4 += self.print_props()
            message.append(MessageSegment.text(message4))
        return message

    def print_props(self) -> str:
        result = "  ▷ 道具效果：\n"
        for i in range(len(self.props)):
            prop = self.props[i]
            result += (
                f"    {i + 1}. {str(prop)}"
                if i == len(self.props) - 1
                else f"    {i + 1}. {str(prop)}\n"
            )
        return result


# Constants
fishing_coin_name = config.fishing_coin_name
config_fishes: list[Fish] = [Fish(fish_dict) for fish_dict in config.fishes]
config_achievements = [
    Achievement(achievement_dict) for achievement_dict in config.fishing_achievement
]

fish_list: list[str] = [fish.name for fish in config_fishes]
# 独立概率鱼（含永恒鱼竿 only 鱼）不进入普通权重池，由 get_fish 单独判定
event_fishes: list[Fish] = [
    fish
    for fish in config_fishes
    if fish.can_catch and fish.catch_probability is not None
]
can_catch_fishes = {
    fish.name: fish.weight
    for fish in config_fishes
    if fish.can_catch and fish.catch_probability is None
}
can_buy_fishes = [fish.name for fish in config_fishes if fish.can_buy]
can_sell_fishes = [fish.name for fish in config_fishes if fish.can_sell]


def get_fish_by_name(fish_name: str) -> Fish | None:
    for fish in config_fishes:
        if fish.name == fish_name or fish_name in fish.aliases:
            return fish

    return None
