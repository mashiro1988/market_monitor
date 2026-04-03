"""
链上数据页 - Dune Analytics ETH 链上指标（迁移自旧版 app.py）
"""
import streamlit as st
import plotly.express as px
import config

st.title("链上数据")

try:
    from 市场监控.dune_queries import (
        fetch_eth_top100_netflow_last30d,
        fetch_eth_daily_stats_last30d,
        fetch_eth_cex_daily_inout_last30d,
    )
    dune_available = True
except ImportError:
    dune_available = False


if not dune_available:
    st.warning("Dune 查询模块未找到。")
    st.stop()

if not config.DUNE_API_KEY:
    st.warning("请在 .env 中配置 DUNE_API_KEY")
    st.stop()

tab1, tab2, tab3 = st.tabs(["ETH Top100 净买入", "ETH 每日统计", "ETH CEX 资金流"])

with tab1:
    st.subheader("ETH 前100地址 · 近30天逐日净买入")
    if not config.DUNE_QUERY_ID_ETH_TOP100_NETFLOW:
        st.info("请在 .env 中配置 DUNE_QUERY_ID_ETH_TOP100_NETFLOW")
    else:
        try:
            with st.spinner("查询 Dune..."):
                df = fetch_eth_top100_netflow_last30d()
            if df is not None and not df.empty:
                latest_day = df["day"].max() if "day" in df.columns else None
                if latest_day and "net_amount_usd" in df.columns:
                    day_df = df[df["day"] == latest_day].sort_values("net_amount_usd", ascending=False).head(10)
                    st.markdown(f"**{latest_day} 当日净买入 Top10**")
                    st.dataframe(day_df, use_container_width=True)

                if "day" in df.columns and "net_amount_usd" in df.columns:
                    daily = df.groupby("day", as_index=False)["net_amount_usd"].sum()
                    fig = px.bar(daily, x="day", y="net_amount_usd", title="按日净买入金额(USD)")
                    st.plotly_chart(fig, use_container_width=True)

                with st.expander("查看全部明细"):
                    st.dataframe(df, use_container_width=True)
            else:
                st.info("无数据返回")
        except Exception as e:
            st.error(f"查询失败: {e}")

with tab2:
    st.subheader("ETH 每日交易统计 · 近30天")
    if not config.DUNE_QUERY_ID_ETH_DAILY_STATS:
        st.info("请在 .env 中配置 DUNE_QUERY_ID_ETH_DAILY_STATS")
    else:
        try:
            with st.spinner("查询 Dune..."):
                df = fetch_eth_daily_stats_last30d()
            if df is not None and not df.empty:
                st.dataframe(df, use_container_width=True)
            else:
                st.info("无数据返回")
        except Exception as e:
            st.error(f"查询失败: {e}")

with tab3:
    st.subheader("ETH CEX 每日资金流 · 近30天")
    if not config.DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT:
        st.info("请在 .env 中配置 DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT")
    else:
        try:
            with st.spinner("查询 Dune..."):
                df = fetch_eth_cex_daily_inout_last30d()
            if df is not None and not df.empty:
                st.dataframe(df, use_container_width=True)
            else:
                st.info("无数据返回")
        except Exception as e:
            st.error(f"查询失败: {e}")
