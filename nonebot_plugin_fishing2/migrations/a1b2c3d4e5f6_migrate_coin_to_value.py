"""migrate coin to nonebot-plugin-value

迁移 ID: a1b2c3d4e5f6
父迁移: c5ab992c9af3
创建时间: 2026-08-27 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: str | Sequence[str] | None = 'c5ab992c9af3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(name: str = "") -> None:
    if name:
        return
    # coin 列保留不删除，用于向后兼容
    # 旧数据在插件启动时由 data_source.init_currency() 迁移到 nonebot-plugin-value
    pass


def downgrade(name: str = "") -> None:
    if name:
        return
    pass
