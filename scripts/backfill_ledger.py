# -*- coding: utf-8 -*-
"""主题台账历史回灌（news-impact-engine Phase 1）。

把库里未打标的历史新闻批量打 topic/方向/量级，然后打印各品种的台账总览——
让"哪些主题历史上动过价、最近反应趋势"立刻可见,不必等新数据攒。

跑法（生产服务器,数据在那里）：
  .venv/bin/python scripts/backfill_ledger.py [--limit N] [--symbols BTC/USDT,NQ=F]
本地库自 2026-05-17 起停更,只能跑通流程、看不到近期数据（见 memory: local-env）。
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_session
from models.news import NewsItem
from services import news_tagging, theme_ledger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000, help="本轮最多打标多少条未打标新闻")
    ap.add_argument("--symbols", default="BTC/USDT,NQ=F", help="逗号分隔，打印这些品种的台账总览")
    ap.add_argument("--no-tag", action="store_true", help="跳过打标，只打印总览")
    args = ap.parse_args()

    session = get_session()
    try:
        # 前置条件：先把 traditional_open 为 NULL 的历史新闻补齐（纯日历、无 LLM）。
        # 没有这个标，台账取数没法滤休市；打标之前必须先有它。
        filled = news_tagging.backfill_traditional_open(session)
        print(f"traditional_open 补齐：{filled} 条")

        if not args.no_tag:
            pending = (
                session.query(NewsItem)
                .filter(NewsItem.tagged_at.is_(None))
                .count()
            )
            print(f"未打标新闻：{pending} 条；本轮上限 {args.limit}")
            tagged = news_tagging.tag_untagged(session, limit=args.limit)
            print(f"打标完成（仅反应窗口已走完的）：{tagged} 条")

        for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            print(f"\n===== 台账总览 · {symbol} =====")
            overview = theme_ledger.ledger_overview(session, symbol, n=5)
            if not overview:
                print("  （无反应数据——可能价格快照缺这些时段，或新闻尚未打标）")
                continue
            for o in overview:
                line = "  ".join(
                    f"{r['time'].strftime('%m-%d')}[{r['magnitude']}]净{r['net_pct']:+.2f}%振{r['range_pct']:.2f}%"
                    for r in o["recent"]
                )
                print(f"  {o['topic']}（{o['count']} 次）: {line}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
