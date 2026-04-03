"""
新闻快讯页 - 多源新闻聚合展示
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from database import get_session
from models.news import NewsItem

st.title("新闻快讯")

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=120_000, key="news_refresh")  # 每2分钟刷新
except ImportError:
    pass

# 筛选器
col1, col2, col3 = st.columns(3)
with col1:
    source_filter = st.multiselect(
        "新闻源",
        ["wallstreetcn", "jin10", "coindesk_rss", "cointelegraph_rss", "theblock_rss", "reuters_rss"],
        default=[],
        help="留空则显示所有源",
    )
with col2:
    lang_filter = st.selectbox("语言", ["全部", "中文", "英文"])
with col3:
    hours_back = st.slider("回溯时长(小时)", 1, 72, 24)


@st.cache_data(ttl=120)
def load_news(hours: int):
    """加载最近N小时的新闻"""
    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        items = session.query(NewsItem).filter(
            NewsItem.timestamp >= cutoff,
        ).order_by(NewsItem.timestamp.desc()).limit(500).all()

        return [{
            "timestamp": n.timestamp,
            "source": n.source,
            "title": n.title,
            "content": n.content,
            "url": n.url,
            "importance": n.importance,
            "language": n.language,
            "categories": n.categories,
        } for n in items]
    finally:
        session.close()


news = load_news(hours_back)

if not news:
    st.info("暂无新闻数据。")
    st.stop()

# 应用筛选
filtered = news
if source_filter:
    filtered = [n for n in filtered if n["source"] in source_filter]
if lang_filter == "中文":
    filtered = [n for n in filtered if n["language"] == "zh"]
elif lang_filter == "英文":
    filtered = [n for n in filtered if n["language"] == "en"]

st.caption(f"共 {len(filtered)} 条新闻（过去 {hours_back} 小时）")

# 搜索框
search = st.text_input("搜索关键词", placeholder="输入关键词筛选新闻...")
if search:
    search_lower = search.lower()
    filtered = [n for n in filtered if search_lower in (n["title"] or "").lower()
                or search_lower in (n["content"] or "").lower()]
    st.caption(f"搜索结果: {len(filtered)} 条")

st.markdown("---")

# 新闻列表
for n in filtered[:100]:
    ts = n["timestamp"]
    source_tag = n["source"]
    importance = n["importance"] or 0

    # 重要新闻用不同颜色
    if importance >= 8:
        prefix = "🔴"
    elif importance >= 5:
        prefix = "🟡"
    else:
        prefix = "⚪"

    title = n["title"]
    content = n["content"] or ""
    url = n["url"]

    header = f"{prefix} **[{source_tag}]** {ts:%H:%M} · {title}"

    with st.expander(header, expanded=False):
        if content:
            st.write(content[:1000])
        if url:
            st.markdown(f"[原文链接]({url})")
        if n["categories"]:
            st.caption(f"分类: {n['categories']}")
