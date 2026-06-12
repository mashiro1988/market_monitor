# -*- coding: utf-8 -*-
"""用生产同款窗口逻辑重放昨晚（2026-06-10 晚 ~ 06-11 晨）纳指期货走势。

复刻 load_price_windows：15min 基准取容差内最近 bar，|chg|>=0.3% 触发，
同向相邻触发间隔 <=60min 合并成事件窗口。数据：yfinance NQ=F 5m 收盘价。
"""
import sys
from datetime import datetime, timedelta, timezone

import yfinance as yf

THRESHOLD = 0.3          # NQ=F 生产阈值 %/15min
WINDOW_MIN = 15
MERGE_GAP_MIN = 60
TOL_MIN = 10             # 基准容差（生产 = SCAN_INTERVALS["price"]*2）

df = yf.download(["NQ=F"], period="5d", interval="5m", progress=False, auto_adjust=True)
close = df["Close"]["NQ=F"].dropna()

# bar 结束时刻（UTC naive），与生产口径一致（idx + 5min）
bars = [(idx.tz_convert("UTC").tz_localize(None) + timedelta(minutes=5), float(v)) for idx, v in close.items()]

# 关注区间：BJ 06-10 21:00 ~ 06-11 05:30 = UTC 06-10 13:00 ~ 06-10 21:30
t0 = datetime(2026, 6, 10, 13, 0)
t1 = datetime(2026, 6, 10, 21, 30)
seg = [(t, p) for t, p in bars if t0 <= t <= t1]
# 模拟生产库：挖掉服务器实际缺失的两根连续 bar（诊断输出：19:05→19:20 缺口）
if "--prod" in sys.argv:
    missing = {datetime(2026, 6, 10, 19, 10), datetime(2026, 6, 10, 19, 15)}
    seg = [(t, p) for t, p in seg if t not in missing]
    print("[模拟生产库：已移除 19:10 / 19:15 两根 bar]")
print(f"bars in range: {len(seg)}  {seg[0][0]} ~ {seg[-1][0]} (UTC)")

def bj(t): return (t + timedelta(hours=8)).strftime("%m-%d %H:%M")

# 15min 滚动变化 + 触发
triggers = []
for i, (t, p) in enumerate(seg):
    target = t - timedelta(minutes=WINDOW_MIN)
    base = None; best = None
    for tt, pp in seg:
        if tt >= t: break
        d = abs((tt - target).total_seconds())
        if d <= TOL_MIN * 60 and (best is None or d < best):
            base, best = (tt, pp), d
    if base is None: continue
    chg = (p - base[1]) / base[1] * 100
    if abs(chg) >= THRESHOLD:
        triggers.append({"start": base[0], "end": t, "p0": base[1], "p1": p, "chg": chg,
                          "sign": 1 if chg >= 0 else -1})

print(f"\n触发点：{len(triggers)} 个")

# 同向合并
events = []
for tr in sorted(triggers, key=lambda x: x["end"]):
    if events and events[-1][-1]["sign"] == tr["sign"] and \
       (tr["start"] - events[-1][-1]["end"]) <= timedelta(minutes=MERGE_GAP_MIN):
        events[-1].append(tr)
    else:
        events.append([tr])

print(f"合并后窗口：{len(events)} 个\n")
for ev in events:
    s, e = ev[0], ev[-1]
    net = (e["p1"] - s["p0"]) / s["p0"] * 100
    peak = max(abs(t_["chg"]) for t_ in ev)
    print(f"  {bj(s['start'])} → {bj(e['end'])} (BJ)  段数={len(ev):2d}  净={net:+.2f}%  峰值15m={peak:.2f}%  "
          f"方向={'涨' if s['sign']>0 else '跌'}  {s['p0']:.0f}→{e['p1']:.0f}")

# 用户说的凌晨 02:00 以后那段慢跌：BJ 06-11 02:00~05:00 = UTC 06-10 18:00~21:00
print("\n—— BJ 02:00 之后（用户说的慢跌段）——")
lateseg = [(t, p) for t, p in seg if t >= datetime(2026, 6, 10, 18, 0)]
if lateseg:
    hi = max(lateseg, key=lambda x: x[1]); lo = min(lateseg, key=lambda x: x[1])
    print(f"  区间 {bj(lateseg[0][0])} ~ {bj(lateseg[-1][0])}  起 {lateseg[0][1]:.0f} 终 {lateseg[-1][1]:.0f}")
    print(f"  最高 {hi[1]:.0f} @ {bj(hi[0])}   最低 {lo[1]:.0f} @ {bj(lo[0])}")
    print(f"  高→低净跌 {(lo[1]-hi[1])/hi[1]*100:+.2f}%")
    mx = 0.0
    for i, (t, p) in enumerate(lateseg):
        target = t - timedelta(minutes=WINDOW_MIN)
        for tt, pp in lateseg:
            if tt >= t: break
            if abs((tt - target).total_seconds()) <= TOL_MIN * 60:
                mx = max(mx, abs((p - pp) / pp * 100))
    print(f"  该段最大单个 15min 变动 = {mx:.2f}%（阈值 {THRESHOLD}%）")

# 横跳段统计：BJ 23:20~02:00 = UTC 15:20~18:00
print("\n—— BJ 23:20~02:00（用户说的横跳段）——")
chopseg = [(t, p) for t, p in seg if datetime(2026, 6, 10, 15, 20) <= t <= datetime(2026, 6, 10, 18, 0)]
if chopseg:
    hi = max(p for _, p in chopseg); lo = min(p for _, p in chopseg)
    net = (chopseg[-1][1] - chopseg[0][1]) / chopseg[0][1] * 100
    print(f"  起 {chopseg[0][1]:.0f} 终 {chopseg[-1][1]:.0f}  净={net:+.2f}%  振幅={(hi-lo)/lo*100:.2f}%")
    in_chop = [tr for tr in triggers if datetime(2026,6,10,15,20) <= tr["end"] <= datetime(2026,6,10,18,0)]
    ups = sum(1 for t_ in in_chop if t_["sign"] > 0); downs = len(in_chop) - ups
    print(f"  该段触发 {len(in_chop)} 次（涨向 {ups} / 跌向 {downs}）")
