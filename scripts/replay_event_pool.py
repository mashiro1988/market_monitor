# -*- coding: utf-8 -*-
"""BOJ 沙盒回放(docs/specs/news-research-phase1-event-pool.md §14):验收挂接质量与时间轴呈现。

用法(沙盒库 = 线上快照副本,拉取流程见 VACUUM INTO + scp 惯例):
  D:/anaconda/python.exe scripts/replay_event_pool.py ^
      --db data/replay-sandbox.db --seed-news-id 12345 ^
      --name "日本央行加息预期提前" --keywords "日本央行、日银、BOJ、植田" ^
      --days 55 --report replay-boj-report.md

生产库零接触:必须显式传 --db,脚本拒绝生产库文件名。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="沙盒 SQLite 文件(线上快照副本)")
    ap.add_argument("--seed-news-id", type=int, required=True, help="人工指定的首条信号新闻 id")
    ap.add_argument("--name", required=True)
    ap.add_argument("--keywords", default=None)
    ap.add_argument("--days", type=float, default=55.0, help="回扫深度(天)")
    ap.add_argument("--report", default="replay-report.md")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    if db_path.name == "market_monitor.db":
        raise SystemExit("拒绝:这是生产/开发库文件名,回放只允许沙盒副本(spec §14)")
    if not db_path.exists():
        raise SystemExit(f"沙盒库不存在: {db_path}")
    # 必须在 import database 之前设置(engine 在模块导入时绑定 DATABASE_URL)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    from database import SessionLocal, create_tables
    from services import event_linking, event_pool

    create_tables(seed_defaults=False)
    session = SessionLocal()
    try:
        event = event_pool.create_event(
            session, args.name, news_ids=[args.seed_news_id],
            gate_keywords=args.keywords, created_from="manual",
            backscan_hours=args.days * 24)
        print(f"立案 #{event.id} {event.name};回扫 {args.days} 天,开始分批挂接...")
        rounds = 0
        while True:
            stats = event_linking.link_unprocessed(session, limit=200)
            rounds += 1
            print(f"  round {rounds}: 盖章 {stats['processed']}, 新挂 {stats['linked']}, LLM {stats['called']} 条")
            if stats["processed"] == 0 and stats["called"] == 0:
                break
            if rounds >= 500:      # LLM 持续报错时失败批不盖游标会无限重试,兜个底
                print("  达到 500 轮上限,提前停止(检查 API/网络后可重跑续接——游标幂等)")
                break
        tl = event_pool.event_timeline(session, event.id)
        lines = [f"# 回放报告:{event.name}", "",
                 f"- 证据 {len(tl['items'])} 条;seed news #{args.seed_news_id};关键词:{args.keywords or '(无)'}", "",
                 "| 时间(BJ) | 来源 | 分 | 方向 | 观测 | 徽章 | 挂接 | 标题 |",
                 "|---|---|---|---|---|---|---|---|"]
        for it in tl["items"]:
            obs = it["obs"]
            obs_txt = ("计算中" if obs["status"] == "pending"
                       else "—" if obs["status"] != "ok"
                       else f"{obs['actual_minutes']}min {obs['net_pct']:+.2f}%")
            badge = (f"driver {it['driver_badge']['change_pct']:+.2f}%"
                     if it["driver_badge"] and it["driver_badge"].get("change_pct") is not None
                     else "driver" if it["driver_badge"] else "")
            miss = f"评分失手{it['news']['llm_importance']}" if it["score_miss"] else ""
            src = it["link"]["link_source"] + (f"({it['link']['confidence']})" if it["link"]["confidence"] else "")
            lines.append(f"| {it['news']['timestamp_bj']} | {it['news']['source']} | "
                         f"{it['news']['llm_importance']} | {it['news']['news_direction'] or ''} | "
                         f"{obs_txt} | {badge or miss} | {src} | {(it['news']['title'] or '')[:60]} |")
        Path(args.report).write_text("\n".join(lines), encoding="utf-8")
        print(f"报告已写入 {args.report};请人工盘点漏挂/误挂(spec §14 通过标准)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
