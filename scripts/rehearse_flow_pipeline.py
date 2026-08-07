# -*- coding: utf-8 -*-
"""本地彩排：把真实 pivot 加上两个合成的资金流矩阵，跑完整资金流链路。

用法（需先由 remote_puller 拉到本地 pivot 缓存）：
    D:\\anaconda\\python.exe scripts/rehearse_flow_pipeline.py

目的：服务器补丁还没打，但要提前证明整条链在**生产形状**的数据上能跑通、
跑得快、算得对 —— 2×2 的玩具矩阵证明不了 2000×400 的事。

战绩：2026-08-07 首次运行即抓到一个单元测试漏掉的真 bug —— 整根最新 bar 串列
（482 个币全坏）在 2000 行历史里只占 0.05%，低于全矩阵 0.1% 的阈值，闸门放行。
修复是给「最新 bar」单设一道检查（config.FLOW_LATEST_BAR_VIOLATION_MAX_RATIO）。
改动闸门逻辑后建议重跑本脚本。

合成规则（模拟补丁后的真实数据形态）：
  quote_volume  = 与 close 同形状的正数，close 为 NaN 处同样 NaN
  taker_buy     = quote_volume × 一个 0.3~0.7 的比例（永远满足 0 <= tb <= qv）
真实数字对不上没关系 —— 这里验的是管道，不是行情。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\market_monitor")

import config  # noqa: E402
from services import remote_fs  # noqa: E402
from services import sector_flows  # noqa: E402
from scanners.sector_scanner import normalize_pivot_symbol  # noqa: E402

CACHE = Path(config.LOCAL_CACHE_DIR)
rng = np.random.default_rng(20260807)


def synth(close: pd.DataFrame):
    """按 close 的形状造 quote_volume / taker_buy，保持 NaN 位置一致。"""
    mask = close.notna()
    qv = pd.DataFrame(
        rng.lognormal(mean=12.0, sigma=1.5, size=close.shape),
        index=close.index, columns=close.columns,
    ).where(mask)
    ratio = pd.DataFrame(
        rng.uniform(0.3, 0.7, size=close.shape),
        index=close.index, columns=close.columns,
    )
    tb = (qv * ratio).where(mask)
    return qv, tb


def main():
    for market in ("spot", "swap"):
        fname = f"preprocess_1h_resample__{config.REMOTE_OFFSET}__market_pivot_{market}_2026.pkl"
        path = CACHE / fname
        if not path.exists():
            print(f"[SKIP] {market}: 本地缓存不存在 {path}")
            continue

        t0 = time.perf_counter()
        pivot = remote_fs.load_pickle(path)
        close = pivot["close"]
        print(f"\n=== {market} ===")
        print(f"真实 close 形状: {close.shape[0]} 行 × {close.shape[1]} 列"
              f"（加载 {time.perf_counter()-t0:.1f}s）")

        # 1) 未打补丁：勾稽门必须判「缺字段」
        reason = sector_flows.check_flow_gate(pivot)
        assert reason and "缺字段" in reason, f"未打补丁时应判缺字段，实际: {reason}"
        print(f"[OK] 未打补丁 → 勾稽门拦下: {reason[:40]}...")

        # 2) 打上合成补丁
        qv, tb = synth(close)
        pivot[sector_flows.QUOTE_VOLUME_KEY] = qv
        pivot[sector_flows.TAKER_BUY_KEY] = tb

        t0 = time.perf_counter()
        reason = sector_flows.check_flow_gate(pivot)
        gate_ms = (time.perf_counter() - t0) * 1000
        assert reason is None, f"打补丁后勾稽门不该拦: {reason}"
        print(f"[OK] 打补丁后 → 勾稽门通过（耗时 {gate_ms:.0f}ms）")

        # 3) 币级窗口求和
        as_of = close.index.max()
        t0 = time.perf_counter()
        flows = sector_flows.per_symbol_flows(pivot, as_of=as_of.to_pydatetime().replace(tzinfo=None))
        calc_s = time.perf_counter() - t0
        print(f"[OK] 币级求和：{len(flows)} 个 symbol（耗时 {calc_s:.2f}s）")

        # 4) 抽一个币手工核对
        sample = next(iter(flows))
        col = next(c for c in close.columns if normalize_pivot_symbol(str(c)) == sample)
        expect_qv = float(qv[col].iloc[-24:].sum())
        expect_net = float((2 * tb[col].iloc[-24:] - qv[col].iloc[-24:]).sum())
        got_qv = flows[sample]["qv_24h"]
        got_net = flows[sample]["net_24h"]
        ok_qv = abs(got_qv - expect_qv) < max(1.0, abs(expect_qv) * 1e-9)
        ok_net = abs(got_net - expect_net) < max(1.0, abs(expect_net) * 1e-9)
        print(f"[{'OK' if ok_qv and ok_net else 'FAIL'}] 抽样核对 {sample}({col}) 24h: "
              f"qv 期望 {expect_qv:,.0f} 得 {got_qv:,.0f} / "
              f"net 期望 {expect_net:,.0f} 得 {got_net:,.0f}")
        assert ok_qv and ok_net

        # 5) 强度比率必须落在 [-100%, +100%]
        bad = [
            (s, v["net_24h"] / v["qv_24h"])
            for s, v in flows.items()
            if v.get("qv_24h") and abs(v["net_24h"] / v["qv_24h"]) > 1.0000001
        ]
        print(f"[{'OK' if not bad else 'FAIL'}] 强度比率越界数: {len(bad)}")
        assert not bad

        # 6) 板块聚合（用一个真实规模的成员集合）
        members = set(list(flows)[:60])
        t0 = time.perf_counter()
        side = sector_flows.aggregate_side(flows, members)
        print(f"[OK] 板块聚合 60 币：tokens={side.tokens}, "
              f"net_24h={side.net['24h']:,.0f}, qv_24h={side.qv['24h']:,.0f}, "
              f"强度={side.net['24h']/side.qv['24h']:+.1%}（耗时 {(time.perf_counter()-t0)*1000:.0f}ms）")

        # 7) 恒等式违规注入：造一格坏数据，看闸门能否在生产规模下抓到
        pivot[sector_flows.TAKER_BUY_KEY] = tb.copy()
        pivot[sector_flows.TAKER_BUY_KEY].iloc[-1, :] = qv.iloc[-1, :] * 5  # 整行串列
        reason = sector_flows.check_flow_gate(pivot)
        print(f"[{'OK' if reason and '恒等式' in reason else 'FAIL'}] 注入整行串列 → {reason}")
        assert reason and "恒等式" in reason

    print("\n彩排全部通过：补丁后的链路在生产形状数据上可用。")


if __name__ == "__main__":
    main()
