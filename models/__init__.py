"""
ORM 模型包 - 导出所有模型
"""
from models.price import PriceSnapshot
from models.news import NewsItem, NewsPriceAnnotation
from models.prediction import PredictionMarket
from models.alert_log import AlertLog
from models.tracked_market import TrackedMarket
from models.sector import CmcSymbolCategory, SectorReturn
from models.behavior import BehaviorSegment, BehaviorDailySummary
from models.research import ResearchEvent, ResearchEventLink
from models.event_market import ResearchEventMarket
from models.crypto import NewsCoin


def create_all_tables(engine):
    """创建所有数据表"""
    from database import Base
    Base.metadata.create_all(bind=engine)
