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
    # —— 主题台账内容标签（news-impact-engine Phase 1，LLM 自动打，不看价格）——
    topic = Column(String(40), nullable=True)            # config.NEWS_TOPICS 之一
    news_direction = Column(String(8), nullable=True)    # 利多 / 利空 / 中性（相对风险资产）
    magnitude_tier = Column(String(2), nullable=True)    # 大 / 中 / 小（a-priori 严重度）
    # 这条新闻发生时传统市场(美式期货)开没开。台账给 NQ 这类品种取数时直接滤掉休市时段的，
    # 免得"休市发的新闻量不到反应"把更早合格反应饿死（见 services/market_calendar.py）。
    traditional_open = Column(Boolean, nullable=True)
    tagged_at = Column(DateTime, nullable=True)          # 打标时间；NULL = 未打标（回灌待处理）
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
    causal_news_ids = Column(Text, nullable=True)        # JSON 数组，元素为 news_items.id；v2 起为派生值 = roles 中 primary+secondary
    candidate_news_ids = Column(Text, nullable=True)     # JSON 数组：标注时整个 context 窗口里的全部候选新闻 ID（含负样本，训练用）
    reference_changes = Column(Text, nullable=True)      # JSON dict：保存标注当时的同期对标特征，训练导出优先使用
    # —— v2 标签（docs/specs/annotation-v2.md；v2.1 枚举见 schemas/annotations.py）——
    news_roles = Column(Text, nullable=True)             # JSON dict {news_id: causal_role}，只存非 noise 条目
    market_reaction_type = Column(String(40), nullable=True)   # macro_policy / event_driven / no_news_driver
    confidence = Column(Float, nullable=True)            # 0-1；旧迁移样本为 NULL（低保真标记）
    auto_news_roles = Column(Text, nullable=True)        # AI 原始标注（人改前快照），人机分歧=难例信号
    prompt_version = Column(String(40), nullable=True)   # 产生 auto_* 的提示词版本
    eval_set = Column(Boolean, nullable=False, default=False)  # 冻结为评估集（训练导出默认排除）
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
