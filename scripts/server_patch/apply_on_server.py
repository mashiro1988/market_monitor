# -*- coding: utf-8 -*-
"""在数据服务器上就地打 taker 宽表补丁（2026-08-08 净资金流入 spec §10.1）。

自包含，无外部依赖（只要 pandas）。设计成**跑几次都一样**：已经打过就直接跳过。

用法（在数据服务器上）：
    # 1) 预检：不改任何文件，用真实数据在内存里试跑补丁
    /root/anaconda3/envs/psm0129/bin/python /root/apply_on_server.py --preflight

    # 2) 落盘：写文件 + 编译校验（不通过自动还原），仍不重启
    /root/anaconda3/envs/psm0129/bin/python /root/apply_on_server.py --apply

    # 3) 重启（此步由人来做，看清楚了再敲）
    pm2 restart realtime_data

回滚：
    cp /root/backup/taker_patch_20260808/preprocess.py.orig \\
       /root/data_center/core/preprocess.py && pm2 restart realtime_data

2026-08-08 侦察确认的环境事实（与设计稿最初的猜测不同，以这里为准）：
  - 文件在 /root/data_center/core/preprocess.py（**不是** bmac/preprocess.py）
  - BMAC 由 pm2 托管，进程名 realtime_data，fork 模式，watch=False（改文件不会自动重启）
  - 解释器 /root/anaconda3/envs/psm0129/bin/python（3.12.8 / pandas 2.2.3 / numpy 2.0.2）
  - 写入 offset 是每小时 :10/:20/:30/:35，其余时间空转 —— 挑 :40~次点 前的空窗操作
  - data_center 不是 git 仓库，备份是唯一退路
"""
from __future__ import annotations

import argparse
import ast
import glob
import shutil
import sys

import pandas as pd

TARGET = "/root/data_center/core/preprocess.py"
BACKUP_DIR = "/root/backup/taker_patch_20260808"
BACKUP = f"{BACKUP_DIR}/preprocess.py.orig"

OLD_COLS = ("PIVOT_COLUMNS = ['candle_begin_time', 'symbol', 'open', 'close', "
            "'avg_price_1m', 'funding_fee']")
NEW_COLS = ("PIVOT_COLUMNS = ['candle_begin_time', 'symbol', 'open', 'close', "
            "'avg_price_1m', 'funding_fee',\n"
            "                 'quote_volume', 'taker_buy_quote_asset_volume']")

OLD_FN = """def make_market_pivot(market_dict, market_type='spot'):
    df_list = [df[PIVOT_COLUMNS].dropna(subset='symbol') for df in market_dict.values()]
    df_all_market = pd.concat(df_list, ignore_index=True)
    df_all_market['symbol'] = pd.Categorical(df_all_market['symbol'])
    df_open = df_all_market.pivot(values='open', index='candle_begin_time', columns='symbol')
    df_close = df_all_market.pivot(values='close', index='candle_begin_time', columns='symbol')
    df_vwap1m = df_all_market.pivot(values='avg_price_1m', index='candle_begin_time', columns='symbol')
    if market_type == 'swap':
        df_rate = df_all_market.pivot(values='funding_fee', index='candle_begin_time', columns='symbol')
        df_rate.fillna(value=0, inplace=True)
        return {'open': df_open, 'close': df_close, 'funding_rate': df_rate, 'vwap1m': df_vwap1m}
    else:
        return {'open': df_open, 'close': df_close, 'vwap1m': df_vwap1m}"""

NEW_FN = """def make_market_pivot(market_dict, market_type='spot'):
    # market_monitor 本地补丁（2026-08-08）：缺列补 NaN 而不是 KeyError。
    # data_api 备用源个别文件若缺 taker 字段，硬取会让整轮预处理崩掉——
    # 预处理停产会波及交易框架供数，此处宁可缺数不可崩溃。
    df_list = [
        df.reindex(columns=PIVOT_COLUMNS).dropna(subset='symbol')
        for df in market_dict.values()
    ]
    df_all_market = pd.concat(df_list, ignore_index=True)
    df_all_market['symbol'] = pd.Categorical(df_all_market['symbol'])
    df_open = df_all_market.pivot(values='open', index='candle_begin_time', columns='symbol')
    df_close = df_all_market.pivot(values='close', index='candle_begin_time', columns='symbol')
    df_vwap1m = df_all_market.pivot(values='avg_price_1m', index='candle_begin_time', columns='symbol')
    # market_monitor 本地补丁（2026-08-08）：资金流两个矩阵
    df_qv = df_all_market.pivot(values='quote_volume', index='candle_begin_time', columns='symbol')
    df_taker = df_all_market.pivot(values='taker_buy_quote_asset_volume',
                                   index='candle_begin_time', columns='symbol')
    result = {'open': df_open, 'close': df_close, 'vwap1m': df_vwap1m,
              'quote_volume': df_qv, 'taker_buy_quote_asset_volume': df_taker}
    if market_type == 'swap':
        df_rate = df_all_market.pivot(values='funding_fee', index='candle_begin_time', columns='symbol')
        df_rate.fillna(value=0, inplace=True)
        result['funding_rate'] = df_rate
    return result"""

REQUIRED_AFTER = (
    "'quote_volume', 'taker_buy_quote_asset_volume']",
    "df.reindex(columns=PIVOT_COLUMNS)",
    "result['funding_rate'] = df_rate",
    "return result",
)


def build_patched(source: str) -> str:
    assert source.count(OLD_COLS) == 1, f"PIVOT_COLUMNS 原文匹配到 {source.count(OLD_COLS)} 处，应为 1"
    assert source.count(OLD_FN) == 1, f"make_market_pivot 原文匹配到 {source.count(OLD_FN)} 处，应为 1"
    return source.replace(OLD_COLS, NEW_COLS).replace(OLD_FN, NEW_FN)


def _extract(source: str, tag: str):
    tree = ast.parse(source)
    wanted = [
        n for n in tree.body
        if (isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) == "PIVOT_COLUMNS" for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name == "make_market_pivot")
    ]
    assert len(wanted) == 2, f"{tag}: 抠出 {len(wanted)} 个节点"
    ns = {"pd": pd}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), tag, "exec"), ns)
    return ns["make_market_pivot"], ns["PIVOT_COLUMNS"]


def _real_market_dict(market: str, n_symbols: int = 6):
    """仿 gen_batch_data：读真实 1h resample，补上 add_additional_cols 加的那几列。"""
    files = sorted(glob.glob(
        f"/root/data_center/data/binance_{market}_1h_resample/30m/*USDT.pkl"))[:n_symbols]
    out = {}
    for path in files:
        symbol = path.rsplit("/", 1)[-1][:-4]
        df = pd.read_pickle(path).copy()
        df["symbol"] = symbol
        df["funding_fee"] = 0.0
        df["avg_price_1m"] = df["open"]
        out[symbol] = df
    return out


def preflight() -> int:
    source = open(TARGET, encoding="utf-8").read()
    print(f"读到 {TARGET}（{len(source)} 字节）")
    if "taker_buy_quote_asset_volume" in source:
        print("[SKIP] 补丁似乎已经打过了")
        return 0

    patched = build_patched(source)
    print("[OK] 两处原文均精确匹配，补丁可干净套用")
    compile(patched, TARGET, "exec")
    print("[OK] 打完补丁语法编译通过")

    old_fn, old_cols = _extract(source, "<原版>")
    new_fn, new_cols = _extract(patched, "<补丁版>")
    print(f"[OK] PIVOT_COLUMNS: {len(old_cols)} 列 → {len(new_cols)} 列")

    for market in ("spot", "swap"):
        md = _real_market_dict(market)
        before = old_fn({k: v.copy() for k, v in md.items()}, market)
        after = new_fn({k: v.copy() for k, v in md.items()}, market)

        assert set(after) - set(before) == {"quote_volume", "taker_buy_quote_asset_volume"}
        assert set(before) <= set(after), f"丢了旧键: {set(before) - set(after)}"
        for key in before:                      # 旧键逐值一致 —— 交易框架吃的就是这些
            assert before[key].equals(after[key]), f"{market}/{key} 数值被改动了！"

        qv, tb = after["quote_volume"], after["taker_buy_quote_asset_volume"]
        assert qv.shape == after["close"].shape
        assert list(qv.columns) == list(after["close"].columns)
        assert qv.index.equals(after["close"].index)
        both = qv.notna() & tb.notna()
        assert bool((((tb >= 0) & (tb <= qv)) | ~both).all().all()), "恒等式不成立"

        latest_net = (2 * tb.iloc[-1] - qv.iloc[-1]).dropna()
        print(f"[OK] {market}: 旧键 {sorted(before)} 逐值未变；形状 {qv.shape}；恒等式成立")
        print("      最新 bar 净流入样例："
              + "，".join(f"{s}={v:+,.0f}" for s, v in latest_net.head(3).items()))

    print("\n预检通过：补丁在真实数据上产出正确、且不动任何旧字段。")
    return 0


def apply() -> int:
    source = open(TARGET, encoding="utf-8").read()
    if "taker_buy_quote_asset_volume" in source:
        print("[SKIP] 已经打过补丁，不重复动")
        return 0
    if source.count(OLD_COLS) != 1 or source.count(OLD_FN) != 1:
        print("[ABORT] 原文匹配数不对，文件可能已被改动，不敢动")
        return 1
    import os
    if not os.path.exists(BACKUP):
        print(f"[ABORT] 备份不存在: {BACKUP}。先执行：\n"
              f"  mkdir -p {BACKUP_DIR} && cp {TARGET} {BACKUP}")
        return 1

    patched = build_patched(source)
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(patched)
    print(f"[OK] 已写入 {TARGET}（{len(source)} → {len(patched)} 字节）")

    # 落盘后立刻编译校验：不通过马上还原，绝不让 BMAC 带着语法错误重启
    try:
        compile(open(TARGET, encoding="utf-8").read(), TARGET, "exec")
    except SyntaxError as exc:
        shutil.copyfile(BACKUP, TARGET)
        print(f"[ABORT] 落盘后编译失败，已从备份还原：{exc}")
        return 1
    print("[OK] 落盘文件编译通过")

    text = open(TARGET, encoding="utf-8").read()
    for needle in REQUIRED_AFTER:
        if needle not in text:
            shutil.copyfile(BACKUP, TARGET)
            print(f"[ABORT] 复核缺失片段 {needle!r}，已还原")
            return 1
    print("[OK] 关键片段复核通过（含 funding_rate 仍在 swap 分支里）")
    print("\n补丁已落盘，BMAC 尚未重启。下一步手动执行：pm2 restart realtime_data")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="数据服务器 taker 宽表补丁")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true", help="只预检，不改文件")
    group.add_argument("--apply", action="store_true", help="落盘（含编译校验与自动还原）")
    args = ap.parse_args()
    return preflight() if args.preflight else apply()


if __name__ == "__main__":
    sys.exit(main())
