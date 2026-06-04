"""本地文件后端（REMOTE_BACKEND=local）单测。

不触网：用 tmp_path 造数据目录 + 缓存目录，monkeypatch 把 remote_fs 的模块级
全局（REMOTE_BACKEND / REMOTE_DATA_ROOT / LOCAL_CACHE_DIR / _manifest）指向 tmp。
这些全局都在函数体内按调用时读取，所以 monkeypatch.setattr 生效。
"""
import pickle

import pandas as pd
import pytest

from services import remote_fs


def test_is_local_backend_reflects_flag(monkeypatch):
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "sftp")
    assert remote_fs._is_local_backend() is False
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "local")
    assert remote_fs._is_local_backend() is True


@pytest.fixture
def local_backend(tmp_path, monkeypatch):
    """把 remote_fs 切到 local 后端，数据根/缓存指向 tmp。"""
    data_root = tmp_path / "data"
    cache = tmp_path / "cache"
    data_root.mkdir()
    cache.mkdir()
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "local")
    monkeypatch.setattr(remote_fs, "REMOTE_DATA_ROOT", str(data_root) + "/")
    monkeypatch.setattr(remote_fs, "LOCAL_CACHE_DIR", cache)
    monkeypatch.setattr(remote_fs, "_manifest", remote_fs.Manifest(cache))
    return data_root, cache


def test_list_dir_local(local_backend):
    data_root, _ = local_backend
    (data_root / "a.pkl").write_bytes(b"x" * 10)
    (data_root / "b.ready").write_text("ok")
    rows = remote_fs.list_dir(str(data_root))
    names = {n for (n, _s, _m) in rows}
    assert names == {"a.pkl", "b.ready"}
    sizes = {n: s for (n, s, _m) in rows}
    assert sizes["a.pkl"] == 10


def test_stat_remote_local(local_backend):
    data_root, _ = local_backend
    f = data_root / "x.pkl"
    f.write_bytes(b"12345")
    size, mtime = remote_fs.stat_remote(str(f))
    assert size == 5
    assert mtime > 0
    assert remote_fs.stat_remote(str(data_root / "missing.pkl")) is None


def test_pull_local_copies_then_skips(local_backend):
    data_root, cache = local_backend
    rel = "preprocess_1h_resample/30m/market_pivot_spot_2026.pkl"
    src = data_root / rel
    src.parent.mkdir(parents=True)
    df = pd.DataFrame({"BTC": [1.0, 2.0]})
    src.write_bytes(pickle.dumps(df))

    out = remote_fs.pull(rel)
    assert out is not None and out.exists()
    assert out.parent == cache
    pd.testing.assert_frame_equal(remote_fs.load_pkl_as_df(out), df)

    # 源未变 → 命中 manifest，不重复拷贝（mtime 不变）
    first_mtime = out.stat().st_mtime
    assert remote_fs.pull(rel) == out
    assert out.stat().st_mtime == first_mtime


def test_find_latest_ready_local(local_backend):
    data_root, _ = local_backend
    d = data_root / "preprocess_1h_resample" / "30m"
    d.mkdir(parents=True)
    for ts in (1780470000, 1780479000, 1780460000):
        (d / f"market_pivot_spot_{ts}.ready").write_text("x")
    (d / "spot_dict_1780479000.ready").write_text("x")  # 异前缀，须忽略
    assert remote_fs.find_latest_ready(
        "preprocess_1h_resample/30m/", "market_pivot_spot"
    ) == ("market_pivot_spot_1780479000.ready", 1780479000)


def test_puller_pull_if_newer_local(local_backend, monkeypatch):
    from services import remote_puller

    data_root, _ = local_backend
    spec = remote_puller.PHASE1_DATASETS[0]  # market_pivot_spot
    d = data_root / "preprocess_1h_resample" / "30m"
    d.mkdir(parents=True)
    ts = 1780479000  # → year 2026
    (d / f"market_pivot_spot_{ts}.ready").write_text("x")
    (d / "market_pivot_spot_2026.pkl").write_bytes(
        pickle.dumps(pd.DataFrame({"BTC": [1.0]}))
    )
    puller = remote_puller.RemotePuller([spec])
    assert puller._pull_if_newer(spec) is True   # 首次拉到
    assert puller._pull_if_newer(spec) is False  # cutoff 不变 → 跳过
