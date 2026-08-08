"""
数据库引擎与会话工厂

模型定义已迁移到 models/ 包中。
为保持向后兼容，从 models 包中重新导出旧模型。
"""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
import config

_IS_SQLITE = config.DATABASE_URL.startswith("sqlite")
_ENGINE_KWARGS = {"connect_args": {"timeout": 15}} if _IS_SQLITE else {}

# 创建数据库引擎
engine = create_engine(config.DATABASE_URL, echo=False, **_ENGINE_KWARGS)

# 启用 WAL 模式以提高并发性能
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if not _IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def create_tables(*, run_migrations: bool = True, seed_defaults: bool = True):
    """创建所有数据表（新旧共存）"""
    # 导入所有模型以确保它们注册到 Base.metadata
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema(run_migrations=run_migrations)
    if seed_defaults:
        seed_tracked_markets()


def _ensure_sqlite_schema(*, run_migrations: bool = True):
    """SQLite 轻量迁移：补齐 create_all 不会添加的旧表新列。"""
    if not _IS_SQLITE:
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    with engine.begin() as conn:
        # behavior_segments：补人工审计列（price-behavior-engine 2026-07-09）。
        if "behavior_segments" in table_names:
            existing = {col["name"] for col in inspector.get_columns("behavior_segments")}
            for column_name, column_type in {
                "human_class": "VARCHAR(30)",
                "human_confirmed_at": "DATETIME",
            }.items():
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE behavior_segments ADD COLUMN {column_name} {column_type}"))

        # news_items：补 LLM 评分列 + 修索引。
        if "news_items" in table_names:
            existing = {col["name"] for col in inspector.get_columns("news_items")}
            for column_name, column_type in {
                "llm_importance": "INTEGER",
                "llm_importance_reason": "TEXT",
                "llm_model": "VARCHAR(80)",
                "llm_scored_at": "DATETIME",
                # 主题台账内容标签（news-impact-engine Phase 1）
                "topic": "VARCHAR(40)",
                "news_direction": "VARCHAR(8)",
                "magnitude_tier": "VARCHAR(2)",
                "traditional_open": "BOOLEAN",
                "tagged_at": "DATETIME",
                "rescore_attempts": "INTEGER",
            }.items():
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE news_items ADD COLUMN {column_name} {column_type}"))
            if run_migrations:
                conn.execute(text("DROP INDEX IF EXISTS ix_news_content_hash"))
            if run_migrations and "ix_news_source_id" in {idx["name"] for idx in inspector.get_indexes("news_items")}:
                conn.execute(text("DROP INDEX IF EXISTS ix_news_source_id"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_source_id ON news_items (source, source_id)"))
            # 研究事件池游标列(补列 + 存量一次性盖章;不能走上面的补列 dict——那条路径不盖章)
            migrate_news_event_cursor(conn)

        # news_price_annotations：补训练数据列（候选集 + LLM 推理 + LLM 摘要 + v2 标签）。
        if "news_price_annotations" in table_names:
            existing = {col["name"] for col in inspector.get_columns("news_price_annotations")}
            for column_name, column_type in {
                "candidate_news_ids": "TEXT",
                "reference_changes": "TEXT",
                "auto_reasoning": "TEXT",
                "auto_summary": "TEXT",
                "news_roles": "TEXT",
                "market_reaction_type": "VARCHAR(40)",
                "confidence": "FLOAT",
                "auto_news_roles": "TEXT",
                "prompt_version": "VARCHAR(40)",
                "eval_set": "BOOLEAN NOT NULL DEFAULT 0",
                "window_class": "VARCHAR(30)",
            }.items():
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE news_price_annotations ADD COLUMN {column_name} {column_type}"))
            # v1 → v2 一次性迁移 + v2.0 枚举升级到 v2.1（均幂等）
            if run_migrations:
                migrate_legacy_annotations(conn)
                # 历史标注的窗口级三类从行为段 human_class 反向回填（幂等，见函数注释）
                backfill_annotation_window_class(conn)

        # tracked_markets：补软删除墓碑列（删除留行，避免 seed 重启把它补种回来）。
        if "tracked_markets" in table_names:
            existing = {col["name"] for col in inspector.get_columns("tracked_markets")}
            if "dismissed" not in existing:
                conn.execute(text("ALTER TABLE tracked_markets ADD COLUMN dismissed BOOLEAN NOT NULL DEFAULT 0"))

        # prediction_markets：补来源跟踪项列（图表按跟踪项软删状态精确过滤；旧快照为 NULL，走断流启发式）。
        if "prediction_markets" in table_names:
            existing = {col["name"] for col in inspector.get_columns("prediction_markets")}
            if "origin" not in existing:
                conn.execute(text("ALTER TABLE prediction_markets ADD COLUMN origin VARCHAR(120)"))

        # sector_returns：补中位数列。均值代表强度，中位数代表板块广度。
        # 2026-08-07 追加 18 个资金流列（net/qv × 四窗口 × 两市场 + 两个覆盖币数）。
        if "sector_returns" in table_names:
            existing = {col["name"] for col in inspector.get_columns("sector_returns")}
            sector_new_columns = {
                "ret_1h_median": "FLOAT",
                "ret_24h_median": "FLOAT",
                "ret_168h_median": "FLOAT",
                "ret_720h_median": "FLOAT",
            }
            for market in ("spot", "swap"):
                for kind in ("net", "qv"):
                    for window in ("1h", "24h", "168h", "720h"):
                        sector_new_columns[f"{market}_{kind}_{window}"] = "FLOAT"
                sector_new_columns[f"{market}_flow_tokens"] = "INTEGER"
            for column_name, column_type in sector_new_columns.items():
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE sector_returns ADD COLUMN {column_name} {column_type}"))

        # behavior_daily_summaries：北京日口径切换（2026-07-29 spec）——列改名 + 口径标记 + 索引重建。
        if "behavior_daily_summaries" in table_names:
            migrate_behavior_daily_basis(conn)


def migrate_behavior_daily_basis(conn) -> bool:
    """behavior_daily_summaries 北京日口径切换（2026-07-29）：
    utc_date 改名 bucket_date + 新增 date_basis（存量标 'utc'）+ 唯一索引重建为四列。
    幂等：已迁移则全部 no-op。返回本次是否发生结构变更。"""
    insp = inspect(conn)
    if "behavior_daily_summaries" not in insp.get_table_names():
        return False
    cols = {col["name"] for col in insp.get_columns("behavior_daily_summaries")}
    changed = False
    if "utc_date" in cols and "bucket_date" not in cols:
        conn.execute(text("ALTER TABLE behavior_daily_summaries RENAME COLUMN utc_date TO bucket_date"))
        changed = True
    if "date_basis" not in cols:
        conn.execute(text("ALTER TABLE behavior_daily_summaries "
                          "ADD COLUMN date_basis VARCHAR(3) NOT NULL DEFAULT 'utc'"))
        changed = True
    want = ["symbol", "bucket_date", "date_basis", "computed_at"]
    pit = next((idx for idx in inspect(conn).get_indexes("behavior_daily_summaries")
                if idx["name"] == "ix_behavior_daily_pit"), None)
    if pit is None or list(pit["column_names"]) != want or not pit.get("unique"):
        conn.execute(text("DROP INDEX IF EXISTS ix_behavior_daily_pit"))
        conn.execute(text("CREATE UNIQUE INDEX ix_behavior_daily_pit ON behavior_daily_summaries "
                          "(symbol, bucket_date, date_basis, computed_at)"))
        changed = True
    return changed


# v2.0 → v2.1 枚举映射（2026-06-11 与用户定稿：角色不分主次、反应类型收敛为驱动源单轴）
_ROLE_UPGRADE = {
    "primary_driver": "driver", "secondary_driver": "driver", "amplifier": "driver",
}
_REACTION_UPGRADE = {
    "fundamental_repricing": "macro_policy", "policy_expectation_shift": "macro_policy",
    "risk_sentiment": "event_driven",
    "liquidity_shock": "no_news_driver", "positioning_squeeze": "no_news_driver",
    "technical_move": "no_news_driver", "emotional_noise": "no_news_driver",
    "no_clear_driver": "no_news_driver",
}
# news-impact-engine Phase 3a：退场角色（综述/解释/矛盾并入 noise = 从 news_roles 移除）。
_RETIRED_ROLES = {"post_hoc_explanation", "contradictory"}


def migrate_news_event_cursor(conn) -> bool:
    """news_items.event_linked_at 补列 + 存量一次性盖章(研究事件池,
    docs/specs/news-research-phase1-event-pool.md §3.3/§13.1)。
    只在缺列那一次盖章:历史新闻默认"已处理出池",要历史靠回扫清游标召回。
    新库 create_all 自带该列不走此分支(且新库无存量),幂等。"""
    from sqlalchemy import inspect as _inspect
    existing = {col["name"] for col in _inspect(conn).get_columns("news_items")}
    if "event_linked_at" in existing:
        return False
    conn.execute(text("ALTER TABLE news_items ADD COLUMN event_linked_at DATETIME"))
    conn.execute(text("UPDATE news_items SET event_linked_at = CURRENT_TIMESTAMP"))
    return True


def migrate_legacy_annotations(conn) -> int:
    """标注标签迁移（docs/specs/annotation-v2.md §2），两步均幂等：

    1. v1 二元勾选行（news_roles IS NULL）：causal_news_ids 全部 → 'driver'；
       no_clear_news=1 → market_reaction_type='no_news_driver'。confidence 保持
       NULL（导出时作为低保真 schema_version=1 标记）。
    2. v2.0 旧枚举行：news_roles 值与 market_reaction_type 按 _ROLE_UPGRADE /
       _REACTION_UPGRADE 升级到 v2.1（新值映射到自身 → 重跑无副作用）。
    返回发生变更的行数。"""
    import json as _json

    changed = 0
    # 步骤 1：v1 → v2.1
    rows = conn.execute(text(
        "SELECT id, causal_news_ids, no_clear_news FROM news_price_annotations WHERE news_roles IS NULL"
    )).fetchall()
    for row in rows:
        try:
            ids = _json.loads(row[1]) if row[1] else []
        except (ValueError, TypeError):
            ids = []
        roles = {str(int(nid)): "driver" for nid in ids}
        reaction = "no_news_driver" if row[2] else None
        conn.execute(
            text("UPDATE news_price_annotations SET news_roles = :roles, "
                 "market_reaction_type = COALESCE(market_reaction_type, :reaction) WHERE id = :id"),
            {"roles": _json.dumps(roles, ensure_ascii=False), "reaction": reaction, "id": row[0]},
        )
        changed += 1

    # 步骤 2：v2.0 枚举 → v2.1
    rows = conn.execute(text(
        "SELECT id, news_roles, market_reaction_type FROM news_price_annotations WHERE news_roles IS NOT NULL"
    )).fetchall()
    for row in rows:
        try:
            roles = _json.loads(row[1]) if row[1] else {}
        except (ValueError, TypeError):
            roles = {}
        new_roles = {k: _ROLE_UPGRADE.get(v, v) for k, v in roles.items()}
        new_reaction = _REACTION_UPGRADE.get(row[2], row[2])
        if new_roles != roles or new_reaction != row[2]:
            conn.execute(
                text("UPDATE news_price_annotations SET news_roles = :roles, "
                     "market_reaction_type = :reaction WHERE id = :id"),
                {"roles": _json.dumps(new_roles, ensure_ascii=False), "reaction": new_reaction, "id": row[0]},
            )
            changed += 1

    # 步骤 3：v2.1 → v3（Phase 3a）：退场角色 post_hoc_explanation / contradictory 移除（归 noise），幂等。
    rows = conn.execute(text(
        "SELECT id, news_roles FROM news_price_annotations WHERE news_roles IS NOT NULL"
    )).fetchall()
    for row in rows:
        try:
            roles = _json.loads(row[1]) if row[1] else {}
        except (ValueError, TypeError):
            roles = {}
        new_roles = {k: v for k, v in roles.items() if v not in _RETIRED_ROLES}
        if new_roles != roles:
            conn.execute(
                text("UPDATE news_price_annotations SET news_roles = :roles WHERE id = :id"),
                {"roles": _json.dumps(new_roles, ensure_ascii=False), "id": row[0]},
            )
            changed += 1
    return changed


def backfill_annotation_window_class(conn) -> int:
    """历史标注补窗口级三类（2026-08-08 加列后一次性回填，幂等）。

    数据来源 = 行为段的 human_class——它本就是保存标注时回写过去的同一个值，这里原路取回。
    匹配规则与 annotation_service._write_back_window_class 保持一致（同 symbol、tier≥0.5 档、
    区间重叠 ≥50% 且以较短一方为分母），避免回填口径与写入口径打架。
    段被回补劈改 / 早于行为段时代的老标注匹配不到 → 保持 NULL（宁缺勿错），返回回填行数。"""
    rows = conn.execute(text(
        "SELECT id, symbol, window_start, window_end FROM news_price_annotations "
        "WHERE window_class IS NULL"
    )).fetchall()
    if not rows:
        return 0

    filled = 0
    for ann_id, symbol, w_start, w_end in rows:
        if not w_start or not w_end:
            continue
        # SQLite 直接比较 DATETIME 文本（ISO 格式，字典序=时间序），无需在 Python 侧转换
        segs = conn.execute(text(
            "SELECT start_dt, end_dt, human_class FROM behavior_segments "
            "WHERE symbol = :sym AND tier_idx >= 1 AND human_class IS NOT NULL "
            "AND start_dt <= :w_end AND end_dt >= :w_start"
        ), {"sym": symbol, "w_start": w_start, "w_end": w_end}).fetchall()
        best, best_ratio = None, 0.0
        for s_start, s_end, human_class in segs:
            s0, s1 = _parse_dt(s_start), _parse_dt(s_end)
            w0, w1 = _parse_dt(w_start), _parse_dt(w_end)
            if None in (s0, s1, w0, w1):
                continue
            overlap = (min(s1, w1) - max(s0, w0)).total_seconds()
            shorter = min((s1 - s0).total_seconds(), (w1 - w0).total_seconds()) or 1.0
            ratio = overlap / shorter
            if ratio > best_ratio:
                best, best_ratio = human_class, ratio
        if best is not None and best_ratio >= 0.5:
            conn.execute(
                text("UPDATE news_price_annotations SET window_class = :wc WHERE id = :id"),
                {"wc": best, "id": ann_id},
            )
            filled += 1
    return filled


def _parse_dt(value):
    """SQLite 取回的时间可能是 datetime 对象或 ISO 字符串，统一成 datetime；无法解析返回 None。"""
    from datetime import datetime as _dt

    if value is None or isinstance(value, _dt):
        return value
    text_value = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return _dt.strptime(text_value, fmt)
        except ValueError:
            continue
    return None


def seed_tracked_markets(session=None, *, slugs: list[str] | None = None, tags: list[str] | None = None):
    """从给定 slug 列表 upsert tracked_markets。已存在的 (kind, identifier) 行跳过，
    不覆盖用户已修改的 enabled / display_name。

    tags 参数仅保留旧调用兼容；tag 自动发现已暂停，不再补种。
    """
    from models.tracked_market import TrackedMarket
    import config

    if slugs is None:
        slugs = list(config.POLYMARKET.get("tracked_slugs", []))
    _ = tags

    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        existing = {
            (row.kind, row.identifier)
            for row in session.query(TrackedMarket.kind, TrackedMarket.identifier).all()
        }
        for slug in slugs:
            slug = (slug or "").strip()
            if slug and ("slug", slug) not in existing:
                session.add(TrackedMarket(kind="slug", identifier=slug, enabled=True))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()


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
