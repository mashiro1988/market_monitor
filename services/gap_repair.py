# -*- coding: utf-8 -*-
"""价格快照缺口自愈：每小时扫近 N 小时缺口 → 定向回补 → 复扫确认 → 企业微信情况报告。

背景（2026-06-11，与用户实证定稿）：Yahoo 间歇限频 + 滚动回补只追平 10 分钟，
超过 10 分钟的洞在重启前永远不补——6/10 夜 NQ=F 缺的两根 bar 恰好把 -1.02% 的
慢跌窗口打成碎片。本 job 把"重启才修洞"变成"每小时自动修洞"。

分类按**回补结果**而非交易日历（天然适配 CME 日休 / 周末 / 亚洲时段）：
- 回补后缺口消失 → 补回；
- 源端本来就没有该时段 K 线 → 休市段，静默忽略；
- 拉取异常 → 本轮未确认，下轮重试但不推“仍缺”；
- 源端有数据但没补进库 → 真实仍缺，下轮重试并推送。

推送语义（用户要求）：本轮实际补回或确认仍缺才推完整账目；
完全无缺口、纯休市缺口、纯拉取失败的轮次静默。CNBC 债券无历史接口，不在自愈范围。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

import config
from database import get_session
from models.price import PriceSnapshot

# 相邻快照间隔超过此值视为缺口。5 分钟栅格上缺 1 根 bar = 恰好 10 分钟间隔，
# 阈值取 7.5 分钟：单根缺失也能捕获（慢跌窗口的擦线触发对单根缺失就很脆弱），同时容忍时间戳抖动。
GAP_MIN_INTERVAL_MINUTES = 7.5
# 距 now 太近的"尾部缺口"不处理（那是实时扫描的职责，且 bar 可能尚未收盘）
TAIL_FRESH_MINUTES = 15


def repair_symbols() -> dict[str, str]:
    """symbol -> asset_class：有历史 K 线可回补的品种（yfinance 全量 + OKX 加密）。"""
    from scanners.sources.yfinance_source import YFinancePriceSource

    out: dict[str, str] = {}
    for sym, (asset_class, _name) in YFinancePriceSource()._all_tickers().items():
        out[sym] = asset_class
    for base in config.PRICE_SOURCES.get("crypto", {}):
        out[f"{base}/USDT"] = "crypto"
    return out


def find_gaps(session, symbols: list[str], hours: int, now: datetime | None = None) -> dict[str, list[tuple[datetime, datetime]]]:
    """近 N 小时内各品种的内部缺口：相邻快照间隔 > GAP_MIN_INTERVAL_MINUTES。

    只报内部缺口（两端都有快照）；头部（回看起点前）与尾部（最近 TAIL_FRESH_MINUTES）不算。"""
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=max(1, hours))
    tail = now - timedelta(minutes=TAIL_FRESH_MINUTES)

    gaps: dict[str, list[tuple[datetime, datetime]]] = {}
    for symbol in symbols:
        rows = (
            session.query(PriceSnapshot.timestamp)
            .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp >= cutoff)
            .order_by(PriceSnapshot.timestamp.asc())
            .all()
        )
        ts = [r.timestamp for r in rows if r.timestamp <= tail]
        spans = [
            (prev, cur)
            for prev, cur in zip(ts, ts[1:])
            if (cur - prev).total_seconds() > GAP_MIN_INTERVAL_MINUTES * 60
        ]
        if spans:
            gaps[symbol] = spans
    return gaps


def _missing_bars(span: tuple[datetime, datetime]) -> int:
    """缺口里缺的 5m bar 数（不含两端）。"""
    return max(0, int((span[1] - span[0]).total_seconds() // 300) - 1)


def run_gap_repair(session=None, hours: int | None = None, now: datetime | None = None,
                   scanner=None, channel=None) -> dict:
    """一轮自愈：扫缺口 → 一次批量回补 → 复扫 → 按结果分类 → 推送账目。

    session/scanner/channel 可注入（测试用）；生产由调度器无参调用。"""
    own_session = session is None
    if own_session:
        session = get_session()
    try:
        hours = int(hours or getattr(config, "GAP_REPAIR_LOOKBACK_HOURS", 24))
        symbols = repair_symbols()
        before = find_gaps(session, list(symbols), hours, now=now)
        summary = {
            "gaps_found": sum(len(v) for v in before.values()),
            "bars_missing": sum(_missing_bars(s) for v in before.values() for s in v),
            "bars_repaired": 0, "still_missing": [], "closed_ignored": 0, "fetch_error": None,
        }
        if not before:
            logger.info("[GapRepair] 无缺口")
            return summary

        span_start = min(s[0] for v in before.values() for s in v)
        span_end = max(s[1] for v in before.values() for s in v)
        logger.info(f"[GapRepair] 发现 {summary['gaps_found']} 个缺口（缺 {summary['bars_missing']} 根），"
                    f"回补 {span_start} ~ {span_end} UTC")

        fetched_records: list = []
        fetch_failed = True
        try:
            if scanner is None:
                from scanners.price_scanner import PriceScanner
                scanner = PriceScanner()
            fetched_records = scanner.backfill_range(span_start, span_end)
            fetch_failed = False
        except Exception as exc:  # 限频/网络等：本轮失败，下轮重试
            summary["fetch_error"] = str(exc)[:200]
            logger.error(f"[GapRepair] 回补拉取失败: {exc}")

        if fetch_failed and summary["fetch_error"] is None:
            summary["fetch_error"] = "fetch failed"

        session.expire_all()
        after = find_gaps(session, list(symbols), hours, now=now)
        summary["bars_repaired"] = summary["bars_missing"] - sum(
            _missing_bars(s) for v in after.values() for s in v
        )

        # 复扫仍在的缺口：按源端是否有数据分类（有→真实仍缺；无→休市段，静默）
        if summary["fetch_error"] is not None:
            logger.warning("[GapRepair] fetch failed; remaining gaps are unconfirmed and will be retried")
            _push_report(summary, channel)
            return summary

        fetched_ts: dict[str, set] = {}
        for rec in fetched_records:
            if rec.timestamp is not None:
                fetched_ts.setdefault(rec.symbol, set()).add(rec.timestamp)
        for symbol, spans in after.items():
            for span in spans:
                source_has = any(span[0] < t < span[1] for t in fetched_ts.get(symbol, ()))
                if summary["fetch_error"] is not None or source_has:
                    summary["still_missing"].append({
                        "symbol": symbol,
                        "span": (span[0].isoformat(), span[1].isoformat()),
                        "bars": _missing_bars(span),
                        "reason": "拉取失败（可能限频），下轮重试" if summary["fetch_error"] else "源端有数据但未补全",
                    })
                else:
                    summary["closed_ignored"] += 1

        _push_report(summary, channel)
        return summary
    finally:
        if own_session:
            session.close()


def _push_report(summary: dict, channel=None) -> None:
    """有缺口活动（补回 / 真实仍缺）才推送完整账目；纯休市缺口静默。"""
    if summary["bars_repaired"] <= 0 and not summary["still_missing"]:
        return
    if channel is None:
        from alerts.channels.wechat_work import WeChatWorkChannel
        channel = WeChatWorkChannel()

    lines = [f"发现缺口 **{summary['gaps_found']}** 处（缺 {summary['bars_missing']} 根 5m bar）",
             f"补回 **{summary['bars_repaired']}** 根"]
    if summary["still_missing"]:
        lines.append(f"<font color=\"warning\">仍缺 {sum(m['bars'] for m in summary['still_missing'])} 根：</font>")
        for m in summary["still_missing"][:8]:
            lines.append(f"> {m['symbol']} {m['span'][0][5:16]} ~ {m['span'][1][11:16]} UTC（{m['reason']}）")
    else:
        lines.append("<font color=\"info\">已全部补全</font>")
    if summary["closed_ignored"]:
        lines.append(f"<font color=\"comment\">另有 {summary['closed_ignored']} 处为休市段，忽略</font>")
    try:
        channel.send("数据自愈", "\n".join(lines))
    except Exception as exc:  # 推送失败不影响修复本身
        logger.error(f"[GapRepair] 推送失败: {exc}")
