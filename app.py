"""
Investment Agent - Streamlit 多页应用入口
"""
import streamlit as st
from database import create_tables
import config

# 页面配置
st.set_page_config(
    page_title=config.STREAMLIT_CONFIG["page_title"],
    page_icon=config.STREAMLIT_CONFIG["page_icon"],
    layout=config.STREAMLIT_CONFIG["layout"],
    initial_sidebar_state=config.STREAMLIT_CONFIG["initial_sidebar_state"],
)

# 全局样式
st.markdown(
    """
    <style>
    html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
        font-family: Arial, "Helvetica Neue", "Segoe UI", "PingFang SC",
                     "Microsoft YaHei", sans-serif;
    }
    [data-testid="stMetricValue"] {font-size: 1.3rem;}
    [data-testid="stMetricDelta"] {font-size: 1.15rem;}
    section[data-testid="stSidebar"] .stButton>button {
        width: 100%; height: 42px; border-radius: 8px; font-weight: 600;
        background-color: #2563eb; color: #fff; border: 1px solid #1e40af;
    }
    section[data-testid="stSidebar"] .stButton>button:hover {
        background-color: #1d4ed8;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 初始化数据库
create_tables()

# 侧边栏
with st.sidebar:
    st.header("Investment Agent")
    st.caption("宏观市场监控 & 告警系统")

    st.markdown("---")

    # 手动触发扫描
    if st.button("执行一次扫描", type="primary"):
        with st.spinner("扫描中..."):
            from run import run_scan_once
            prices, news, preds = run_scan_once()
            st.success(
                f"扫描完成: 价格 {len(prices)} | "
                f"新闻 {len(news)} | 预测 {len(preds)}"
            )
            st.cache_data.clear()

    st.markdown("---")
    st.markdown("**数据源**")
    st.caption(
        "股指/期货/商品: Yahoo Finance\n"
        "加密货币: Binance (ccxt)\n"
        "美债利率: FRED API\n"
        "新闻: 华尔街见闻 / 金十 / RSS\n"
        "预测市场: Polymarket"
    )

# 首页内容
st.title("Investment Agent")
st.markdown("宏观驱动的加密货币投资监控系统")
st.markdown("---")

st.markdown("""
### 导航
- **市场概览** — 全品种价格一览（美股、期货、亚洲、债券、商品、加密）
- **新闻快讯** — 中英文多源新闻聚合
- **预测市场** — Polymarket 宏观事件概率跟踪
- **链上数据** — Dune Analytics ETH 链上指标
- **告警设置** — 告警规则管理与历史记录

使用左侧导航栏切换页面，或运行 `python run.py schedule` 启动后台 5 分钟自动扫描。
""")

# 快速状态
from database import get_session
from models.price import PriceSnapshot
from models.news import NewsItem
from datetime import datetime, timedelta, timezone

session = get_session()
try:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    one_hour = now - timedelta(hours=1)

    price_count = session.query(PriceSnapshot).filter(PriceSnapshot.timestamp >= one_hour).count()
    news_count = session.query(NewsItem).filter(NewsItem.timestamp >= one_hour).count()

    c1, c2, c3 = st.columns(3)
    c1.metric("过去1h价格快照", price_count)
    c2.metric("过去1h新闻条目", news_count)
    c3.metric("扫描频率", f"{config.SCAN_INTERVALS.get('price', 5)} 分钟")
finally:
    session.close()
