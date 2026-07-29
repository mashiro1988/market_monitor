# -*- coding: utf-8 -*-
"""行为日汇总回算：按北京日补写 date_basis='bj' 的 PIT 行（2026-07-29 口径切换配套）。

日聚合口径从 UTC 日改成北京日后，存档表里只剩旧的 date_basis='utc' 行，面板会全部走现算。
本脚本按北京日重算最近 N 个**已结束**的北京日并写入 bj 行，让面板立刻回到"已锁账"状态。
旧 utc 行只读不动。幂等：目标日已有 bj 行则跳过。

跑法（生产服务器,数据在那里）：
  .venv/bin/python scripts/backfill_behavior_bj_daily.py --days 14           # dry-run 先看
  .venv/bin/python scripts/backfill_behavior_bj_daily.py --days 14 --commit  # 落库
本地库自 2026-05-17 起停更,只能跑通流程、看不到近期数据（见 memory: local-env）。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_session
from models.behavior import BehaviorDailySummary
from services import behavior_classifier as bc
from services.time_utils import bj_date_of


def backfill(session, symbol: str, days: int, commit: bool,
             now: datetime | None = None) -> list[dict]:
    """回算最近 days 个已结束的北京日（不含今天）。commit=False 只试算不落库。"""
    now = now or datetime.utcnow()
    today_bj = datetime.strptime(bj_date_of(now), "%Y-%m-%d")
    results: list[dict] = []
    for offset in range(days, 0, -1):
        bj_date = (today_bj - timedelta(days=offset)).strftime("%Y-%m-%d")
        exists = (
            session.query(BehaviorDailySummary)
            .filter_by(symbol=symbol, bucket_date=bj_date, date_basis="bj")
            .first()
        )
        if exists is not None:
            results.append({"bj_date": bj_date, "action": "skip", "reason": "已有 bj 行"})
            continue
        counts, _composition, down_sum = bc.aggregate_day(session, symbol, bj_date)
        segments = sum(v.get("up", 0) + v.get("down", 0) for v in counts.values())
        if commit:
            bc.write_daily_summary(session, symbol, bj_date, now=now)
        results.append({"bj_date": bj_date, "action": "write" if commit else "dry-run",
                        "segments": segments, "down_net_sum": down_sum})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="回算最近 N 个已结束的北京日（不含今天）")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--commit", action="store_true", help="不加则只 dry-run 打印，不写库")
    args = ap.parse_args()

    session = get_session()
    try:
        results = backfill(session, args.symbol, args.days, args.commit)
        for r in results:
            if r["action"] == "skip":
                print(f"  {r['bj_date']}  跳过（{r['reason']}）")
            else:
                print(f"  {r['bj_date']}  段数 {r['segments']:>3}  "
                      f"跌净幅Σ {r['down_net_sum']:+.2f}%  [{r['action']}]")
        skipped = sum(1 for r in results if r["action"] == "skip")
        pending = len(results) - skipped
        if args.commit:
            print(f"\n完成：写入 {pending} 行，跳过 {skipped} 行。")
        else:
            print(f"\nDRY-RUN：将写入 {pending} 行，跳过 {skipped} 行。确认无误后加 --commit 落库。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
