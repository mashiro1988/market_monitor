"""sector_returns 资金流列的轻量迁移：旧库补列 + 重复跑不炸。"""
from sqlalchemy import create_engine, inspect, text

import database


FLOW_FLOAT_COLUMNS = [
    f"{market}_{kind}_{window}"
    for market in ("spot", "swap")
    for kind in ("net", "qv")
    for window in ("1h", "24h", "168h", "720h")
]
FLOW_INT_COLUMNS = ["spot_flow_tokens", "swap_flow_tokens"]


def _legacy_engine(tmp_path):
    """造一个只有旧列的 sector_returns —— 模拟线上存量库。"""
    engine = create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE sector_returns ("
            " id INTEGER PRIMARY KEY,"
            " snapshot_at DATETIME NOT NULL,"
            " category VARCHAR(120) NOT NULL,"
            " group_name VARCHAR(60),"
            " token_count INTEGER NOT NULL,"
            " ret_1h FLOAT, ret_24h FLOAT, ret_168h FLOAT, ret_720h FLOAT,"
            " created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO sector_returns (snapshot_at, category, token_count, ret_24h)"
            " VALUES ('2026-08-07 10:00:00', 'AI & Big Data', 12, 3.5)"
        ))
    return engine


def test_migration_adds_flow_columns_and_is_idempotent(tmp_path, monkeypatch):
    engine = _legacy_engine(tmp_path)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_IS_SQLITE", True)

    database._ensure_sqlite_schema()
    database._ensure_sqlite_schema()  # 第二次必须 no-op，不能抛

    columns = {c["name"]: c["type"].__class__.__name__.upper()
               for c in inspect(engine).get_columns("sector_returns")}
    for name in FLOW_FLOAT_COLUMNS:
        assert name in columns, f"缺列 {name}"
        assert "FLOAT" in columns[name]
    for name in FLOW_INT_COLUMNS:
        assert name in columns, f"缺列 {name}"
        assert "INTEGER" in columns[name]

    # 存量行还在，且新列为 NULL
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT ret_24h, spot_net_24h, spot_flow_tokens FROM sector_returns"
        )).one()
    assert row[0] == 3.5
    assert row[1] is None
    assert row[2] is None
