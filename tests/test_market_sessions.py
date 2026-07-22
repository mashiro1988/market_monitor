# -*- coding: utf-8 -*-
"""交易时段表：夏令时切换、周末、维护段、午休、+10min 尾巴、未知品种 fail-open。

所有断言时刻都是 naive UTC（与库内口径一致）。
2026 美国夏令时：03-08 开始（EDT, UTC-4），11-01 结束（EST, UTC-5）。
"""
from datetime import datetime

from scanners.market_sessions import active_symbols, is_open, should_fetch


# ---------- 美股现指：夏令时切换让同一 UTC 时刻翻转 ----------

def test_gspc_dst_spring_forward():
    # 03-06（周五, EST）: 14:35 UTC = 09:35 EST → 开市
    assert is_open("^GSPC", datetime(2026, 3, 6, 14, 35))
    # 03-09（周一, EDT）: 14:35 UTC = 10:35 EDT → 开市；13:25 UTC = 09:25 EDT → 未开
    assert is_open("^GSPC", datetime(2026, 3, 9, 14, 35))
    assert not is_open("^GSPC", datetime(2026, 3, 9, 13, 25))
    # 但 03-06 的 13:35 UTC = 08:35 EST → 未开（同一 UTC 钟点冬夏答案不同）
    assert not is_open("^GSPC", datetime(2026, 3, 6, 13, 35))


def test_gspc_dst_fall_back():
    # 10-30（周五, EDT）: 13:35 UTC = 09:35 EDT → 开市
    assert is_open("^GSPC", datetime(2026, 10, 30, 13, 35))
    # 11-02（周一, EST）: 13:35 UTC = 08:35 EST → 未开；14:35 UTC → 开市
    assert not is_open("^GSPC", datetime(2026, 11, 2, 13, 35))
    assert is_open("^GSPC", datetime(2026, 11, 2, 14, 35))


# ---------- CME 期货：周界 + 每日维护段（芝加哥时区） ----------

def test_cme_daily_maintenance_break():
    # 2026-07-22 是周三。16:30 CT = 21:30 UTC（CDT, UTC-5）→ 维护段，闭市
    assert not is_open("ES=F", datetime(2026, 7, 22, 21, 30))
    # 17:30 CT = 22:30 UTC → 重开
    assert is_open("ES=F", datetime(2026, 7, 22, 22, 30))
    # 维护段开始后 10 分钟内 should_fetch 仍为 True（收尾抓最后一根 bar）
    assert should_fetch("ES=F", datetime(2026, 7, 22, 21, 5))
    assert not should_fetch("ES=F", datetime(2026, 7, 22, 21, 30))


def test_cme_weekend():
    # 周六全天闭市（2026-07-25 周六 12:00 UTC）
    assert not is_open("NQ=F", datetime(2026, 7, 25, 12, 0))
    assert not should_fetch("NQ=F", datetime(2026, 7, 25, 12, 0))
    # 周日 17:05 CT = 22:05 UTC 重开
    assert is_open("NQ=F", datetime(2026, 7, 26, 22, 5))
    # 周五 15:55 CT = 20:55 UTC 仍开；16:05 CT 闭市
    assert is_open("CL=F", datetime(2026, 7, 24, 20, 55))
    assert not is_open("CL=F", datetime(2026, 7, 24, 21, 5))


# ---------- 亚洲现指：午休、尾巴；无夏令时 ----------

def test_n225_lunch_and_tail():
    # 2026-07-22 周三。11:45 JST = 02:45 UTC → 午休
    assert not is_open("^N225", datetime(2026, 7, 22, 2, 45))
    # 午休开始后 10min 内 should_fetch 抓收尾
    assert should_fetch("^N225", datetime(2026, 7, 22, 2, 35))
    # 12:35 JST = 03:35 UTC → 下午场
    assert is_open("^N225", datetime(2026, 7, 22, 3, 35))
    # 收盘 15:30 JST；15:35 仍 fetch，15:45 停止
    assert should_fetch("^N225", datetime(2026, 7, 22, 6, 35))
    assert not should_fetch("^N225", datetime(2026, 7, 22, 6, 45))


def test_cn_indices_sessions():
    # 上证 2026-07-22 周三 10:00 CST = 02:00 UTC → 开市
    assert is_open("000001.SS", datetime(2026, 7, 22, 2, 0))
    # 12:00 CST = 04:00 UTC → 午休
    assert not is_open("399001.SZ", datetime(2026, 7, 22, 4, 0))
    # 14:30 CST = 06:30 UTC → 下午场
    assert is_open("399006.SZ", datetime(2026, 7, 22, 6, 30))


def test_kospi_continuous():
    # KOSPI 无午休：12:00 KST = 03:00 UTC → 开市
    assert is_open("^KS11", datetime(2026, 7, 22, 3, 0))


# ---------- 美元指数 / 债券 / 加密 ----------

def test_dxy_daily_break_ny():
    # ICE 每日 17:00-18:00 ET 跳过。2026-07-22: 17:30 ET = 21:30 UTC → 闭
    assert not is_open("DX-Y.NYB", datetime(2026, 7, 22, 21, 30))
    assert is_open("DX-Y.NYB", datetime(2026, 7, 22, 22, 30))


def test_bonds_and_crypto():
    # 美债近 24h（周三 12:00 UTC 开）；日债东京时段；加密永远开
    assert is_open("US_10Y", datetime(2026, 7, 22, 12, 0))
    assert is_open("JP_10Y", datetime(2026, 7, 22, 2, 0))    # 11:00 JST
    assert not is_open("JP_10Y", datetime(2026, 7, 22, 12, 0))  # 21:00 JST
    assert is_open("BTC/USDT", datetime(2026, 7, 25, 3, 0))     # 周六也开
    assert is_open("QQQ-USDT-SWAP", datetime(2026, 7, 25, 3, 0))


# ---------- 未知品种 fail-open + 集合过滤 ----------

def test_unknown_symbol_fails_open():
    assert is_open("NEW=F", datetime(2026, 7, 25, 12, 0))   # 宁多拉勿漏拉


def test_active_symbols_filters():
    # 周六 12:00 UTC：期货/现指全闭，加密开
    now = datetime(2026, 7, 25, 12, 0)
    got = active_symbols(["ES=F", "^GSPC", "^N225", "BTC/USDT"], now)
    assert got == {"BTC/USDT"}
