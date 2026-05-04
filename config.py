"""
统一配置文件 - Investment Agent
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 代理配置（自动检测可用性）
# ============================================================
_PROXY_URL = os.getenv("PROXY_URL", "http://127.0.0.1:4780")


def _check_proxy(url: str, timeout: float = 2.0) -> bool:
    """检测代理是否可用"""
    try:
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 1080
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


PROXY_AVAILABLE = _check_proxy(_PROXY_URL)
PROXY = _PROXY_URL if PROXY_AVAILABLE else ""

if not PROXY_AVAILABLE:
    # 代理不可用时清除环境变量，避免库自动使用代理
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)


def proxies() -> dict:
    """返回 requests 风格的 proxies dict；代理不可用时返回空 dict。
    替代过去散落在各源 / 通道里的 `{"http": PROXY, "https": PROXY} if PROXY else {}` 模板。"""
    return {"http": PROXY, "https": PROXY} if PROXY else {}

# ============================================================
# API 密钥（全部从 .env 读取）
# ============================================================
DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BATCH_SIZE = int(os.getenv("DEEPSEEK_BATCH_SIZE", "12"))
DEEPSEEK_CONNECT_TIMEOUT = float(os.getenv("DEEPSEEK_CONNECT_TIMEOUT", "10"))
DEEPSEEK_READ_TIMEOUT = float(os.getenv("DEEPSEEK_READ_TIMEOUT", "45"))
DEEPSEEK_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "1"))

# 企业微信机器人 Webhook
WECHAT_WORK_WEBHOOK = os.getenv("WECHAT_WORK_WEBHOOK", "")

# ============================================================
# 数据库配置
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///market_monitor.db")

# ============================================================
# 扫描频率（分钟）
# ============================================================
SCAN_INTERVALS = {
    "price": 5,
    "news": 5,
    "prediction": 5,
}
SCAN_ROLLING_BACKFILL_INTERVALS = int(os.getenv("SCAN_ROLLING_BACKFILL_INTERVALS", "2"))

# App / scheduler 启动后最多回补的 5m 价格历史小时数。
# 回补按已入库的最新 timestamp 继续，重复 (symbol, timestamp) 会跳过。
PRICE_BACKFILL_MAX_HOURS = int(os.getenv("PRICE_BACKFILL_MAX_HOURS", "72"))

# App / scheduler 启动后最多回补的新闻小时数。
# 回补只用于补齐停机期间缺失的新闻，最多 72 小时，避免重启后拉取过长历史。
NEWS_BACKFILL_MAX_HOURS = int(os.getenv("NEWS_BACKFILL_MAX_HOURS", "72"))
NEWS_BACKFILL_LLM_ENABLED = os.getenv("NEWS_BACKFILL_LLM_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
NEWS_BACKFILL_CATCHUP_ROUNDS = int(os.getenv("NEWS_BACKFILL_CATCHUP_ROUNDS", "4"))

# 市场概览「跨资产历史走势对比」默认品种；企业微信 hourly summary 也复用这份清单。
MARKET_OVERVIEW_DEFAULT_SYMBOLS = [
    "YM=F",       # 道指期货
    "NQ=F",       # 纳指期货
    "000001.SS",  # 上证指数
    "399006.SZ",  # 创业板指
    "^N225",      # 日经指数
    "CL=F",       # 原油
    "GC=F",       # 黄金
    "BTC/USDT",   # BTC
]

# ============================================================
# 价格数据源配置
# ============================================================
PRICE_SOURCES = {
    # 美股指数
    "us_indices": {
        "道琼斯": "^DJI",
        "纳斯达克": "^IXIC",
        "标普500": "^GSPC",
    },
    # 美股期货（盘前盘后关键参考）
    "us_futures": {
        "S&P500期货": "ES=F",
        "纳指期货": "NQ=F",
        "道指期货": "YM=F",
    },
    # 亚洲指数
    "asian_indices": {
        "日经225": "^N225",
        "韩国KOSPI": "^KS11",
        "上证综指": "000001.SS",
        "深证成指": "399001.SZ",
        "创业板指": "399006.SZ",
    },
    # 债券利率
    "bonds": {
        "US_10Y": {"source": "eastmoney", "secid": "171.US10Y", "name": "美国10年期国债收益率"},
        "US_2Y": {"source": "eastmoney", "secid": "171.US2Y", "name": "美国2年期国债收益率"},
        "JP_10Y": {"source": "eastmoney", "secid": "171.JP10Y", "name": "日本10年期国债"},
        "JP_2Y": {"source": "eastmoney", "secid": "171.JP2Y", "name": "日本2年期国债"},
    },
    # 商品
    "commodities": {
        "WTI原油": "CL=F",
        "黄金": "GC=F",
        "白银": "SI=F",
    },
    # 加密货币
    "crypto": {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "FET": "FETUSDT",
        "TAO": "TAOUSDT",
        "RNDR": "RENDERUSDT",
        "WLD": "WLDUSDT",
        "UNI": "UNIUSDT",
        "ONDO": "ONDOUSDT",
        "PENDLE": "PENDLEUSDT",
        "1INCH": "1INCHUSDT",
        "DOGE": "DOGEUSDT",
        "XRP": "XRPUSDT",
        "SOL": "SOLUSDT",
        "DOT": "DOTUSDT",
        "LINK": "LINKUSDT",
        "CFX": "CFXUSDT",
        "ENS": "ENSUSDT",
        "AR": "ARUSDT",
        "FIL": "FILUSDT",
        "ARB": "ARBUSDT",
        "OP": "OPUSDT",
    },
}

# ============================================================
# 新闻源配置
# ============================================================
NEWS_SOURCES = {
    "jin10": {
        "enabled": True,
        "language": "zh",
    },
    "bloomberg": {
        "enabled": True,
        "type": "rss",
        "language": "en",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "name": "Bloomberg",
    },
}

# ============================================================
# Polymarket 预测市场配置
# ============================================================
POLYMARKET = {
    "enabled": True,
    "api_url": "https://clob.polymarket.com",
    "gamma_url": "https://gamma-api.polymarket.com",
    # tag 仅用于候选发现：Gamma 按 volume 降序取前 discovery_limit 个，再由过滤器筛选
    "tracked_tags": [
        "fed", "fomc", "interest-rate",
        "inflation", "cpi",
        "geopolitics", "iran", "middle-east", "oil", "shipping", "hormuz",
    ],
    "discovery_limit": 5,
    "min_volume": 100_000,
    # 手动指定的 market/event slug（优先跟踪；event slug 会展开为其 markets；无效 slug 静默忽略）
    # market 验证: https://gamma-api.polymarket.com/markets?slug=<slug>
    # event 验证: https://gamma-api.polymarket.com/events/slug/<slug>
    "tracked_slugs": [
        # Fed / 利率
        "how-many-fed-rate-cuts-in-2026",
        "fed-decision-in-june-825",
        "fed-rate-cut-by-629",
        "what-will-the-fed-rate-be-at-the-end-of-2026",
        # US inflation
        "how-high-will-inflation-get-in-2026",
        # Strait of Hormuz / shipping normalization
        "strait-of-hormuz-traffic-returns-to-normal-by-april-30",
        "strait-of-hormuz-traffic-returns-to-normal-by-may-15",
        "strait-of-hormuz-traffic-returns-to-normal-by-end-of-may",
        "strait-of-hormuz-traffic-returns-to-normal-by-end-of-june",
        "iran-agrees-to-unrestricted-shipping-through-hormuz-in-april",
    ],
}

# ============================================================
# 告警规则默认配置
# ============================================================
ALERT_RULES = [
    {
        "name": "btc_price_spike",
        "rule_type": "price_change",
        "params": {"symbol": "BTC/USDT", "threshold_pct": 0.3, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 0,
        "enabled": True,
    },
    {
        "name": "eth_price_spike",
        "rule_type": "price_change",
        "params": {"symbol": "ETH/USDT", "threshold_pct": 0.5, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 0,
        "enabled": True,
    },
    {
        "name": "us_futures_spike",
        "rule_type": "price_change",
        "params": {"symbol": "NQ=F", "threshold_pct": 0.3, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 0,
        "enabled": True,
    },
    {
        "name": "important_news",
        "rule_type": "news_importance",
        "params": {"min_importance": 8},
        "channels": ["wechat_work"],
        "cooldown_minutes": 5,
        "enabled": True,
    },
    {
        "name": "prediction_shift",
        "rule_type": "prediction_shift",
        "params": {"threshold_pct": 5.0, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 0,
        "enabled": True,
    },
    {
        "name": "hourly_summary",
        "rule_type": "hourly_summary",
        "params": {},
        "channels": ["wechat_work"],
        "cooldown_minutes": 55,
        "enabled": True,
    },
]

# ============================================================
# Dune Analytics 配置（保留）
# ============================================================
DUNE_QUERY_ID_ETH_TOP100_NETFLOW = os.getenv("DUNE_QUERY_ID_ETH_TOP100_NETFLOW", "")
DUNE_QUERY_ID_ETH_DAILY_STATS = os.getenv("DUNE_QUERY_ID_ETH_DAILY_STATS", "")
DUNE_QUERY_ID_ETH_MONTHLY_TX_COUNT = os.getenv("DUNE_QUERY_ID_ETH_MONTHLY_TX_COUNT", "")
DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT = os.getenv("DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT", "")

# ============================================================
# 旧版兼容（供旧数据采集代码引用）
# ============================================================
DATA_SOURCES = {
    "stock_symbols": PRICE_SOURCES["us_indices"],
    "bond_symbols": {
        "US_10Y": "171.US10Y",
        "US_2Y": "171.US2Y",
        "JP_10Y": "171.JP10Y",
        "JP_2Y": "171.JP2Y",
    },
    "crypto_symbols": PRICE_SOURCES["crypto"],
}

# ============================================================
# 数据清理配置
# ============================================================
DATA_RETENTION = {
    "price_snapshots_days": 30,     # 5分钟快照保留天数
    "news_items_days": 90,          # 新闻保留天数
    "prediction_markets_days": 30,  # 预测市场快照保留天数
    "alert_logs_days": 90,          # 告警日志保留天数
}
