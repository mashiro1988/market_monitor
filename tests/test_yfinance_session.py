"""yfinance 源必须走 curl_cffi 浏览器指纹会话。

Yahoo 对数据中心 IP 按 TLS 指纹限流（YFRateLimitError），用 curl_cffi 的
Chrome 指纹会话可绕过。这里验证 fetch / fetch_history 确实把一个 curl_cffi
Session 传给了 yf.download（不触网，monkeypatch 掉 download）。
"""
from datetime import datetime

import pandas as pd

from scanners.sources import yfinance_source as yfs


def _patch_download(monkeypatch, captured):
    def fake_download(tickers, **kwargs):
        captured["session"] = kwargs.get("session", "MISSING")
        return pd.DataFrame()  # 空 df → 源走"返回空"分支，不碰网络

    monkeypatch.setattr(yfs.yf, "download", fake_download)


def test_fetch_passes_curl_cffi_session(monkeypatch):
    from curl_cffi.requests import Session as CurlSession

    captured = {}
    _patch_download(monkeypatch, captured)

    yfs.YFinancePriceSource().fetch()

    assert isinstance(captured.get("session"), CurlSession), (
        f"fetch 应把 curl_cffi 会话传给 yf.download，实际: {captured.get('session')!r}"
    )


def test_fetch_history_passes_curl_cffi_session(monkeypatch):
    from curl_cffi.requests import Session as CurlSession

    captured = {}
    _patch_download(monkeypatch, captured)

    yfs.YFinancePriceSource().fetch_history(datetime(2026, 6, 1), datetime(2026, 6, 2))

    assert isinstance(captured.get("session"), CurlSession), (
        f"fetch_history 应把 curl_cffi 会话传给 yf.download，实际: {captured.get('session')!r}"
    )
