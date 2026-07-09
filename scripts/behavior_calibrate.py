# -*- coding: utf-8 -*-
"""行为引擎校准 CLI（price-behavior-engine-plan Task 9）。

用法（服务器 venv / 本地 anaconda）：
    python scripts/behavior_calibrate.py --days 30
    python scripts/behavior_calibrate.py --days 30 --mode anchor

产出 markdown 报告到 docs/reports/behavior-calibration-YYYYMMDD.md 并打印路径。
运行节律：上线前一次（补齐 CL/N225/US_2Y 三档 → 用户拍板进 config）；此后每季度 + regime 事件后。
**脚本只建议、不改 config。**
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from database import SessionLocal  # noqa: E402
from services import behavior_calibration as cal  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="行为引擎 T_ref 校准（四件套）")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--mode", default="all",
                        choices=["all", "anchor", "sensitivity", "null-lift", "session-bias"])
    parser.add_argument("--out", default=None, help="报告输出路径（默认 docs/reports/）")
    args = parser.parse_args()

    now = datetime.utcnow()
    session = SessionLocal()
    try:
        anchor = cal.anchor_table(session, args.symbol, args.days, now) \
            if args.mode in ("all", "anchor") else []
        null_lift = cal.null_lift_table(session, args.symbol, args.days, now) \
            if args.mode in ("all", "null-lift") else []
        sensitivity: dict[str, list[dict]] = {}
        if args.mode in ("all", "sensitivity"):
            for ref in config.BEHAVIOR_REF_SYMBOLS:
                rows = cal.sensitivity_table(session, ref, args.symbol, args.days, now)
                if rows:
                    sensitivity[ref] = rows
        session_bias = cal.session_bias_table(session, args.symbol, args.days, now) \
            if args.mode in ("all", "session-bias") else []
    finally:
        session.close()

    report = cal.render_report(anchor, null_lift, sensitivity, session_bias, args.days, now)
    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent / "docs" / "reports"
        / f"behavior-calibration-{now.strftime('%Y%m%d')}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
