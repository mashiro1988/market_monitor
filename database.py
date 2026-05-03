"""
数据库引擎与会话工厂

模型定义已迁移到 models/ 包中。
为保持向后兼容，从 models 包中重新导出旧模型。
"""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import config

# 创建数据库引擎
engine = create_engine(config.DATABASE_URL, echo=False)

# 启用 WAL 模式以提高并发性能
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def create_tables():
    """创建所有数据表（新旧共存）"""
    # 导入所有模型以确保它们注册到 Base.metadata
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema()


def _ensure_sqlite_schema():
    """SQLite 轻量迁移：补齐 create_all 不会添加的旧表新列。"""
    if not config.DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "news_items" not in table_names:
        return

    existing_columns = {col["name"] for col in inspector.get_columns("news_items")}
    required_columns = {
        "llm_importance": "INTEGER",
        "llm_importance_reason": "TEXT",
        "llm_model": "VARCHAR(80)",
        "llm_scored_at": "DATETIME",
    }

    with engine.begin() as conn:
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                conn.execute(text(f"ALTER TABLE news_items ADD COLUMN {column_name} {column_type}"))
        conn.execute(text("DROP INDEX IF EXISTS ix_news_content_hash"))
        if "ix_news_source_id" in {idx["name"] for idx in inspector.get_indexes("news_items")}:
            conn.execute(text("DROP INDEX IF EXISTS ix_news_source_id"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_source_id ON news_items (source, source_id)"))


def get_session():
    """获取数据库会话"""
    return SessionLocal()


def get_db():
    """获取数据库会话（生成器，用于 FastAPI 依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 向后兼容导出（旧代码可能直接 from database import StockIndex 等）
from models.legacy import StockIndex, BondRate, EconomicData, CryptoData, MarketNews  # noqa: E402, F401
