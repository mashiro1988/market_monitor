# -*- coding: utf-8 -*-
"""价格源健康告警（2026-07-27 P0）：源挂了主动推企业微信，不必等人打开页面。

背景：2026-07-22 Yahoo 封服务器 IP，yfinance 停产约 10 小时后才由人工察觉。市场概览的
freshness 徽标虽已上线，但要用户主动看。本模块把同一判定接到企业微信推送上。

判定口径与卡片**同源**——直接调 market_service.freshness_for，只对红标（source_down）
告警，黄标（stale）不推，避免噪音。仿 services/remote_monitoring.py 的 findings + 冷却
去重 + AlertLog 落库结构。

已知误报：本项目不建模交易所节假日（设计取舍），美股假日当天 yfinance 无数据会被判
source_down。推送正文带提示语，用户一眼可判。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import and_, func

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from database import SessionLocal
from models.alert_log import AlertLog
from models.price import PriceSnapshot
from scanners import market_sessions
from services import market_service

RULE_NAME = "price_source_monitor"


@dataclass
class PriceSourceFinding:
    kind: str
    title: str
    content: str
    marker: str


def check_price_source_health(
    *,
    session=None,
    channel=None,
    now: datetime | None = None,
    source_statuses: dict | None = None,
) -> list[dict]:
    """检查价格源健康并推送（带冷却去重）；返回本次实际发出的 findings。"""
    if not getattr(config, "PRICE_SOURCE_MONITORING_ENABLED", True):
        return []

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    own_session = session is None
    session = session or SessionLocal()
    channel = channel or WeChatWorkChannel()
    sent: list[dict] = []
    try:
        findings = collect_price_source_findings(session, now=now, source_statuses=source_statuses)
        for finding in findings:
            if _recently_delivered(session, finding.marker, now):
                continue
            message = f"{finding.marker}\n{finding.content}"
            delivered = channel.send(finding.title, finding.content)
            session.add(AlertLog(
                timestamp=now,
                rule_name=RULE_NAME,
                message=message[:8000],
                channel=getattr(channel, "name", "wechat_work"),
                delivered=delivered,
            ))
            sent.append({**asdict(finding), "delivered": delivered})
        if own_session:
            session.commit()
        else:
            session.flush()
    except Exception:
        if own_session:
            session.rollback()
        logger.exception("price source health alert failed")
        raise
    finally:
        if own_session:
            session.close()
    return sent


def collect_price_source_findings(
    session,
    *,
    now: datetime,
    source_statuses: dict | None = None,
) -> list[PriceSourceFinding]:
    findings: list[PriceSourceFinding] = []

    # ① 扫描器本轮直接抛错：高置信信号，单独一条
    for status in (source_statuses or {}).get("price", []):
        if status.get("ok", True):
            continue
        source = status.get("source", "unknown")
        findings.append(PriceSourceFinding(
            kind="price_scanner_error",
            title=f"价格源 {source} 采集异常",
            content=(f"{source} 本轮采集抛出异常：{status.get('error') or 'unknown'}\n"
                     f"（连续失败会自动指数退避；数据在源恢复后由游标窗口自动补齐）"),
            marker=f"price-monitor:scanner_error:{source}",
        ))

    # ② 开市却长时间没有新数据：与卡片红标同一判定
    failed_scanners = market_service.failed_price_scanner_names()
    down_by_source: dict[str, list[tuple[str, int | None]]] = {}
    for symbol, ts, snapshot_source in _latest_rows(session):
        if not market_sessions.is_open(symbol, now):
            continue
        freshness, lag = market_service.freshness_for(
            symbol, snapshot_source or "", ts, now, failed_scanners)
        if freshness == "source_down":
            down_by_source.setdefault(snapshot_source or "unknown", []).append((symbol, lag))

    for source, entries in sorted(down_by_source.items()):
        entries.sort(key=lambda e: -(e[1] or 0))
        detail = "、".join(f"{sym}(滞后{lag}分钟)" for sym, lag in entries)
        findings.append(PriceSourceFinding(
            kind="price_source_down",
            title=f"价格源中断：{source} {len(entries)} 个品种",
            content=(f"以下开市品种超过 {config.FRESHNESS_DOWN_MINUTES} 分钟没有新数据：\n{detail}\n"
                     f"卡片已显示红标「源中断」。若今日为该市场的交易所假日，属预期内可忽略。"),
            marker=f"price-monitor:source_down:{source}",
        ))

    return findings


def _latest_rows(session) -> list[tuple[str, datetime, str]]:
    """每个品种最新一行的 (symbol, timestamp, source)。

    走 (品种最大时刻) 子查询 + 自连接，命中 ix_price_snapshot_ts_symbol 索引；
    比市场概览那条「拉 10 天全量再取尾」的查询轻得多，适合每 5 分钟跑一次。"""
    sub = (
        session.query(
            PriceSnapshot.symbol.label("symbol"),
            func.max(PriceSnapshot.timestamp).label("ts"),
        )
        .group_by(PriceSnapshot.symbol)
        .subquery()
    )
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.timestamp, PriceSnapshot.source)
        .join(sub, and_(PriceSnapshot.symbol == sub.c.symbol,
                        PriceSnapshot.timestamp == sub.c.ts))
        .all()
    )
    return [(symbol, ts, source) for symbol, ts, source in rows]


def _recently_delivered(session, marker: str, now: datetime) -> bool:
    """冷却窗内**成功送达过**同一 marker 才算数：发送失败的不占用冷却，下一轮会重试。"""
    cooldown = max(1, int(getattr(config, "PRICE_SOURCE_ALERT_COOLDOWN_MINUTES", 60)))
    cutoff = now - timedelta(minutes=cooldown)
    rows = (
        session.query(AlertLog.message)
        .filter(
            AlertLog.rule_name == RULE_NAME,
            AlertLog.timestamp >= cutoff,
            AlertLog.delivered == True,   # noqa: E712 - SQLAlchemy 列比较
        )
        .all()
    )
    return any(marker in (message or "").splitlines() for (message,) in rows)
