# -*- coding: utf-8 -*-
"""价格行为引擎 config 参数块 sanity（price-behavior-engine-plan Task 1）。"""
import config


def test_behavior_tiers_shape():
    # BTC 三档 = 既定阶梯（0.3 计数基档 / 0.5 构成起点=生产现值 / 0.8 重拳档）
    assert config.BEHAVIOR_TIERS["BTC/USDT"] == [0.3, 0.5, 0.8]
    for sym, tiers in config.BEHAVIOR_TIERS.items():
        if tiers is None:  # None = 未校准 → 整体禁用
            continue
        assert len(tiers) == 3, sym
        assert tiers[0] < tiers[1] < tiers[2], sym
        assert all(t > 0 for t in tiers), sym


def test_behavior_ref_symbols_consistent():
    # 参照清单里的 symbol 必须在 BEHAVIOR_TIERS 里有条目（可为 None=禁用）
    for sym in config.BEHAVIOR_REF_SYMBOLS:
        assert sym in config.BEHAVIOR_TIERS, sym
    # 标注主品种不进参照清单
    assert "BTC/USDT" not in config.BEHAVIOR_REF_SYMBOLS
    # 参照清单与标注页对标清单同源（除 BTC 本身）
    ann_syms = {t[0] for t in config.ANNOTATION_REFERENCE_ASSETS} - {"BTC/USDT"}
    assert set(config.BEHAVIOR_REF_SYMBOLS) == ann_syms


def test_behavior_cutoffs():
    assert 0 < config.BEHAVIOR_S_MID < config.BEHAVIOR_S_HI <= 1
    assert config.BEHAVIOR_ESS_THIN > 0
    assert 0 < config.BEHAVIOR_COVERAGE_MIN <= 1
    assert config.BEHAVIOR_ROLLING_POINTS >= 10
    assert config.BEHAVIOR_REPLACES_ANNOTATION_WINDOWS is False  # 默认关
    assert set(config.BEHAVIOR_NEWS_MAGNITUDES) <= set(config.NEWS_MAGNITUDE_TIERS)
    assert config.BEHAVIOR_NEWS_WINDOW_MIN > 0


def test_retention_extended_for_baseline():
    # spec 拍板：60-90 天（S 校准/回放需要）；注意仓库目前无清理 job，此值纯声明
    assert config.DATA_RETENTION["price_snapshots_days"] >= 90
