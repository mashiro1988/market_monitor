# -*- coding: utf-8 -*-
"""加密源配置必须与宏观白名单物理隔离。

为什么是硬性测试:标注上下文与自动标注的候选新闻源白名单直接读 NEWS_SOURCES
(services/annotation_service.py::_annotation_news_sources),加密源一旦混进去,
币圈新闻立刻污染已校准的标注池——这是结构性防线,不能靠记性。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def test_crypto_sources_not_in_macro_whitelist():
    assert set(config.CRYPTO_NEWS_SOURCES) & set(config.NEWS_SOURCES) == set()


def test_crypto_sources_shape():
    for key, cfg in config.CRYPTO_NEWS_SOURCES.items():
        assert "enabled" in cfg and "name" in cfg and "language" in cfg, key


def test_blockbeats_defaults():
    bb = config.CRYPTO_NEWS_SOURCES["blockbeats"]
    assert bb["api_url"].startswith("https://api-pro.theblockbeats.info")
    assert bb["page_size"] <= 50            # Pro API 单页上限
    assert bb["lang"] == "cn"


def test_binance_catalogs_are_id_label_pairs():
    for catalog_id, label in config.BINANCE_ANN_CATALOGS:
        assert isinstance(catalog_id, int) and isinstance(label, str) and label
