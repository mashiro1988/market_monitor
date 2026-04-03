"""
新闻条目模型 - 多源新闻聚合存储
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index
from datetime import datetime
from database import Base


class NewsItem(Base):
    """多源新闻条目表"""
    __tablename__ = "news_items"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    source = Column(String(50), nullable=False)         # wallstreetcn, jin10, coindesk_rss, etc.
    source_id = Column(String(100), nullable=True)      # 源端原始ID，用于源内去重
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=True)
    url = Column(String(500), nullable=True)
    importance = Column(Integer, nullable=True)          # 0-10 重要性评分
    language = Column(String(5), nullable=False, default="zh")  # zh, en
    categories = Column(String(200), nullable=True)     # 逗号分隔的标签
    content_hash = Column(String(64), nullable=True)    # SHA256 标题哈希，用于跨源去重
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_news_source_id", "source", "source_id", unique=True),
        Index("ix_news_content_hash", "content_hash"),
        Index("ix_news_timestamp", "timestamp"),
    )
