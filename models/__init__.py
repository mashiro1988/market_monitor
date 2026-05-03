"""
ORM 模型包 - 导出所有模型
"""
from models.legacy import StockIndex, BondRate, EconomicData, CryptoData, MarketNews
from models.price import PriceSnapshot
from models.news import NewsItem, NewsPriceAnnotation
from models.prediction import PredictionMarket
from models.alert_log import AlertLog


def create_all_tables(engine):
    """创建所有数据表（新旧共存）"""
    from database import Base
    Base.metadata.create_all(bind=engine)
