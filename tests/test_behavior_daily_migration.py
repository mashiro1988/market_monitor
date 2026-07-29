# -*- coding: utf-8 -*-
"""behavior_daily_summaries 北京日口径迁移（2026-07-29）：改名 + 加口径列 + 重建索引 + 幂等。"""
import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.fixture()
def legacy_engine(tmp_path):
    """造一张切换前的旧结构表：有 utc_date、无 date_basis、三列唯一索引、两行存量。"""
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE behavior_daily_summaries (
                id INTEGER NOT NULL PRIMARY KEY,
                symbol VARCHAR(30) NOT NULL,
                utc_date VARCHAR(10) NOT NULL,
                day_type VARCHAR(10) NOT NULL,
                counts TEXT NOT NULL,
                composition TEXT NOT NULL,
                down_net_sum FLOAT,
                computed_at DATETIME NOT NULL
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX ix_behavior_daily_pit "
            "ON behavior_daily_summaries (symbol, utc_date, computed_at)"
        ))
        for d, at in (("2026-07-08", "2026-07-09 00:05:00"), ("2026-07-09", "2026-07-10 00:05:00")):
            conn.execute(text(
                "INSERT INTO behavior_daily_summaries "
                "(symbol, utc_date, day_type, counts, composition, down_net_sum, computed_at) "
                "VALUES ('BTC/USDT', :d, 'weekday', '{}', '{}', -3.87, :at)"
            ), {"d": d, "at": at})
    return eng


def test_migration_renames_column_and_marks_legacy_rows_utc(legacy_engine):
    from database import migrate_behavior_daily_basis

    with legacy_engine.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is True

    with legacy_engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("behavior_daily_summaries")}
        assert "bucket_date" in cols and "utc_date" not in cols
        assert "date_basis" in cols
        rows = conn.execute(text(
            "SELECT bucket_date, date_basis, down_net_sum FROM behavior_daily_summaries "
            "ORDER BY bucket_date"
        )).all()
        assert [r.bucket_date for r in rows] == ["2026-07-08", "2026-07-09"]
        assert {r.date_basis for r in rows} == {"utc"}      # 存量一律标旧口径
        assert rows[0].down_net_sum == -3.87                # 其余字段不动


def test_migration_rebuilds_unique_index_with_basis(legacy_engine):
    from database import migrate_behavior_daily_basis

    with legacy_engine.begin() as conn:
        migrate_behavior_daily_basis(conn)

    with legacy_engine.connect() as conn:
        pit = next(i for i in inspect(conn).get_indexes("behavior_daily_summaries")
                   if i["name"] == "ix_behavior_daily_pit")
        assert list(pit["column_names"]) == ["symbol", "bucket_date", "date_basis", "computed_at"]
        assert pit["unique"]


def test_migration_is_idempotent(legacy_engine):
    from database import migrate_behavior_daily_basis

    with legacy_engine.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is True
    with legacy_engine.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is False   # 第二次全 no-op
    with legacy_engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM behavior_daily_summaries")).scalar()
        assert n == 2                                         # 没重复、没丢行


def test_migration_skips_when_table_absent(tmp_path):
    from database import migrate_behavior_daily_basis

    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with eng.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is False
