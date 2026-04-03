"""
预测市场页 - Polymarket 宏观事件概率跟踪
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from database import get_session
from models.prediction import PredictionMarket

st.title("预测市场")

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=120_000, key="pred_refresh")
except ImportError:
    pass


@st.cache_data(ttl=120)
def load_predictions():
    """加载最新的预测市场快照"""
    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        items = session.query(PredictionMarket).filter(
            PredictionMarket.timestamp >= cutoff,
        ).order_by(PredictionMarket.timestamp.desc()).all()

        # 按 (market_id, outcome) 去重取最新
        seen = {}
        for p in items:
            key = f"{p.market_id}:{p.outcome}"
            if key not in seen:
                seen[key] = {
                    "market_id": p.market_id,
                    "question": p.question,
                    "outcome": p.outcome,
                    "probability": p.probability,
                    "prev_probability": p.prev_probability,
                    "volume": p.volume,
                    "timestamp": p.timestamp,
                }
        return list(seen.values())
    finally:
        session.close()


predictions = load_predictions()

if not predictions:
    st.info("暂无预测市场数据。请先运行扫描。")
    st.stop()

# 按 market_id 分组
markets = {}
for p in predictions:
    markets.setdefault(p["market_id"], []).append(p)

# 最后更新时间
latest_ts = max(p["timestamp"] for p in predictions)
st.caption(f"最后更新: {latest_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC")

# 搜索
search = st.text_input("搜索市场", placeholder="输入关键词...")

for market_id, outcomes in markets.items():
    question = outcomes[0]["question"]
    volume = outcomes[0]["volume"] or 0

    if search and search.lower() not in question.lower():
        continue

    # 检查是否有显著变化
    has_shift = any(
        o["prev_probability"] is not None and
        abs(o["probability"] - o["prev_probability"]) >= 0.03
        for o in outcomes
    )
    indicator = " 🔥" if has_shift else ""

    with st.expander(f"{question[:120]}{indicator}", expanded=has_shift):
        cols = st.columns(len(outcomes))
        for col, o in zip(cols, outcomes):
            with col:
                prob = o["probability"]
                prev = o["prev_probability"]

                delta = None
                if prev is not None:
                    shift = (prob - prev) * 100
                    delta = f"{shift:+.1f}%"

                st.metric(
                    label=o["outcome"],
                    value=f"{prob:.1%}",
                    delta=delta,
                )

        if volume > 0:
            st.caption(f"交易量: ${volume:,.0f}")
