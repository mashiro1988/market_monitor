"""
新闻条目模型 - 多源新闻聚合存储
"""
from sqlalchemy import Boolean, Column, Float, Integer, String, DateTime, Text, Index
from datetime import datetime
from database import Base


class NewsItem(Base):
    """新闻条目表"""
    __tablename__ = "news_items"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    source = Column(String(50), nullable=False)         # 取值见 config.NEWS_SOURCES（如 jin10、cnbc）
    source_id = Column(String(100), nullable=True)      # 源端原始ID，用于告警标记和追踪
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=True)
    url = Column(String(500), nullable=True)
    importance = Column(Integer, nullable=True)          # 源端重要标志；Jin10 important 映射为 1/0
    llm_importance = Column(Integer, nullable=True)      # DeepSeek V4 价格波动重要性评分，1-10
    llm_importance_reason = Column(Text, nullable=True)
    llm_model = Column(String(80), nullable=True)
    llm_scored_at = Column(DateTime, nullable=True)
    language = Column(String(5), nullable=False, default="zh")  # zh, en
    categories = Column(String(200), nullable=True)     # 逗号分隔的标签
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_news_source_id", "source", "source_id"),
        Index("ix_news_timestamp", "timestamp"),
    )


class NewsPriceAnnotation(Base):
    """价格异动窗口与候选新闻的人工标注结果。"""
    __tablename__ = "news_price_annotations"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), nullable=False)
    asset_class = Column(String(20), nullable=True)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    context_start = Column(DateTime, nullable=False)
    context_end = Column(DateTime, nullable=False)
    threshold_pct = Column(Float, nullable=True)
    price_start = Column(Float, nullable=True)
    price_end = Column(Float, nullable=True)
    change_pct = Column(Float, nullable=True)
    causal_news_ids = Column(Text, nullable=True)        # JSON 数组，元素为 news_items.id（人工最终选定）
    candidate_news_ids = Column(Text, nullable=True)     # JSON 数组：标注时整个 context 窗口里的全部候选新闻 ID（含负样本，训练用）
    auto_reasoning = Column(Text, nullable=True)         # DeepSeek auto-annotate 的 reasoning_content 全文；纯人工标注则 NULL
    auto_summary = Column(Text, nullable=True)           # DeepSeek auto-annotate 的 summary 原文（与人改后的 notes 区分）
    no_clear_news = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    labeler = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_news_annotation_window", "symbol", "window_start", "window_end", unique=True),
        Index("ix_news_annotation_created", "created_at"),
    )
