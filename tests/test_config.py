"""Configuration helper tests."""

import config


def test_proxy_url_without_port_uses_checked_default_port():
    assert config._normalize_proxy_url("http://127.0.0.1") == "http://127.0.0.1:1080"


def test_proxy_url_with_port_is_kept():
    assert config._normalize_proxy_url("http://127.0.0.1:7897") == "http://127.0.0.1:7897"


def test_proxy_helpers_share_detected_proxy(monkeypatch):
    monkeypatch.setattr(config, "PROXY", "http://127.0.0.1:7897")
    assert config.proxy_url() == "http://127.0.0.1:7897"
    assert config.proxies() == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }


def test_prediction_scan_hourly_and_grace_scales():
    """2026-08-28 事件池合并:预测扫描降频到 1 小时;宽限期随间隔联动(interval×2+30),
    否则小时节奏下单次抓取失败市场就从图上消失一小时。"""
    assert config.SCAN_INTERVALS["prediction"] == 60
    assert config.PREDICTION_ACTIVE_GRACE_MINUTES == config.SCAN_INTERVALS["prediction"] * 2 + 30


def test_prediction_retention_permanent_and_proposal_volume_floor():
    assert config.DATA_RETENTION["prediction_markets_days"] is None
    assert config.POLYMARKET["proposal_min_volume"] == 10_000
