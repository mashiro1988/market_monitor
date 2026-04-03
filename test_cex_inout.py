"""
测试 ETH 在中心化交易所转入转出查询功能
"""
import os
import sys
sys.path.append('市场监控')

from dune_queries import fetch_eth_cex_daily_inout_last30d, DuneQueryRunner
import config
import pandas as pd

def test_cex_inout_query():
    """测试 CEX 转入转出查询"""
    print("=== 测试 ETH CEX 转入转出查询 ===")
    
    # 检查配置
    if not config.DUNE_API_KEY:
        print("❌ 错误：未配置 DUNE_API_KEY")
        return False
        
    if not config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT:
        print("⚠️  警告：未配置 DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT")
        print("请先在 Dune Analytics 中创建查询并获取 Query ID")
        print("\n建议的 Dune SQL 查询如下：")
        print("""
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
        """)
        return False
    
    try:
        print(f"正在执行查询 ID: {config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT}")
        df = fetch_eth_cex_daily_inout_last30d()
        
        if df.empty:
            print("⚠️  查询返回空数据")
            return False
        
        print(f"✅ 查询成功！返回 {len(df)} 行数据")
        print(f"📊 数据列: {list(df.columns)}")
        
        # 显示数据预览
        print("\n🔍 数据预览（前5行）:")
        print(df.head().to_string(index=False))
        
        # 统计信息
        if 'exchange_name' in df.columns:
            unique_exchanges = df['exchange_name'].nunique()
            print(f"\n📈 统计信息:")
            print(f"- 涉及交易所数量: {unique_exchanges}")
            print(f"- 交易所列表: {sorted(df['exchange_name'].unique())}")
        
        if 'day' in df.columns:
            date_range = df['day'].nunique()
            min_date = df['day'].min()
            max_date = df['day'].max()
            print(f"- 日期范围: {min_date} 到 {max_date} ({date_range} 天)")
        
        # 总计信息
        numeric_cols = ['inflow_eth', 'outflow_eth', 'net_flow_eth', 'inflow_usd', 'outflow_usd', 'net_flow_usd']
        print(f"\n💰 总计信息:")
        for col in numeric_cols:
            if col in df.columns:
                total = df[col].sum()
                print(f"- 总{col.replace('_', ' ')}: {total:,.2f}")
        
        return True
        
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return False

def test_direct_query():
    """测试直接使用 DuneQueryRunner"""
    print("\n=== 测试直接查询功能 ===")
    
    if not config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT:
        print("⚠️  跳过直接查询测试（未配置 Query ID）")
        return False
    
    try:
        runner = DuneQueryRunner()
        df = runner.run_saved_query(query_id=config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT)
        
        print(f"✅ 直接查询成功！返回 {len(df)} 行数据")
        print(f"📊 原始数据列: {list(df.columns)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 直接查询失败: {e}")
        return False

if __name__ == "__main__":
    print("开始测试 ETH CEX 转入转出查询功能...\n")
    
    # 显示当前配置
    print("📋 当前配置:")
    print(f"- DUNE_API_KEY: {'已配置' if config.DUNE_API_KEY else '未配置'}")
    print(f"- DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT: {config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT or '未配置'}")
    print()
    
    # 测试
    success1 = test_cex_inout_query()
    success2 = test_direct_query()
    
    print(f"\n=== 测试结果 ===")
    print(f"CEX 转入转出查询: {'✅ 成功' if success1 else '❌ 失败'}")
    print(f"直接查询功能: {'✅ 成功' if success2 else '❌ 失败'}")
    
    if success1 or success2:
        print("\n🎉 恭喜！查询功能运行正常！")
        print("您可以在应用中使用 fetch_eth_cex_daily_inout_last30d() 函数获取数据。")
    else:
        print("\n💡 请按照上面的说明配置 Dune 查询 ID 后重试。")



