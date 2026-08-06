# -*- coding: utf-8 -*-
"""一次性新闻评分回补:把 llm_importance 为空的存量新闻补上分(在服务器上跑)。

用法(手册见 .claude/commands/backfill-yf.md "新闻评分回补"节;先备份库再跑):
  .venv/bin/python scripts/rescore_news.py --days 14             # dry-run:只看分布与预计调用数
  .venv/bin/python scripts/rescore_news.py --days 14 --execute   # 真跑(幂等,只补空分,可重复运行)

设计:docs/specs/2026-08-06-news-rescore-and-source-cut-design.md §1.4。
复用 services/news_rescore.rescore_unscored 循环消化;尝试上限同线上补扫共用,毒条目自动退休。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone


def main() -> int:
    ap = argparse.ArgumentParser(description="新闻评分一次性回补(默认 dry-run)")
    ap.add_argument("--days", type=int, default=14, help="回补窗口天数(默认 14)")
    ap.add_argument("--execute", action="store_true", help="真跑;不带则只打印分布")
    ap.add_argument("--limit-per-round", type=int, default=48, help="每轮补扫条数(默认 48 = 4 批)")
    ap.add_argument("--max-rounds", type=int, default=300, help="安全阀:最多循环轮数")
    args = ap.parse_args()

    import config
    from sqlalchemy import func, text
    from database import get_session
    from models.news import NewsItem

    session = get_session()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=args.days)
    base = (session.query(NewsItem)
            .filter(NewsItem.llm_importance.is_(None), NewsItem.created_at >= cutoff))
    total = base.count()
    eligible = base.filter(
        func.coalesce(NewsItem.rescore_attempts, 0) < config.NEWS_RESCORE_MAX_ATTEMPTS
    ).count()
    batch = max(1, int(getattr(config, "DEEPSEEK_BATCH_SIZE", 12)))
    print(f"窗口: 近 {args.days} 天 (created_at >= {cutoff:%Y-%m-%d %H:%M} UTC)")
    print(f"未评分: {total} 条;可补(尝试<{config.NEWS_RESCORE_MAX_ATTEMPTS}): {eligible} 条;"
          f"预计约 {-(-eligible // batch)} 次调用({config.DEEPSEEK_MODEL})")
    print("按日×来源分布(北京日):")
    rows = session.execute(text(
        "SELECT date(datetime(timestamp,'+8 hours')) d, source, COUNT(*) "
        "FROM news_items WHERE llm_importance IS NULL AND created_at >= :cutoff "
        "GROUP BY d, source ORDER BY d, source"), {"cutoff": cutoff}).fetchall()
    for d, source, n in rows:
        print(f"  {d}  {source:<16} {n}")

    if not args.execute:
        print("\ndry-run 结束(加 --execute 真跑;跑前先 VACUUM INTO 备份)")
        return 0

    from services.news_rescore import rescore_unscored
    done = scored = 0
    for rnd in range(1, args.max_rounds + 1):
        stats = rescore_unscored(session, limit=args.limit_per_round,
                                 window_hours=args.days * 24)
        if not stats["selected"]:
            break
        done += stats["selected"]
        scored += stats["scored"]
        print(f"第 {rnd} 轮: 扫 {stats['selected']} 补上 {stats['scored']} "
              f"(累计 {scored}/{done})", flush=True)
        time.sleep(1)

    remaining = (session.query(NewsItem)
                 .filter(NewsItem.llm_importance.is_(None), NewsItem.created_at >= cutoff)
                 .count())
    print(f"\n完成: 尝试 {done} 条,补上 {scored} 条;窗口内仍未评分 {remaining} 条"
          f"(含达尝试上限的毒条目)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
