# -*- coding: utf-8 -*-
"""一次性冒烟测试：用 2026-06-09 晚生产环境窗口 #26 的真实候选新闻重放新版自动标注 prompt。

旧 prompt 对该窗口给了 no_clear_news=true（漏判美伊升级信号）。
新 prompt（reference_changes + 跨资产签名表 + 地缘例外）预期应选中 id=6217
（以军参谋长"重返战斗/更强大打击的前奏"，22:27，恰在窗口内、紧贴下跌加速段）。

只调模型、不碰数据库。跑法：D:\\anaconda\\python.exe scripts/smoke_prompt_iran_replay.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.annotation_service import _call_deepseek_reasoner, _parse_auto_annotate_response

# 窗口 #26：BTC/USDT，北京时间 2026-06-09 21:10 ~ 22:50（UTC 13:10 ~ 14:50）
# 跨资产同期变动按当晚真实走势还原：纳指第一波下跌、避险签名（10Y 下行 + 金微涨）
WINDOW = {
    "symbol": "BTC/USDT",
    "start_utc": "2026-06-09T13:10:00",
    "end_utc": "2026-06-09T14:50:00",
    "threshold_pct": 1.0,
    "price_start": 107850.0,
    "price_end": 105620.0,
    "change_pct": -2.07,
    "reference_changes": {
        "纳指": "-1.12%",
        "原油": "-0.55%",
        "黄金": "+0.28%",
        "美债10Y": "-3.5bp",
        "美元指数": "+0.14%",
    },
}

# 候选新闻：当晚真实条目（生产库 id / 北京时间 / 来源 / 标题），含升级、缓和、综述、噪音四类
CANDIDATES = [
    {"id": 6178, "time_bj": "2026-06-09 21:46", "source": "jin10", "llm_score": 4,
     "title": "巴西财长：我们将谨慎评估相关措施，以缓解伊朗战争对燃料供应的影响。", "content": ""},
    {"id": 6181, "time_bj": "2026-06-09 21:51", "source": "financialjuice", "llm_score": 5,
     "title": "FinancialJuice: Talks on an agreement to prevent Iran from acquiring a nuclear weapon are making positive progress - White House official", "content": ""},
    {"id": 6180, "time_bj": "2026-06-09 21:53", "source": "jin10", "llm_score": 5,
     "title": "据沙特阿拉伯电视台援引一位白宫官员的话报道称，关于防止伊朗获得核武器的协议谈判正取得积极成果。", "content": ""},
    {"id": 6200, "time_bj": "2026-06-09 22:05", "source": "jin10", "llm_score": 3,
     "title": "美股盘初：三大指数涨跌不一，特斯拉涨超2%，能源股走低。", "content": ""},
    {"id": 6212, "time_bj": "2026-06-09 22:15", "source": "financialjuice", "llm_score": 6,
     "title": "FinancialJuice: Israel's Chief of Staff Zamir in the North: Attack we carried out is preparation for a larger and more powerful strike", "content": ""},
    {"id": 6217, "time_bj": "2026-06-09 22:27", "source": "jin10", "llm_score": 6,
     "title": "以色列国防军参谋长：我们随时准备立即重返与伊朗的战斗。近期对伊朗的袭击是更强大、更严重打击的前奏。", "content": ""},
    {"id": 6229, "time_bj": "2026-06-09 22:36", "source": "jin10", "llm_score": 5,
     "title": "以色列国防军参谋长：伊朗试图制定新规则的企图将会失败。", "content": ""},
    {"id": 6233, "time_bj": "2026-06-09 22:41", "source": "jin10", "llm_score": 5,
     "title": "【美能源部长：通过霍尔木兹海峡的船舶流量正“显著增加”】随着与伊朗的冲突持续，穿越霍尔木兹海峡的船舶流量正显著增加。", "content": ""},
    {"id": 6251, "time_bj": "2026-06-09 23:08", "source": "jin10", "llm_score": 6,
     "title": "巴方“打脸”特朗普：美伊“不太可能”数日内达成协议，以色列彻底搅乱和谈", "content": ""},
    {"id": 6257, "time_bj": "2026-06-09 23:10", "source": "jin10", "llm_score": 3,
     "title": "金十数据整理：欧盘美盘重要新闻汇总（2026-06-09）", "content": ""},
]

body = {"window": WINDOW, "candidate_news": CANDIDATES}
user_content = f"共 {len(CANDIDATES)} 条候选新闻。\n{json.dumps(body, ensure_ascii=False)}"

print("调用 DeepSeek reasoner（新版单窗口 prompt）...")
content, reasoning, duration = _call_deepseek_reasoner(user_content)
selected, no_clear, summary = _parse_auto_annotate_response(content, {c["id"] for c in CANDIDATES})

print(f"\n耗时 {duration:.1f}s")
print(f"no_clear_news = {no_clear}")
print(f"selected_news_ids = {selected}")
print(f"summary = {summary}")
print(f"\n--- reasoning（前 1500 字）---\n{reasoning[:1500]}")

ok = (not no_clear) and (6217 in selected or 6212 in selected)
bad = any(i in selected for i in (6200, 6257))  # 噪音/综述不该被选
print(f"\n判定：{'PASS' if ok and not bad else 'FAIL'}"
      f"（期望选中 6217/6212 升级信号，且不选 6200 噪音、6257 综述）")
