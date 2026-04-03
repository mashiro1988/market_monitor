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

# ============================================================
# API 密钥（全部从 .env 读取）
# ============================================================
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")

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
        "US_10Y": {"source": "fred", "series": "DGS10"},
        "US_2Y": {"source": "fred", "series": "DGS2"},
        # 日债先尝试 yfinance，不可用则后续补充
        "JP_10Y": {"source": "yfinance", "symbol": "^TNX"},
        # JP_2Y yfinance 暂无可靠 ticker，后续补充
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
    "wallstreetcn": {
        "enabled": True,
        "language": "zh",
    },
    "jin10": {
        "enabled": True,
        "language": "zh",
    },
    "coindesk_rss": {
        "enabled": True,
        "language": "en",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "name": "CoinDesk",
    },
    "cointelegraph_rss": {
        "enabled": True,
        "language": "en",
        "url": "https://cointelegraph.com/rss",
        "name": "CoinTelegraph",
    },
    "theblock_rss": {
        "enabled": True,
        "language": "en",
        "url": "https://www.theblock.co/rss.xml",
        "name": "The Block",
    },
    "reuters_rss": {
        "enabled": True,
        "language": "en",
        "url": "https://www.reutersagency.com/feed/",
        "name": "Reuters",
    },
}

# ============================================================
# Polymarket 预测市场配置
# ============================================================
POLYMARKET = {
    "enabled": True,
    "api_url": "https://clob.polymarket.com",
    "gamma_url": "https://gamma-api.polymarket.com",
    # 跟踪的市场标签（用于搜索相关市场）
    "tracked_tags": [
        "fed", "fomc", "interest-rate",
        "geopolitics", "iran", "russia", "china", "war",
        "gdp", "cpi", "inflation", "unemployment", "jobs",
        "election", "trump", "president",
        "tariff", "trade",
        "crypto", "bitcoin", "sec", "etf",
        "recession",
    ],
    # 手动指定的市场 slug（优先跟踪）
    "tracked_slugs": [],
}

# ============================================================
# 告警规则默认配置
# ============================================================
ALERT_RULES = [
    {
        "name": "btc_price_spike",
        "rule_type": "price_change",
        "params": {"symbol": "BTC/USDT", "threshold_pct": 3.0, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 30,
        "enabled": True,
    },
    {
        "name": "eth_price_spike",
        "rule_type": "price_change",
        "params": {"symbol": "ETH/USDT", "threshold_pct": 5.0, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 30,
        "enabled": True,
    },
    {
        "name": "us_futures_spike",
        "rule_type": "price_change",
        "params": {"symbol": "ES=F", "threshold_pct": 2.0, "window_minutes": 15},
        "channels": ["wechat_work"],
        "cooldown_minutes": 30,
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
        "params": {"threshold_pct": 5.0, "window_minutes": 30},
        "channels": ["wechat_work"],
        "cooldown_minutes": 30,
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
# Streamlit 配置
# ============================================================
STREAMLIT_CONFIG = {
    "page_title": "Investment Agent",
    "page_icon": "📊",
    "layout": "wide",
    "initial_sidebar_state": "expanded",
}

# ============================================================
# 旧版兼容（供旧代码引用）
# ============================================================
DATA_SOURCES = {
    "stock_symbols": PRICE_SOURCES["us_indices"],
    "bond_symbols": {"US_10Y": "^UST10Y", "US_2Y": "^UST2Y"},
    "crypto_symbols": PRICE_SOURCES["crypto"],
    "fred_indicators": {"CPI": "CPIAUCSL", "失业率": "UNRATE", "GDP": "GDP"},
}

UPDATE_SCHEDULE = {
    "stock_indices": "0 9 * * *",
    "economic_data": "0 9 * * *",
    "crypto_data": "0 9 * * *",
    "bond_rates": "0 9 * * *",
    "market_news": "0 9 * * *",
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
