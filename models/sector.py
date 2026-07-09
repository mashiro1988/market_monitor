"""板块（sector）相关模型。

两张表：
- cmc_symbol_categories  — symbol → CMC category 的本地映射缓存（7 天 TTL，从 CMC API 刷新）
- sector_returns         — 每个板块在每个 snapshot_at 时的均值/中位数涨跌（多周期）

时间字段都是 UTC naive，跟 market_monitor 现有约定一致。
详细背景见 docs/specs/remote_data_integration.md §4。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, UniqueConstraint

from database import Base


class CmcSymbolCategory(Base):
    """Symbol → CMC 板块 的多对多映射的本地缓存。

    刷新策略：
    - 启动时若任一白名单板块缺失或过期，触发一次刷新
    - Phase 2 起，APScheduler 每周一凌晨自动刷新
    - 手动：`python run.py refresh-sectors`

    一个 symbol 可能落在多个板块（e.g., ETHUSDT ∈ {Layer 1, Smart Contracts, Ethereum Ecosystem}），
    所以唯一约束在 (symbol, category) 复合键上。
    """
    __tablename__ = "cmc_symbol_categories"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(40), nullable=False)
    category = Column(String(120), nullable=False)
    # CMC 给的额外元数据（可选）。category_id 用于幂等刷新（CMC 改名时按 id 而不是 name）。
    category_id = Column(String(40), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "category", name="uq_cmc_symbol_category"),
        Index("ix_cmc_symbol", "symbol"),
        Index("ix_cmc_category", "category"),
    )


class SectorReturn(Base):
    """每个板块在某个 snapshot_at 时刻的等权聚合涨跌。

    snapshot_at 是 sector_scanner 取最新 BMAC pivot 的 candle_begin_time（UTC naive）。
    一次扫描会写入 N 行（N = 当前命中白名单且有 ≥1 个匹配 symbol 的 CMC category 数）。

    token_count 是参与计算的活跃 symbol 数。
    ret_*h 是该板块下 symbol 的 1h / 24h / 168h / 720h 等权平均涨跌（百分比）。
    ret_*h_median 是同一批 symbol 的中位数涨跌，用来衡量板块广度。
    """
    __tablename__ = "sector_returns"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_at = Column(DateTime, nullable=False)
    category = Column(String(120), nullable=False)
    group_name = Column(String(60), nullable=True)  # 中文大组名，from config.SECTOR_WHITELIST
    token_count = Column(Integer, nullable=False)
    ret_1h = Column(Float, nullable=True)
    ret_24h = Column(Float, nullable=True)
    ret_168h = Column(Float, nullable=True)
    ret_720h = Column(Float, nullable=True)
    ret_1h_median = Column(Float, nullable=True)
    ret_24h_median = Column(Float, nullable=True)
    ret_168h_median = Column(Float, nullable=True)
    ret_720h_median = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("snapshot_at", "category", name="uq_sector_snapshot_category"),
        Index("ix_sector_snapshot", "snapshot_at"),
        Index("ix_sector_category_snapshot", "category", "snapshot_at"),
    )
