import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from database import Base
import models  # noqa: F401  注册模型

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()

def test_gapfill_anchor_table_created_and_upsertable(session):
    from models.gapfill_anchor import GapfillAnchor
    assert "gapfill_anchor" in inspect(session.get_bind()).get_table_names()
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=datetime(2026,6,26,21,0),
                              real_close=22000.0, perp_price=706.0))
    session.commit()
    row = session.get(GapfillAnchor, "NQ=F")
    assert row.real_close == 22000.0 and row.perp_price == 706.0
    assert row.updated_at is not None   # Task 3 的锚点时效比较依赖此字段在 INSERT 后非 None

def test_fetch_instrument_bars_parses_closed_bars_ascending(monkeypatch):
    from scanners.sources.okx_source import OkxPriceSource, PerpBar
    src = OkxPriceSource.__new__(OkxPriceSource)          # 绕过 __init__（不建真 exchange）
    src.proxy = ""
    monkeypatch.setattr(src, "_make_exchange", lambda: object())
    # OKX candle: [ts(start,ms), o,h,l,c, vol, volCcy, volCcyQuote, confirm]；newest-first
    canned = {
        "QQQ-USDT-SWAP": [
            ["1782700200000","705","707","704","706","10","0","0","1"],   # 较新
            ["1782699900000","704","706","703","705","9","0","0","1"],    # 较旧
        ],
        "XAU-USDT-SWAP": [["1782700200000","4085","4090","4080","4088","1","0","0","1"]],
    }
    monkeypatch.setattr(src, "_fetch_candles",
                        lambda exchange, inst_id, limit=12: canned.get(inst_id, []))
    out = src.fetch_instrument_bars(["QQQ-USDT-SWAP", "XAU-USDT-SWAP"])
    assert isinstance(out["QQQ-USDT-SWAP"][0], PerpBar)
    closes = [b.close for b in out["QQQ-USDT-SWAP"]]
    assert closes == [705.0, 706.0]                       # 升序（旧→新）
    assert out["XAU-USDT-SWAP"][-1].close == 4088.0
    from datetime import datetime, timezone
    expected = datetime.fromtimestamp(1782700200000/1000 + 300, timezone.utc).replace(tzinfo=None)
    assert out["XAU-USDT-SWAP"][-1].bar_end == expected   # 镜像生产 _closed_candle_points 的口径


from datetime import datetime, timedelta
from models.price import PriceSnapshot
from models.gapfill_anchor import GapfillAnchor
from scanners.sources.okx_source import PerpBar

NOW = datetime(2026, 6, 26, 21, 0)   # 周五交易时段

class FakeOkx:
    def __init__(self, bars): self._bars = bars
    def fetch_instrument_bars(self, inst_ids, limit=12): return self._bars

def _real(session, symbol, ts, price, source="yfinance", asset_class="futures", name=None):
    session.add(PriceSnapshot(timestamp=ts, asset_class=asset_class, symbol=symbol,
                              name=name or symbol, price=price, source=source))

def test_live_updates_anchor_no_fill(session, monkeypatch):
    import config
    monkeypatch.setattr(config, "ONCHAIN_GAPFILL", {"NQ=F": {"okx_inst": "QQQ-USDT-SWAP"}})
    _real(session, "NQ=F", NOW, 22000.0)        # 新鲜真实 bar
    session.commit()
    bars = {"QQQ-USDT-SWAP": [PerpBar(bar_end=NOW, close=706.0)]}   # bar_end 对齐
    from scanners.gap_filler import GapFiller
    written = GapFiller().run(session, FakeOkx(bars), NOW + timedelta(minutes=1))
    assert written == 0                          # live 不补
    a = session.get(GapfillAnchor, "NQ=F")
    assert a is not None and a.real_close == 22000.0 and a.perp_price == 706.0

def test_future_dated_real_row_ignored_for_latest(session):
    _real(session, "NQ=F", NOW, 22000.0)
    _real(session, "NQ=F", NOW + timedelta(minutes=10), 99999.0)    # 未来戳 fallback 行
    session.commit()
    from scanners.gap_filler import GapFiller
    real = GapFiller()._latest_real(session, "NQ=F", NOW + timedelta(minutes=1))
    assert real.price == 22000.0                 # 未来戳行不被选为 latest


def _setup_single(monkeypatch):
    import config
    monkeypatch.setattr(config, "ONCHAIN_GAPFILL", {"NQ=F": {"okx_inst": "QQQ-USDT-SWAP"}})

def test_fill_writes_synthetic_at_futures_magnitude(session, monkeypatch):
    _setup_single(monkeypatch)
    fri_close = NOW
    _real(session, "NQ=F", fri_close, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=fri_close, real_close=22000.0, perp_price=706.0))
    session.commit()
    gap_time = fri_close + timedelta(hours=10)
    bars = {"QQQ-USDT-SWAP": [PerpBar(bar_end=gap_time, close=710.0)]}
    from scanners.gap_filler import GapFiller
    written = GapFiller().run(session, FakeOkx(bars), gap_time + timedelta(minutes=1))
    assert written == 1
    syn = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=gap_time).one()
    assert syn.source == "okx_gapfill"
    assert syn.asset_class == "futures"
    assert syn.price == pytest.approx(710.0 * 22000.0 / 706.0, rel=1e-6)

def test_fill_skipped_without_anchor(session, monkeypatch):
    _setup_single(monkeypatch)
    _real(session, "NQ=F", NOW, 22000.0)
    session.commit()
    bars = {"QQQ-USDT-SWAP": [PerpBar(bar_end=NOW + timedelta(hours=10), close=710.0)]}
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(bars), NOW + timedelta(hours=10, minutes=1)) == 0

def test_step_guard_rejects_single_jump(session, monkeypatch):
    _setup_single(monkeypatch)
    fri = NOW
    _real(session, "NQ=F", fri, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=fri, real_close=22000.0, perp_price=706.0))
    t1 = fri + timedelta(hours=10)
    session.add(PriceSnapshot(timestamp=t1, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22124.0, source="okx_gapfill"))
    session.commit()
    t2 = t1 + timedelta(minutes=5)
    bad = {"QQQ-USDT-SWAP": [PerpBar(bar_end=t2, close=800.0)]}   # 单步 +12%
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(bad), t2 + timedelta(minutes=1)) == 0
    assert session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=t2).first() is None

def test_gradual_large_move_not_rejected(session, monkeypatch):
    """周末渐进大行情(由许多正常小步构成)必须被写入，不能被步进防呆误杀。"""
    _setup_single(monkeypatch)
    fri = NOW
    _real(session, "NQ=F", fri, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=fri, real_close=22000.0, perp_price=706.0))
    session.commit()
    from scanners.gap_filler import GapFiller
    gf = GapFiller()
    perp = 706.0
    base_t = fri + timedelta(hours=2)
    written_total = 0
    for i in range(40):                       # 40 步 × +1% ≈ 累计 +48%，但每步仅 +1%
        perp *= 1.01
        t = base_t + timedelta(minutes=5*i)
        written_total += gf.run(session, FakeOkx({"QQQ-USDT-SWAP": [PerpBar(bar_end=t, close=perp)]}), t + timedelta(minutes=1))
    assert written_total == 40                # 全部写入，无一被毙
    last = session.query(PriceSnapshot).filter_by(symbol="NQ=F").order_by(PriceSnapshot.timestamp.desc()).first()
    assert last.price > 22000.0 * 1.4         # 累计大涨被如实补出

def test_perp_stale_skipped(session, monkeypatch):
    _setup_single(monkeypatch)
    _real(session, "NQ=F", NOW, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=NOW, real_close=22000.0, perp_price=706.0))
    session.commit()
    scan = NOW + timedelta(hours=10)
    stale = {"QQQ-USDT-SWAP": [PerpBar(bar_end=scan - timedelta(hours=2), close=710.0)]}  # perp 自身 2h 旧
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(stale), scan) == 0

def test_future_dated_perp_bar_not_used(session, monkeypatch):
    """Part A 回归：未来戳 perp bar 不应被当作 fresh、不应补点。"""
    _setup_single(monkeypatch)
    _real(session, "NQ=F", NOW, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=NOW, real_close=22000.0, perp_price=706.0))
    session.commit()
    scan = NOW + timedelta(hours=10)
    future = {"QQQ-USDT-SWAP": [PerpBar(bar_end=scan + timedelta(minutes=30), close=710.0)]}
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(future), scan) == 0
