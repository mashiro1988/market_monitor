# -*- coding: utf-8 -*-
"""数据服务器 BMAC 宽表补丁的**权威副本**（2026-08-07 净资金流入 spec §4）。

这个文件不参与本项目运行 —— 它是「要贴到数据服务器上的那两段代码」的留档，
也是 tests/test_server_pivot_patch.py 的被测对象。

**为什么单独存一份而不是直接改 scripts/server_src/preprocess.py**：
后者是从服务器抓下来的第三方源码副本，被 .gitignore 挡在版本库外（见 .gitignore:58），
不进 git 就等于没留档 —— BMAC 升级把补丁冲掉之后没有底稿可以重打。
这里只抄「我们改动的那两段」，进版本库、有测试盯着。

**贴到服务器的操作**：把下面两段替换掉 /root/data_center/bmac/preprocess.py 里的同名部分。
两处都是纯增量：原有键一个不动，旧消费者（用户的量化交易框架）向后兼容。
完整操作步骤见 docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md §10.1。
"""
import pandas as pd


# ============================================================
# 改动 ①：PIVOT_COLUMNS（原第 18 行）
# 追加 quote_volume / taker_buy_quote_asset_volume 两列
# ============================================================
PIVOT_COLUMNS = ['candle_begin_time', 'symbol', 'open', 'close', 'avg_price_1m', 'funding_fee',
                 'quote_volume', 'taker_buy_quote_asset_volume']


# ============================================================
# 改动 ②：make_market_pivot（原第 76-88 行）
# a) 切片改 reindex —— 缺列补 NaN 而不是 KeyError
# b) 增产 quote_volume / taker_buy_quote_asset_volume 两个矩阵
# ============================================================
def make_market_pivot(market_dict, market_type='spot'):
    # market_monitor 本地补丁（2026-08-07）：缺列补 NaN 而不是 KeyError。
    # data_api 备用源个别文件可能没有 taker 字段，硬取会让整轮预处理崩掉——
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
    # market_monitor 本地补丁（2026-08-07）：资金流两个矩阵
    df_qv = df_all_market.pivot(values='quote_volume', index='candle_begin_time', columns='symbol')
    df_taker = df_all_market.pivot(values='taker_buy_quote_asset_volume',
                                   index='candle_begin_time', columns='symbol')
    result = {'open': df_open, 'close': df_close, 'vwap1m': df_vwap1m,
              'quote_volume': df_qv, 'taker_buy_quote_asset_volume': df_taker}
    if market_type == 'swap':
        df_rate = df_all_market.pivot(values='funding_fee', index='candle_begin_time', columns='symbol')
        df_rate.fillna(value=0, inplace=True)
        result['funding_rate'] = df_rate
    return result
