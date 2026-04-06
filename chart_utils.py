"""图表工具函数"""


def normalize_prices(prices: list[float]) -> list[float]:
    """将价格序列转为相对第一个点的涨跌幅（%）。"""
    if not prices:
        return []
    base = prices[0]
    if base == 0:
        return [0.0] * len(prices)
    return [(p / base - 1) * 100 for p in prices]
