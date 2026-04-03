"""
市场概览页 - 按资产类别分组展示所有品种最新价格
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from database import get_session
from models.price import PriceSnapshot

st.title("市场概览")

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=60_000, key="market_refresh")  # 每60秒刷新
except ImportError:
    pass


@st.cache_data(ttl=120)
def load_latest_prices():
    """加载所有品种的最新价格快照"""
    session = get_session()
    try:
        # 获取最近2小时内的数据
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        snapshots = session.query(PriceSnapshot).filter(
            PriceSnapshot.timestamp >= cutoff,
        ).order_by(PriceSnapshot.timestamp.desc()).all()

        # 按 symbol 去重取最新
        seen = {}
        for s in snapshots:
            if s.symbol not in seen:
                seen[s.symbol] = {
                    "name": s.name,
                    "symbol": s.symbol,
                    "price": s.price,
                    "change_pct": s.change_pct,
                    "volume": s.volume,
                    "asset_class": s.asset_class,
                    "source": s.source,
                    "timestamp": s.timestamp,
                }
        return list(seen.values())
    finally:
        session.close()


prices = load_latest_prices()

if not prices:
    st.info("暂无价格数据。请先运行 `python run.py scan` 或点击侧边栏「执行一次扫描」。")
    st.stop()

# 按资产类别分组
CLASS_ORDER = ["stock_index", "futures", "asian_index", "bond", "commodity", "crypto"]
CLASS_NAMES = {
    "stock_index": "美股指数",
    "futures": "美股期货",
    "asian_index": "亚洲指数",
    "bond": "债券利率",
    "commodity": "商品",
    "crypto": "加密货币",
}

groups = {}
for p in prices:
    groups.setdefault(p["asset_class"], []).append(p)

# 最后更新时间
if prices:
    latest_ts = max(p["timestamp"] for p in prices)
    st.caption(f"最后更新: {latest_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC")

for cls in CLASS_ORDER:
    items = groups.get(cls, [])
    if not items:
        continue

    st.markdown(f"### {CLASS_NAMES.get(cls, cls)}")

    # 自适应列数
    cols_count = min(len(items), 4)
    rows = [items[i:i + cols_count] for i in range(0, len(items), cols_count)]

    for row in rows:
        cols = st.columns(len(row))
        for col, item in zip(cols, row):
            with col:
                change = item["change_pct"]
                delta_str = f"{change:+.2f}%" if change is not None else None

                # 对于债券利率，直接显示数值不带$
                if cls == "bond":
                    st.metric(
                        label=item["name"],
                        value=f"{item['price']:.3f}%",
                        delta=delta_str,
                    )
                elif cls == "crypto":
                    st.metric(
                        label=item["name"],
                        value=f"${item['price']:,.2f}",
                        delta=delta_str,
                    )
                else:
                    st.metric(
                        label=item["name"],
                        value=f"{item['price']:,.2f}",
                        delta=delta_str,
                    )

st.markdown("---")

# 详细数据表
with st.expander("查看详细数据表"):
    df = pd.DataFrame(prices)
    if not df.empty:
        df = df.sort_values(["asset_class", "name"])
        st.dataframe(
            df[["asset_class", "name", "symbol", "price", "change_pct", "volume", "source", "timestamp"]],
            use_container_width=True,
        )
