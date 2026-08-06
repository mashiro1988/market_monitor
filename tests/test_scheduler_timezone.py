# -*- coding: utf-8 -*-
"""定时任务时刻表：每条 CronTrigger 必须显式绑时区（2026-08-05 线上实证）。

显式构造的 CronTrigger 在**构造那一刻**就绑死系统本地时区（服务器 = Asia/Shanghai），
BackgroundScheduler(timezone="UTC") 对它完全无效。后果实测两例：
  · 行为日结写 hour=16、注释"UTC 16:05 = 北京 00:05"，实际每天北京 16:05 才跑（存档 computed_at 连续多日 08:05 UTC）；
  · 事件池日报写 hour=0、意图北京 08:10，实际北京 00:10 半夜发微信（日志 08-04/08-05 各一条）。
两处都"运行成功"，只有时刻不对——代码评审看不出来，只能靠线上时间戳发现。故用测试钉死。
"""
import re
from pathlib import Path

import pytest


def _fields(trigger) -> dict[str, str]:
    return {f.name: str(f) for f in trigger.fields}


@pytest.mark.parametrize(("job_id", "expected"), [
    ("behavior_daily_summary", {"hour": "0", "minute": "5"}),             # 北京 00:05 汇总刚结束的北京日
    ("research_daily_brief", {"hour": "8", "minute": "10"}),              # 北京 08:10 紧跟早间新闻推送
    ("data_retention", {"hour": "3", "minute": "17"}),                    # 北京 03:17（维持原有行为）
    ("cmc_refresh", {"day_of_week": "mon", "hour": "2", "minute": "17"}),  # 北京周一 02:17（维持原有行为）
])
def test_cron_jobs_bind_beijing_timezone_explicitly(job_id, expected):
    from api.app import _cron_trigger

    trigger = _cron_trigger(job_id)
    assert str(trigger.timezone) == "Asia/Shanghai"       # 绝不继承系统本地时区
    fields = _fields(trigger)
    for name, value in expected.items():
        assert fields[name] == value


def test_no_inline_cron_trigger_escapes_the_schedule_table():
    """新增定时任务必须进 CRON_SCHEDULES 时刻表，不许在 add_job 处就地构造裸 CronTrigger。

    就地构造 = 又一次静默绑上系统本地时区。要加新 job：往 CRON_SCHEDULES 里加一行，
    再用 _cron_trigger("你的 job_id")。
    """
    source = Path(__file__).resolve().parents[1].joinpath("api", "app.py").read_text(encoding="utf-8")
    constructions = re.findall(r"CronTrigger\(", source)
    assert len(constructions) == 1, (
        f"api/app.py 里有 {len(constructions)} 处构造 CronTrigger，只允许 _cron_trigger() 内那一处"
    )
