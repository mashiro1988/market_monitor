"""本地文件后端（REMOTE_BACKEND=local）单测。

不触网：用 tmp_path 造数据目录 + 缓存目录，monkeypatch 把 remote_fs 的模块级
全局（REMOTE_BACKEND / REMOTE_DATA_ROOT / LOCAL_CACHE_DIR / _manifest）指向 tmp。
这些全局都在函数体内按调用时读取，所以 monkeypatch.setattr 生效。
"""
import pickle
from datetime import datetime

import pandas as pd
import pytest

from services import remote_fs


def test_is_local_backend_reflects_flag(monkeypatch):
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "sftp")
    assert remote_fs._is_local_backend() is False
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "local")
    assert remote_fs._is_local_backend() is True


def test_sftp_rejects_unknown_host_keys_by_default(monkeypatch):
    class _RejectPolicy:
        pass

    class _AutoAddPolicy:
        pass

    class _Paramiko:
        RejectPolicy = _RejectPolicy
        AutoAddPolicy = _AutoAddPolicy

    class _Client:
        def __init__(self):
            self.loaded_system = False
            self.policy = None

        def load_system_host_keys(self):
            self.loaded_system = True

        def load_host_keys(self, path):
            raise AssertionError(path)

        def set_missing_host_key_policy(self, policy):
            self.policy = policy

    monkeypatch.setattr(remote_fs, "paramiko", _Paramiko)
    monkeypatch.delenv("REMOTE_ALLOW_UNKNOWN_HOST", raising=False)
    monkeypatch.delenv("REMOTE_KNOWN_HOSTS", raising=False)
    client = _Client()

    remote_fs._configure_host_key_policy(client)

    assert client.loaded_system is True
    assert isinstance(client.policy, _RejectPolicy)


def test_sftp_unknown_host_auto_add_requires_explicit_opt_in(monkeypatch):
    class _RejectPolicy:
        pass

    class _AutoAddPolicy:
        pass

    class _Paramiko:
        RejectPolicy = _RejectPolicy
        AutoAddPolicy = _AutoAddPolicy

    class _Client:
        def __init__(self):
            self.loaded = None
            self.policy = None

        def load_system_host_keys(self):
            self.loaded = "system"

        def load_host_keys(self, path):
            self.loaded = path

        def set_missing_host_key_policy(self, policy):
            self.policy = policy

    monkeypatch.setattr(remote_fs, "paramiko", _Paramiko)
    monkeypatch.setenv("REMOTE_ALLOW_UNKNOWN_HOST", "1")
    monkeypatch.setenv("REMOTE_KNOWN_HOSTS", "C:\\known_hosts")
    client = _Client()

    remote_fs._configure_host_key_policy(client)

    assert client.loaded == "C:\\known_hosts"
    assert isinstance(client.policy, _AutoAddPolicy)


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


def test_puller_retries_pending_sector_scan_same_cutoff(monkeypatch):
    """pivot 已下载但 sector_scan 失败时，同一个 cutoff 下轮仍要重试下游 scan。"""
    from services import remote_puller

    spec = remote_puller.DatasetSpec(
        name="market_pivot_spot",
        ready_dir="",
        ready_prefix="",
        resolve_pkl_rel=lambda cutoff: "",
        poll_interval_seconds=3600,
    )
    puller = remote_puller.RemotePuller([spec])
    calls = {"pull": 0, "scan": 0}

    def fake_pull_if_newer(_spec):
        calls["pull"] += 1
        with puller._status_lock:
            ds = puller._status.datasets[_spec.name]
            ds.last_cutoff_ts = 1780479000
            ds.last_pull_at = datetime(2026, 6, 1)
            ds.pending_sector_retry_cutoff_ts = 1780479000
        return True

    def fake_sector_scan():
        calls["scan"] += 1
        if calls["scan"] == 1:
            return {"error": "boom"}
        return {"sectors_written": 3}

    monkeypatch.setattr(puller, "_pull_if_newer", fake_pull_if_newer)
    monkeypatch.setattr(puller, "_run_sector_scan", fake_sector_scan)

    first = puller.cycle()
    assert first["sector_scan"] == {"error": "boom"}
    assert calls == {"pull": 1, "scan": 1}
    assert puller.status().datasets["market_pivot_spot"].pending_sector_retry_cutoff_ts == 1780479000

    second = puller.cycle()
    assert second["skipped_not_due"] == ["market_pivot_spot"]
    assert second["sector_scan"] == {"sectors_written": 3}
    assert calls == {"pull": 1, "scan": 2}
    assert puller.status().datasets["market_pivot_spot"].pending_sector_retry_cutoff_ts is None
