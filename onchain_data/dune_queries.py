"""
Dune 查询封装

功能：执行已保存的 Dune 查询，获取：
- ETH 持有量前100大地址
- 最近30天内逐日每账户的净买入金额(USD)与净买入量(ETH)

使用方法：
- 在 .env 中配置 DUNE_API_KEY
- 在 .env 中配置 DUNE_QUERY_ID_ETH_TOP100_NETFLOW（保存于 Dune 的查询ID）
"""
from __future__ import annotations

import os
import time
import pandas as pd
from typing import Optional, Dict, Any

import config

try:
    from dune_client.client import DuneClient
    from dune_client.types import QueryParameter
    from dune_client.query import QueryBase
except Exception as exc:  # 允许在未安装时给出更清晰错误
    DuneClient = None  # type: ignore
    QueryParameter = None  # type: ignore
    QueryBase = None  # type: ignore


class DuneQueryRunner:
    """负责与 Dune API 交互并返回 DataFrame。"""

    def __init__(self, api_key: Optional[str] = None) -> None:
        api_key_final = api_key or config.DUNE_API_KEY or os.getenv("DUNE_API_KEY", "")
        if not api_key_final:
            raise RuntimeError("缺少 DUNE_API_KEY，请在 .env 配置或环境变量中设置")
        if DuneClient is None:
            raise RuntimeError("缺少 dune-client 依赖，请先安装：pip install dune-client")
        self.client = DuneClient(api_key=api_key_final)

    def run_saved_query(self, query_id: str, params: Optional[Dict[str, Any]] = None, 
                       max_retries: int = 3, retry_delay: int = 5) -> pd.DataFrame:
        """
        运行已保存的查询，返回 pandas DataFrame。
        如果查询在 Dune 端仍在执行，会自动轮询直至完成。
        
        添加了错误处理和重试机制来应对Dune服务器问题。
        """
        if not query_id:
            raise ValueError("query_id 不能为空。")

        if QueryBase is None:
            raise RuntimeError("dune-client 版本不兼容，缺少 QueryBase。请升级 dune-client")

        query_id_str = str(query_id).strip()
        try:
            query_id_int = int(query_id_str)
        except Exception:
            raise ValueError(f"无效的 Dune 查询ID：{query_id!r}。请在 Dune 查询页面URL中复制纯数字ID，例如 https://dune.com/queries/1234567")

        query = QueryBase(
            name="ETH Top100 Holders Netflow (Last 30d)",
            query_id=query_id_int,
            params=self._to_query_params(params) or [],
        )
        
        # 重试机制
        last_exception = None
        for attempt in range(max_retries):
            try:
                # 直接获取 DataFrame
                result = self.client.run_query_dataframe(query)
                # 标准化列名
                result.columns = [str(c).strip() for c in result.columns]
                return result
                
            except Exception as exc:
                last_exception = exc
                message = str(exc)
                
                # 检查是否是Dune服务器问题
                if any(keyword in message.lower() for keyword in [
                    'failed to fetch', 'core-api', 'connection', 'timeout', 
                    'server error', '503', '502', '504'
                ]):
                    print(f"⚠ Dune服务器问题，尝试 {attempt + 1}/{max_retries}...")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                
                # 其他错误立即抛出
                break
        
        # 所有重试都失败，抛出详细错误
        message = str(last_exception)
        if any(keyword in message.lower() for keyword in [
            'failed to fetch', 'core-api', 'connection', 'timeout'
        ]):
            raise RuntimeError(
                "Dune 服务器暂时不可用。可能原因：\n"
                "- Dune的core-api服务器正在维护或有故障；\n"
                "- 网络连接不稳定；\n"
                "- 建议稍后重试或使用缓存数据。\n"
                f"原始错误：{message}"
            ) from last_exception
        else:
            raise RuntimeError(
                "Dune 查询失败。可能原因：\n"
                "- Query ID 不存在或填写错误；\n"
                "- 查询为私有或你的 API Key 没有权限（需同一账户或将查询设为公开）；\n"
                "- 你的订阅/额度受限（可在账户设置调整）。\n"
                f"原始错误：{message}"
            ) from last_exception

    @staticmethod
    def _to_query_params(params: Optional[Dict[str, Any]]) -> Optional[list]:
        if not params:
            return None
        converted = []
        for key, value in params.items():
            # 统一按文本传参，Dune 内部可再转换
            converted.append(QueryParameter.text_type(name=key, value=str(value)))
        return converted


def fetch_eth_top100_netflow_last30d() -> pd.DataFrame:
    """
    拉取 Dune 上保存的“ETH前100地址 近30天逐日净买入 量/金额”结果。
    需要先在 .env 设置：
    - DUNE_API_KEY
    - DUNE_QUERY_ID_ETH_TOP100_NETFLOW
    返回列建议：
    - address: 钱包地址
    - day: 日期 (YYYY-MM-DD)
    - net_volume_eth: 当日净买入量(ETH)
    - net_amount_usd: 当日净买入金额(USD)
    - balance_eth: 当前ETH余额（可选）
    """
    query_id = config.DUNE_QUERY_ID_ETH_TOP100_NETFLOW
    runner = DuneQueryRunner()
    df = runner.run_saved_query(query_id=query_id)

    # 尝试统一列名（兼容不同SQL命名）
    rename_map = {
        "wallet": "address",
        "addr": "address",
        "date": "day",
        "tx_day": "day",
        "net_eth": "net_volume_eth",
        "net_volume": "net_volume_eth",
        "net_usd": "net_amount_usd",
        "net_amount": "net_amount_usd",
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    # 类型标准化
    if "day" in df.columns:
        df["day"] = pd.to_datetime(df["day"]).dt.date
    for col in ("net_volume_eth", "net_amount_usd"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_eth_daily_stats_last30d() -> pd.DataFrame:
    """
    拉取 Dune 上保存的"ETH链上过去30天每日交易统计"结果。
    需要先在 .env 设置：
    - DUNE_API_KEY
    - DUNE_QUERY_ID_ETH_DAILY_STATS
    
    返回列：
    - day: 日期 (YYYY-MM-DD)
    - transaction_count: 每日交易数
    - avg_transaction_amount_eth: 平均交易额(ETH)
    - avg_transaction_amount_usd: 平均交易额(USD)
    - active_addresses: 活跃地址数
    
    对应的 Dune SQL 查询：
    
    WITH daily_stats AS (
      SELECT 
        DATE_TRUNC('day', block_time) as day,
        COUNT(*) as transaction_count,
        AVG(value / 1e18) as avg_transaction_amount_eth,
        COUNT(DISTINCT "from") as active_addresses
      FROM ethereum.transactions
      WHERE block_time >= NOW() - INTERVAL '30' DAY
        AND block_time < DATE_TRUNC('day', NOW())
        AND success = true
        AND value > 0  -- 只计算有价值转移的交易
      GROUP BY DATE_TRUNC('day', block_time)
    ),
    price_data AS (
      SELECT 
        DATE_TRUNC('day', minute) as day,
        AVG(price) as eth_price_usd
      FROM prices.usd
      WHERE contract_address = 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2  -- WETH作为ETH价格代理
        AND minute >= NOW() - INTERVAL '30' DAY
        AND minute < DATE_TRUNC('day', NOW())
      GROUP BY DATE_TRUNC('day', minute)
    )
    SELECT 
      ds.day,
      ds.transaction_count,
      ds.avg_transaction_amount_eth,
      ds.avg_transaction_amount_eth * COALESCE(pd.eth_price_usd, 0) as avg_transaction_amount_usd,
      ds.active_addresses
    FROM daily_stats ds
    LEFT JOIN price_data pd ON ds.day = pd.day
    ORDER BY ds.day DESC
    """
    query_id = config.DUNE_QUERY_ID_ETH_DAILY_STATS
    runner = DuneQueryRunner()
    df = runner.run_saved_query(query_id=query_id)

    # 类型标准化
    if "day" in df.columns:
        df["day"] = pd.to_datetime(df["day"]).dt.date
    
    numeric_cols = ["transaction_count", "avg_transaction_amount_eth", 
                   "avg_transaction_amount_usd", "active_addresses"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_eth_monthly_transaction_count_last12m() -> pd.DataFrame:
    """
    拉取 Dune 上保存的"ETH链上过去12个月每月交易数量"结果。
    需要先在 .env 设置：
    - DUNE_API_KEY
    - DUNE_QUERY_ID_ETH_MONTHLY_TX_COUNT
    
    返回列：
    - month: 月份 (YYYY-MM-01)
    - transaction_count: 当月交易总数
    - avg_daily_transactions: 当月日均交易数
    
    对应的 Dune SQL 查询：
    
    WITH monthly_stats AS (
      SELECT 
        DATE_TRUNC('month', block_time) as month,
        COUNT(*) as transaction_count,
        COUNT(*) / EXTRACT(DAY FROM (DATE_TRUNC('month', block_time) + INTERVAL '1' MONTH - INTERVAL '1' DAY)) as avg_daily_transactions
      FROM ethereum.transactions
      WHERE block_time >= DATE_TRUNC('month', NOW() - INTERVAL '12' MONTH)
        AND block_time < DATE_TRUNC('month', NOW())
        AND success = true
      GROUP BY DATE_TRUNC('month', block_time)
    )
    SELECT 
      month,
      transaction_count,
      ROUND(avg_daily_transactions, 0) as avg_daily_transactions
    FROM monthly_stats
    ORDER BY month DESC
    """
    query_id = config.DUNE_QUERY_ID_ETH_MONTHLY_TX_COUNT
    runner = DuneQueryRunner()
    df = runner.run_saved_query(query_id=query_id)

    # 类型标准化
    if "month" in df.columns:
        df["month"] = pd.to_datetime(df["month"]).dt.date
    
    numeric_cols = ["transaction_count", "avg_daily_transactions"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_eth_cex_daily_inout_last30d() -> pd.DataFrame:
    """
    拉取 Dune 上保存的"ETH在中心化交易所过去30天每日转入转出金额"结果。
    需要先在 .env 设置：
    - DUNE_API_KEY
    - DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT
    
    返回列：
    - day: 日期 (YYYY-MM-DD)
    - exchange_name: 交易所名称
    - inflow_eth: 转入金额(ETH)
    - outflow_eth: 转出金额(ETH)
    - net_flow_eth: 净流入金额(ETH，正数为净流入，负数为净流出)
    - inflow_usd: 转入金额(USD)
    - outflow_usd: 转出金额(USD)
    - net_flow_usd: 净流入金额(USD)
    
    对应的 Dune SQL 查询：
    
    -- ETH 在中心化交易所的转入转出统计（过去30天逐日）
    WITH cex_addresses AS (
      SELECT DISTINCT 
        address,
        name as exchange_name
      FROM labels.centralized_exchanges
      WHERE blockchain = 'ethereum'
        AND category = 'cex'
    ),
    
    eth_transfers AS (
      SELECT 
        DATE_TRUNC('day', evt_block_time) as day,
        "to" as to_address,
        "from" as from_address,
        value / 1e18 as eth_amount,
        evt_block_time
      FROM erc20_ethereum.evt_Transfer t
      WHERE contract_address = 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2  -- WETH
        AND evt_block_time >= NOW() - INTERVAL '30' DAY
        AND evt_block_time < DATE_TRUNC('day', NOW())
        AND value > 0
      
      UNION ALL
      
      -- 原生ETH转账
      SELECT 
        DATE_TRUNC('day', block_time) as day,
        "to" as to_address,
        "from" as from_address,
        value / 1e18 as eth_amount,
        block_time as evt_block_time
      FROM ethereum.transactions
      WHERE block_time >= NOW() - INTERVAL '30' DAY
        AND block_time < DATE_TRUNC('day', NOW())
        AND success = true
        AND value > 0
    ),
    
    cex_inflows AS (
      SELECT 
        et.day,
        cex.exchange_name,
        SUM(et.eth_amount) as inflow_eth
      FROM eth_transfers et
      INNER JOIN cex_addresses cex ON et.to_address = cex.address
      GROUP BY et.day, cex.exchange_name
    ),
    
    cex_outflows AS (
      SELECT 
        et.day,
        cex.exchange_name,
        SUM(et.eth_amount) as outflow_eth
      FROM eth_transfers et
      INNER JOIN cex_addresses cex ON et.from_address = cex.address
      GROUP BY et.day, cex.exchange_name
    ),
    
    price_data AS (
      SELECT 
        DATE_TRUNC('day', minute) as day,
        AVG(price) as eth_price_usd
      FROM prices.usd
      WHERE contract_address = 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2  -- WETH作为ETH价格代理
        AND minute >= NOW() - INTERVAL '30' DAY
        AND minute < DATE_TRUNC('day', NOW())
      GROUP BY DATE_TRUNC('day', minute)
    ),
    
    combined_flows AS (
      SELECT 
        COALESCE(i.day, o.day) as day,
        COALESCE(i.exchange_name, o.exchange_name) as exchange_name,
        COALESCE(i.inflow_eth, 0) as inflow_eth,
        COALESCE(o.outflow_eth, 0) as outflow_eth,
        COALESCE(i.inflow_eth, 0) - COALESCE(o.outflow_eth, 0) as net_flow_eth
      FROM cex_inflows i
      FULL OUTER JOIN cex_outflows o 
        ON i.day = o.day AND i.exchange_name = o.exchange_name
    )
    
    SELECT 
      cf.day,
      cf.exchange_name,
      cf.inflow_eth,
      cf.outflow_eth,
      cf.net_flow_eth,
      cf.inflow_eth * COALESCE(pd.eth_price_usd, 0) as inflow_usd,
      cf.outflow_eth * COALESCE(pd.eth_price_usd, 0) as outflow_usd,
      cf.net_flow_eth * COALESCE(pd.eth_price_usd, 0) as net_flow_usd
    FROM combined_flows cf
    LEFT JOIN price_data pd ON cf.day = pd.day
    WHERE cf.inflow_eth > 0 OR cf.outflow_eth > 0  -- 过滤掉没有交易的记录
    ORDER BY cf.day DESC, cf.exchange_name
    """
    query_id = config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT
    runner = DuneQueryRunner()
    df = runner.run_saved_query(query_id=query_id)

    # 类型标准化
    if "day" in df.columns:
        df["day"] = pd.to_datetime(df["day"]).dt.date
    
    numeric_cols = ["inflow_eth", "outflow_eth", "net_flow_eth", 
                   "inflow_usd", "outflow_usd", "net_flow_usd"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
