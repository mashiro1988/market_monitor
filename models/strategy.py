# -*- coding: utf-8 -*-
"""持仓策略模块的 4 张表。

设计稿：docs/superpowers/specs/2026-08-28-position-strategy-design.md §3。
时间字段一律 UTC naive（项目约定）。
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text

from database import Base


class StrategyPosition(Base):
    """一行 = 一个批次（B1/B2…）。系统永不自动改 status，平仓只由用户在页面操作。"""
    __tablename__ = "strategy_positions"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(40), nullable=False)          # OKX instId，如 VIRTUAL-USDT-SWAP
    batch_label = Column(String(20), nullable=False)     # B1/B2…
    entry_at = Column(DateTime, nullable=False)          # UTC naive
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    forecast = Column(Integer, nullable=False, default=10)
    status = Column(String(10), nullable=False, default="open")   # open / closed
    closed_at = Column(DateTime, nullable=True)
    close_price = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_strategy_pos_symbol_status", "symbol", "status"),)


class StrategySettings(Base):
    """单行参数表（页面可改，改了下次计算生效）。默认值 = 用户 2026-08-28 拍板值。"""
    __tablename__ = "strategy_settings"

    id = Column(Integer, primary_key=True)
    capital = Column(Float, nullable=False, default=13915.0)
    risk_budget_pct = Column(Float, nullable=False, default=0.15)
    x_soft = Column(Integer, nullable=False, default=4)
    x_hard = Column(Integer, nullable=False, default=6)
    ewma_alpha = Column(Float, nullable=False, default=0.054)
    vol_update_threshold = Column(Float, nullable=False, default=0.25)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StrategySymbolState(Base):
    """按币种的路径依赖状态：在用波动率闩锁 + 重入场观察水位。"""
    __tablename__ = "strategy_symbol_state"

    symbol = Column(String(40), primary_key=True)
    v_used = Column(Float, nullable=True)
    v_used_at = Column(DateTime, nullable=True)
    reentry_level = Column(Float, nullable=True)         # 观察态水位；空 = 不在观察态
    reentry_breached_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StrategyEvent(Base):
    """动作提示流 + 状态转换去重依据。kind 见设计稿 §5。"""
    __tablename__ = "strategy_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    symbol = Column(String(40), nullable=False)
    position_id = Column(Integer, nullable=True)
    kind = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=True)
    pushed = Column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_strategy_events_symbol_created", "symbol", "created_at"),)
