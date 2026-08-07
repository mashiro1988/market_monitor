# -*- coding: utf-8 -*-
"""板块资金流勾稽门失败告警（2026-08-07 净资金流入 spec §5.2）。

资金流数据来自数据服务器的 BMAC 宽表补丁。补丁被 BMAC 升级覆盖、或宽表数据损坏时，
勾稽门会把该市场的资金流整轮作废（页面显示「—」）。页面上的「—」很安静，没人会注意到，
所以这里主动推一条企业微信 —— 判定与 scanner 同源，直接吃 compute 结果里的失败原因。

结构仿 services/price_source_monitoring.py：marker 去重 + 冷却 + AlertLog 落库，
且**发送失败不占冷却**（下一轮继续重试）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from database import SessionLocal
from models.alert_log import AlertLog

RULE_NAME = "sector_flow_gate"


def alert_flow_gate_failures(
    failures: dict[str, str],
    *,
    session=None,
    channel=None,
    now: datetime | None = None,
) -> list[dict]:
    """对每个勾稽失败的市场推一条告警（带冷却去重）；返回本次实际发出的条目。"""
    if not failures:
        return []

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    own_session = session is None
    session = session or SessionLocal()
    channel = channel or WeChatWorkChannel()
    sent: list[dict] = []
    try:
        for market in sorted(failures):
            reason = failures[market]
            marker = f"sector-flow:gate:{market}"
            if _recently_delivered(session, marker, now):
                continue
            title = f"板块资金流数据异常：{market}"
            content = (
                f"{market} 市场的资金流勾稽未通过，本轮该市场净流入已作废（页面显示「—」）。\n"
                f"原因：{reason}\n"
                f"板块涨跌不受影响。常见成因：数据服务器 BMAC 升级覆盖了宽表补丁，"
                f"或宽表本轮写入损坏。处理见 "
                f"docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md §10。"
            )
            delivered = channel.send(title, content)
            session.add(AlertLog(
                timestamp=now,
                rule_name=RULE_NAME,
                message=f"{marker}\n{content}"[:8000],
                channel=getattr(channel, "name", "wechat_work"),
                delivered=delivered,
            ))
            sent.append({"market": market, "reason": reason,
                         "marker": marker, "delivered": delivered})
        if own_session:
            session.commit()
        else:
            session.flush()
    except Exception:
        if own_session:
            session.rollback()
        logger.exception("sector flow gate alert failed")
        raise
    finally:
        if own_session:
            session.close()
    return sent


def _recently_delivered(session, marker: str, now: datetime) -> bool:
    """冷却窗内**成功送达过**同一 marker 才算数：发送失败的不占冷却，下一轮会重试。"""
    cooldown = max(1, int(getattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)))
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
