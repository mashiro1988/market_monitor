"""
市场概览页 - 按资产类别分组展示所有品种最新价格
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from database import get_session
from models.price import PriceSnapshot
from chart_utils import normalize_prices

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

# 跨资产历史走势对比
st.markdown("### 跨资产历史走势对比")


@st.cache_data(ttl=120)
def load_price_history(hours: int):
    """加载所有品种最近 N 小时的价格序列"""
    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        rows = (
            session.query(PriceSnapshot.timestamp, PriceSnapshot.symbol, PriceSnapshot.name, PriceSnapshot.price)
            .filter(PriceSnapshot.timestamp >= cutoff)
            .order_by(PriceSnapshot.timestamp.asc())
            .all()
        )
        return rows
    finally:
        session.close()


WINDOW_OPTIONS = {"24小时": 24, "3天": 72, "7天": 168}
selected_window = st.selectbox("时间窗口", list(WINDOW_OPTIONS.keys()), index=0, key="chart_window")
hours = WINDOW_OPTIONS[selected_window]

history_rows = load_price_history(hours)

if not history_rows:
    st.info("暂无历史数据，请先采集数据。")
else:
    # 构建 symbol → {timestamps, prices, name} 映射
    series: dict[str, dict] = {}
    for ts, symbol, name, price in history_rows:
        if symbol not in series:
            series[symbol] = {"name": name, "timestamps": [], "prices": []}
        series[symbol]["timestamps"].append(ts)
        series[symbol]["prices"].append(price)

    # 过滤至少有 2 个数据点的品种
    valid_symbols = [sym for sym, d in series.items() if len(d["prices"]) >= 2]

    # 多选框（默认全选）
    labels = {sym: series[sym]["name"] for sym in valid_symbols}
    default_selection = valid_symbols[:8]  # 默认前8个，避免初次加载太乱
    selected_symbols = st.multiselect(
        "选择品种（可跨资产类别多选）",
        options=valid_symbols,
        default=default_selection,
        format_func=lambda s: f"{labels[s]} ({s})",
        key="chart_symbols",
    )

    if selected_symbols:
        fig = go.Figure()
        for sym in selected_symbols:
            d = series[sym]
            pct_series = normalize_prices(d["prices"])
            fig.add_trace(go.Scatter(
                x=d["timestamps"],
                y=pct_series,
                mode="lines",
                name=f"{d['name']} ({sym})",
                hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
            ))

        fig.update_layout(
            yaxis_title="涨跌幅 (%)",
            xaxis_title="时间 (UTC)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            hovermode="x unified",
            height=420,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("请至少选择一个品种。")

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
