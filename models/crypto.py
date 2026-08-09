# -*- coding: utf-8 -*-
"""加密线数据模型(web3 二期A design §2/§5)。

news_coins = 新闻实际在讨论哪几个币,由加密打分调用顺手抽取(零额外调用成本)。
**不存"是否可交易"**——交易所上新/下架随时变,冻结成列会过期;可交易性由读侧
拿 Binance symbol 全集现算(与一期"读时派生、挂接表不存业务数值"同一铁律)。

为什么不靠正文全文搜币名:OP/NOT/PEOPLE 这类代码同时是普通英文单词,全文搜
会大量误伤;打分时由模型判断"这条新闻实际在说哪几个币"语义准确得多。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint

from database import Base


class NewsCoin(Base):
    """新闻↔币种对照表(二期B 异动归因的反查地基)。"""
    __tablename__ = "news_coins"

    id = Column(Integer, primary_key=True, index=True)
    news_id = Column(Integer, nullable=False, index=True)
    coin = Column(String(20), nullable=False)      # 归一化大写代码,如 BTC / SOL
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("news_id", "coin", name="uq_news_coin"),
        Index("ix_news_coin_coin", "coin"),        # B 的反查方向:按币找新闻
    )
