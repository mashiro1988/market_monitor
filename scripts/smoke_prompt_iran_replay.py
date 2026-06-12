# -*- coding: utf-8 -*-
"""自动标注 prompt 实弹回归：用生产环境的真实漏判案例重放单窗口标注。

每个场景 = 一次真实发生过的漏判（旧 prompt 给了 no_clear_news=true），
修正后的 prompt 必须能归因。只调模型、不碰数据库。
跑法：D:\\anaconda\\python.exe scripts/smoke_prompt_iran_replay.py
费用：每场景一次 DeepSeek reasoner 调用。
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.annotation_service import _call_deepseek_reasoner, _parse_auto_annotate_v2

DRIVER = ("driver",)   # v2.1：驱动不分主次

SCENARIOS = [
    {
        # 场景 1：2026-06-09 晚窗口 #26（BTC 21:10~22:50 BJ，-2.07%）。
        # 旧 prompt 漏判机制：地缘新闻不提 symbol 被当间接关联 + 新闻晚于窗口起点被拒。
        # 黄金给 -0.30%（当晚事实：金价未涨），验证黄金不是地缘归因的必要条件。
        "name": "美伊升级·盘中长窗口（黄金未涨）",
        "window": {
            "symbol": "BTC/USDT",
            "start_utc": "2026-06-09T13:10:00", "end_utc": "2026-06-09T14:50:00",
            "threshold_pct": 1.0, "price_start": 107850.0, "price_end": 105620.0, "change_pct": -2.07,
            "reference_changes": {"纳指": "-1.12%", "原油": "-0.55%", "黄金": "-0.30%",
                                   "美债10Y": "-3.5bp", "美元指数": "+0.14%"},
        },
        "candidates": [
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
        ],
        # 期望：升级信号（6217 或同簇 6212）为主/次驱动；噪音 6200 与综述 6257 不得为驱动；类型非 no_clear/情绪
        "judge": lambda p: any(p.news_roles.get(i) in DRIVER for i in (6217, 6212))
                  and not any(p.news_roles.get(i) in DRIVER for i in (6200, 6257))
                  and p.market_reaction_type not in (None, "no_clear_driver", "emotional_noise"),
        "expect": "6217/6212 为主/次驱动；6200/6257 非驱动；反应类型为地缘/风险类",
    },
    {
        # 场景 2：2026-06-10 05:15~05:30 BJ（BTC -0.53%）——生产真实漏判。
        # CME 日休：纳指/原油/黄金全 null，仅剩低波动对标走平；美军 05:00(BJ) 打击伊朗，
        # BTC 是全市场唯一即时反应者。旧 prompt 以"reference change 无明显变化"拒选。
        "name": "美军反击·凌晨 CME 日休（对标不可用）",
        "window": {
            "symbol": "BTC/USDT",
            "start_utc": "2026-06-09T21:15:00", "end_utc": "2026-06-09T21:30:00",
            "threshold_pct": 0.5, "price_start": 105800.0, "price_end": 105240.0, "change_pct": -0.53,
            "reference_changes": {"纳指": None, "原油": None, "黄金": None,
                                   "美债10Y": "+0.0bp", "美元指数": "+0.04%"},
        },
        "candidates": [
            {"id": 6521, "time_bj": "2026-06-10 05:15", "source": "jin10", "llm_score": 4,
             "title": "伊朗媒体援引当地居民的话报道称，伊朗锡里克（Sirik）地区传出爆炸声，原因不明。", "content": ""},
            {"id": 6517, "time_bj": "2026-06-10 05:18", "source": "financialjuice", "llm_score": 6,
             "title": "FinancialJuice: US military: Centcom forces launched self-defense strikes against Iran at 5 p.m. ET today", "content": ""},
            {"id": 6515, "time_bj": "2026-06-10 05:18", "source": "jin10", "llm_score": 7,
             "title": "【美军：对伊朗发起自卫打击 以回应此前直升机被击落】美国中央司令部：美国中央司令部部队于美东时间今天下午5点（北京时间今日5点），对伊朗发起自卫打击。", "content": ""},
            {"id": 6522, "time_bj": "2026-06-10 05:28", "source": "jin10", "llm_score": 5,
             "title": "【美副总统称美伊协议或在一周至数月内达成】美国副总统万斯表示，美国“非常接近”与伊朗达成一项能够长期解决伊朗核问题的协议。", "content": ""},
            {"id": 6531, "time_bj": "2026-06-10 05:29", "source": "jin10", "llm_score": 6,
             "title": "据纽约邮报：美国针对伊朗的报复性打击大约在30分钟前开始。美军声明中提到行动保持“对等性”，这可能暗示此次打击并非要重回全面战争状态。", "content": ""},
            {"id": 6530, "time_bj": "2026-06-10 05:30", "source": "financialjuice", "llm_score": 4,
             "title": "FinancialJuice: Iran's Fars: blasts heard in eastern Hormozgan regions", "content": ""},
            {"id": 6529, "time_bj": "2026-06-10 05:31", "source": "jin10", "llm_score": 4,
             "title": "据伊朗媒体Fars News：霍尔木兹甘省东部地区传出爆炸声。", "content": ""},
            {"id": 6526, "time_bj": "2026-06-10 05:32", "source": "jin10", "llm_score": 5,
             "title": "伊朗官方媒体称，已确认锡里克（SIRIK）地区遭到了导弹袭击。", "content": ""},
            {"id": 6537, "time_bj": "2026-06-10 05:43", "source": "jin10", "llm_score": 4,
             "title": "金十提示：此前伊朗方面表示，若美国以军用直升机坠毁为借口再次挑起事端，伊方将作出坚决回应。", "content": ""},
            {"id": 6541, "time_bj": "2026-06-10 05:54", "source": "jin10", "llm_score": 5,
             "title": "据Axios：美国官员称美军袭击了霍尔木兹海峡周边的数个伊朗防空系统和雷达系统。", "content": ""},
        ],
        # 期望：美军打击首报（6515/6517）为主/次驱动；背景回顾 6537 不得为驱动；类型非 no_clear/情绪
        "judge": lambda p: any(p.news_roles.get(i) in DRIVER for i in (6515, 6517))
                  and p.news_roles.get(6537) not in DRIVER
                  and p.market_reaction_type not in (None, "no_clear_driver", "emotional_noise"),
        "expect": "6515/6517 为主/次驱动；6537 非驱动；反应类型为地缘/风险类",
    },
    {
        # 场景 3：2026-06-11 07:15~08:20 BJ（BTC +1.17%）——生产真实案例（v3 prompt 全标 noise）。
        # 美伊升级新闻密集（关闭霍尔木兹）但 BTC 逆势上涨、原油反跌：市场无视升级。
        # contradictory 的正确语义=关系标签：重大升级首报应标 contradictory 留痕，而非全降级 noise；
        # 无 driver → type=no_news_driver。
        "name": "市场无视地缘升级·BTC 逆势上涨（contradictory 语义）",
        "window": {
            "symbol": "BTC/USDT",
            "start_utc": "2026-06-10T23:15:00", "end_utc": "2026-06-11T00:20:00",
            "threshold_pct": 0.5, "price_start": 101170.9, "price_end": 102355.8, "change_pct": 1.17,
            "reference_changes": {"纳指": "+0.26%", "原油": "-1.12%", "黄金": "+0.29%",
                                   "美债10Y": "+0.8bp", "美元指数": None},
        },
        "candidates": [
            {"id": 8214, "time_bj": "2026-06-11 06:48", "source": "jin10", "llm_score": 7,
             "title": "【伊朗：关闭霍尔木兹海峡 试图通行将遭打击】伊朗武装部队总参谋部宣布，即日起关闭霍尔木兹海峡，任何试图通行的船只将遭到打击。", "content": ""},
            {"id": 8218, "time_bj": "2026-06-11 06:53", "source": "jin10", "llm_score": 5,
             "title": "WTI原油日内涨幅扩大至1.00%，现报93.68美元/桶。", "content": ""},
            {"id": 8221, "time_bj": "2026-06-11 06:58", "source": "jin10", "llm_score": 5,
             "title": "美军连续两晚轰炸伊朗！局势会如何演变，市场屏息以待", "content": ""},
            {"id": 8240, "time_bj": "2026-06-11 07:22", "source": "jin10", "llm_score": 6,
             "title": "【伊朗革命卫队：已向美军中东多处基地发射弹道导弹】伊朗革命卫队宣布对美军位于卡塔尔与伊拉克的基地发动新一轮导弹打击。", "content": ""},
            {"id": 8252, "time_bj": "2026-06-11 07:40", "source": "financialjuice", "llm_score": 6,
             "title": "FinancialJuice: US Central Command confirms strikes on Iranian missile sites near Tehran ongoing", "content": ""},
            {"id": 8229, "time_bj": "2026-06-11 07:05", "source": "jin10", "llm_score": 3,
             "title": "金十数据全球财经早餐 | 2026年6月11日", "content": ""},
            {"id": 8299, "time_bj": "2026-06-11 08:01", "source": "jin10", "llm_score": 5,
             "title": "日韩股市开盘大跌，日经225指数跌2.1%，韩国KOSPI跌1.8%。", "content": ""},
            {"id": 8310, "time_bj": "2026-06-11 08:15", "source": "jin10", "llm_score": 4,
             "title": "我喜欢通胀！特朗普语出惊人，市场哗然", "content": ""},
            {"id": 8334, "time_bj": "2026-06-11 08:40", "source": "jin10", "llm_score": 3,
             "title": "金十数据整理：隔夜全球市场表现汇总（2026-06-11）", "content": ""},
        ],
        # 期望：无 driver；窗口内重大升级（8240/8252，方向利空而价格涨）至少一条标 contradictory；
        # 综述（8229/8334）不得为 driver；type=no_news_driver
        "judge": lambda p: not any(r == "driver" for r in p.news_roles.values())
                  and any(p.news_roles.get(i) == "contradictory" for i in (8240, 8252, 8214))
                  and p.market_reaction_type == "no_news_driver",
        "expect": "无 driver；升级首报 8240/8252 标 contradictory（市场无视）；type=no_news_driver",
    },
]


def run_scenario(sc) -> bool:
    body = {"window": sc["window"], "candidate_news": sc["candidates"]}
    user_content = f"共 {len(sc['candidates'])} 条候选新闻。\n{json.dumps(body, ensure_ascii=False)}"
    print(f"\n=== 场景：{sc['name']} ===")
    content, reasoning, duration = _call_deepseek_reasoner(user_content)
    parsed = _parse_auto_annotate_v2(content, {c["id"] for c in sc["candidates"]})
    ok = sc["judge"](parsed)
    print(f"耗时 {duration:.1f}s  type={parsed.market_reaction_type}  confidence={parsed.confidence}")
    print(f"news_roles: {parsed.news_roles}")
    print(f"summary: {parsed.summary}")
    print(f"判定：{'PASS' if ok else 'FAIL'}（期望：{sc['expect']}）")
    if not ok:
        print(f"--- reasoning（前 1200 字）---\n{reasoning[:1200]}")
    return ok


if __name__ == "__main__":
    results = [run_scenario(sc) for sc in SCENARIOS]
    print(f"\n总判定：{'ALL PASS' if all(results) else 'FAIL'}（{sum(results)}/{len(results)}）")
    sys.exit(0 if all(results) else 1)
