"""
统一配置文件 - Investment Agent
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 代理配置（自动检测可用性）
# ============================================================
_RAW_PROXY_URL = os.getenv("PROXY_URL", "http://127.0.0.1:4780")


def _normalize_proxy_url(url: str) -> str:
    """PROXY_URL 不带端口时补成与连通性检测一致的 1080 端口。"""
    if not url:
        return ""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname or parsed.port is not None:
        return url

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    return urlunparse(parsed._replace(netloc=f"{userinfo}{host}:1080"))


_PROXY_URL = _normalize_proxy_url(_RAW_PROXY_URL)


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


def proxy_url() -> str:
    """返回单 URL 代理；供 ccxt 等不接受 requests proxies dict 的库使用。"""
    return PROXY

# ============================================================
# API 密钥（全部从 .env 读取）
# ============================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
APP_AUTH_TOKEN = os.getenv("APP_AUTH_TOKEN", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BATCH_SIZE = int(os.getenv("DEEPSEEK_BATCH_SIZE", "12"))
DEEPSEEK_CONNECT_TIMEOUT = float(os.getenv("DEEPSEEK_CONNECT_TIMEOUT", "10"))
DEEPSEEK_READ_TIMEOUT = float(os.getenv("DEEPSEEK_READ_TIMEOUT", "45"))
DEEPSEEK_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "1"))

# v4 pro 推理模型（自动标注用）。thinking 模式对应 reasoning_content，需要更长 read timeout。
DEEPSEEK_REASONER_MODEL = os.getenv("DEEPSEEK_REASONER_MODEL", "deepseek-v4-pro")
DEEPSEEK_REASONER_READ_TIMEOUT = float(os.getenv("DEEPSEEK_REASONER_READ_TIMEOUT", "240"))
# 批量调用一次喂多个窗口，单次思考时间 = 单窗口 × 倍数；read timeout 也要相应放大。
DEEPSEEK_REASONER_BATCH_READ_TIMEOUT = float(os.getenv("DEEPSEEK_REASONER_BATCH_READ_TIMEOUT", "600"))
DEEPSEEK_REASONER_EFFORT = os.getenv("DEEPSEEK_REASONER_EFFORT", "max")  # "high" | "max"

# 企业微信机器人 Webhook
WECHAT_WORK_WEBHOOK = os.getenv("WECHAT_WORK_WEBHOOK", "")

# ============================================================
# 数据库配置
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///market_monitor.db")

# ============================================================
# 日志配置
# ============================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_FILE_ENABLED = os.getenv("LOG_FILE_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "on",
}
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_FILE_NAME = os.getenv("LOG_FILE_NAME", "market_monitor.log")
LOG_ROTATION = os.getenv("LOG_ROTATION", "20 MB")
LOG_RETENTION = os.getenv("LOG_RETENTION", "14 days")
LOG_COMPRESSION = os.getenv("LOG_COMPRESSION", "zip")

# ============================================================
# 扫描频率（分钟）
# ============================================================
SCAN_INTERVALS = {
    "price": 5,
    "news": 5,
    "prediction": 5,
}
SCAN_ROLLING_BACKFILL_INTERVALS = int(os.getenv("SCAN_ROLLING_BACKFILL_INTERVALS", "2"))

# 预测市场图表的「活跃」宽限期（分钟）：最后一笔快照落后于表内最新快照超过该值的市场，
# 视为已停止跟踪（软删除后快照断流），整体从 /predictions 与 families 图表消失。
# 基准取表内最新快照时间而非墙钟，调度器宕机时不会误杀全部市场。
PREDICTION_ACTIVE_GRACE_MINUTES = int(os.getenv("PREDICTION_ACTIVE_GRACE_MINUTES", "30"))

# 「跨资产走势」净值基准：取窗口起始时刻之前最后一笔收盘作基准，向前回看上限（天）。
MARKET_HISTORY_BASELINE_LOOKBACK_DAYS = int(os.getenv("MARKET_HISTORY_BASELINE_LOOKBACK_DAYS", "7"))

# ============================================================
# 新闻影响力引擎 · 主题台账（docs/specs/news-impact-engine-plan.md，Phase 1）
# ============================================================
# 主题分类种子表：LLM 把每条新闻归入其一（消歧后才能跨时间聚合）。"其他" 兜底，定期审。
NEWS_TOPICS = (
    "地缘冲突",       # 战争 / 军事 / 制裁 / 海峡封锁
    "美联储政策",     # FOMC / 官员讲话 / 降息加息预期
    "通胀数据",       # CPI / PCE / PPI
    "就业数据",       # 非农 / 失业 / 工资
    "其他宏观数据",   # GDP / 零售 / PMI / 央行（非美联储）
    "财政与政治",     # 关税 / 财政 / 大选 / 政府事件
    "能源供给",       # OPEC / 原油库存 / 供给冲击
    "加密监管",       # SEC / 立法 / 政策
    "加密生态",       # ETF / 交易所 / 链上 / 项目事件
    "公司财报",       # 财报 / 指引 / 重大公司事件
    "其他",           # 兜底
)
# a-priori 量级（事件本身有多大，看内容不看价格）。rubric 见 news_tagging.py。
NEWS_MAGNITUDE_TIERS = ("大", "中", "小")
# 方向：相对风险资产（BTC/纳指）的应然影响。
NEWS_DIRECTIONS = ("利多", "利空", "中性")

# 标注窗口（news-impact-engine Phase 2）：每品种**单** 15min 档。
# 触发 = 窗口开收净 (末收 − 初开)/初开 ≥ threshold。沿用既有 15min 触发阈值（BTC 0.5 / NQ 0.3）。
# 删了旧的二次 net_min 门槛——故 0.5~旧 net_min 区间的小幅净移动现在也会出窗口，
# 噪音程度由 6/10 夜回放校准（docs/specs/news-impact-engine-phase2-plan.md Task 4）。
# 显式传 threshold/window 的调试路径不走本配置。
ANNOTATION_WINDOW_SCALES = {
    "BTC/USDT": [{"window_minutes": 15, "threshold_pct": 0.5, "pre_minutes": 60}],
    "NQ=F":     [{"window_minutes": 15, "threshold_pct": 0.3, "pre_minutes": 60}],
}

# 标注页「宏观同期对标」清单：(symbol, 中文标签[, 单位])。增减对标资产只改这里。
# symbol 必须是 price_snapshots 里在采的（config 价格源内）。
# 第三项可选 "bp"：收益率类品种按基点显示（+10.0bp = 上行 0.10 个百分点），缺省按涨跌%。
# 七个对标 = 风险资产 / 亚洲风险资产 / 地缘供给 / 避险 / 利率 / 美元流动性 / 加密贝塔。
ANNOTATION_REFERENCE_ASSETS = [
    ("NQ=F", "纳指"),
    ("^N225", "日经225"),
    ("CL=F", "原油"),
    ("GC=F", "黄金"),
    ("US_2Y", "美债2Y", "bp"),
    ("DX-Y.NYB", "美元指数"),
    ("BTC/USDT", "BTC"),
]

# ============================================================
# 价格行为引擎（docs/specs/price-behavior-engine-plan.md；spec = volume-behavior-engine-discussion.md v0.4）
# ============================================================
# 三档阈值阶梯（15min 开收净，%，绝对尺子：标准固定、数量浮动、频率即读数）。
# BTC 0.3/0.5/0.8 = 计数基档 / 构成起点(生产现值) / 重拳档；
# 宏观参照按"稀有度锚定"反解（该资产 15min 变动分布上与 BTC 对应档位同触发率的分位数，2026-07 实测圆整）。
# None = 该参照未校准 → 整体禁用（不出段、不进 S、不上曲线），避免半配置状态；Task 9 校准脚本产出后填、用户拍板。
BEHAVIOR_TIERS: dict[str, list[float] | None] = {
    "BTC/USDT": [0.3, 0.5, 0.8],
    "NQ=F": [0.23, 0.40, 0.69],     # 2026-07-09 服务器30d复核吻合（双锚偏差15.3%贴线，分布形状差异，季度复查）
    "GC=F": [0.23, 0.39, 0.61],     # 复核吻合（偏差9.1%）
    "DX-Y.NYB": [0.043, 0.069, 0.102],  # 复核吻合（偏差7.8%）
    "CL=F": [0.38, 0.63, 0.94],     # 2026-07-09 校准首跑定档（双锚偏差6.0%，6379 bars）
    "^N225": [0.42, 0.68, 1.16],    # 2026-07-09 定档（偏差11.5%；仅JST盘中1364 bars，休市由覆盖闸兜底）
    # US_2Y：2026-07-09 校准首跑 30d 仅 3 个有效 15min 样本——CNBC 债券快照撑不起 5min 严格网格，
    # S 不可用，维持禁用；标注页 reference_changes/三段展示走容差取点、不受影响。数据源修好后再校准。
    "US_2Y": None,
}
# 共振参照资产（有序 = 展示/S 计算顺序）；与 ANNOTATION_REFERENCE_ASSETS 同源，BTC 是主品种不进参照。
BEHAVIOR_REF_SYMBOLS = ["NQ=F", "^N225", "GC=F", "US_2Y", "DX-Y.NYB", "CL=F"]
# 共振分 S 判级 cutoff（回放校准项）：max|S| ≥ HI 共振；MID~HI 弱共振（仅展示证据）；< MID 独立。
BEHAVIOR_S_HI = float(os.getenv("BEHAVIOR_S_HI", "0.5"))
BEHAVIOR_S_MID = float(os.getenv("BEHAVIOR_S_MID", "0.3"))
# ESS（有效样本数 (Σw)²/Σw²）低于此值标"证据薄"——分数靠一两根 bar 撑起，对插针/坏数据敏感。
BEHAVIOR_ESS_THIN = float(os.getenv("BEHAVIOR_ESS_THIN", "5"))
# 大窗口内参照覆盖（按 BTC 权重质量算）低于此比例 → 该参照不出分（休市/缺数 = 分数地基不实 → 无对照）。
BEHAVIOR_COVERAGE_MIN = float(os.getenv("BEHAVIOR_COVERAGE_MIN", "0.5"))
# 新闻命中：段窗 ± 分钟内存在 a-priori 量级 ∈ BEHAVIOR_NEWS_MAGNITUDES 的新闻（内容判，不看价格）。
BEHAVIOR_NEWS_WINDOW_MIN = int(os.getenv("BEHAVIOR_NEWS_WINDOW_MIN", "30"))
BEHAVIOR_NEWS_MAGNITUDES = ("大", "中")
# rolling S 展示曲线窗口点数（2026-07-09 用户定 30 点 ≈ 2.5h）；纯展示——不触发、不分类、不告警。
BEHAVIOR_ROLLING_POINTS = int(os.getenv("BEHAVIOR_ROLLING_POINTS", "30"))
# （Phase 2 退役）BEHAVIOR_REPLACES_ANNOTATION_WINDOWS 开关已删除：标注页固定以 behavior_segments
# 为唯一窗口源（2026-07-09 用户拍板，不再两套窗口口径并行）；显式 threshold/window 调试参数仍走原始扫描。

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
    "DX-Y.NYB",   # 美元指数（ICE 现货指数；Yahoo 已无 DX=F 期货行情）
    "BTC/USDT",   # BTC
    "ETH/USDT",   # ETH
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
        "US_10Y": {"source": "cnbc", "cnbc": "US10Y", "name": "美国10年期国债收益率"},
        "US_2Y": {"source": "cnbc", "cnbc": "US2Y", "name": "美国2年期国债收益率"},
        "JP_10Y": {"source": "cnbc", "cnbc": "JP10Y", "name": "日本10年期国债"},
        "JP_2Y": {"source": "cnbc", "cnbc": "JP2Y", "name": "日本2年期国债"},
    },
    # 商品
    "commodities": {
        "WTI原油": "CL=F",
        "黄金": "GC=F",
        "白银": "SI=F",
    },
    # 美元指数等外汇（yfinance）。注意：Yahoo 已下架 DX=F（期货）行情，必须用 ICE 现货指数 DX-Y.NYB。
    "currencies": {
        "美元指数": "DX-Y.NYB",
    },
    # 加密货币（市场概览只跟 BTC/ETH；如需更多在此添加）
    "crypto": {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
    },
}

# ============================================================
# 休市补点（gap-fill）：休市时段用 OKX 永续代理价补连续点
# 详见 docs/superpowers/specs/2026-06-28-okx-gapfill-market-overview-design.md
# ============================================================
ONCHAIN_GAPFILL = {
    "NQ=F": {"okx_inst": "QQQ-USDT-SWAP"},   # 纳指100：QQQ ETF 永续（同底层指数）
    "CL=F": {"okx_inst": "CL-USDT-SWAP"},    # WTI 原油
    "GC=F": {"okx_inst": "XAU-USDT-SWAP"},   # 现货黄金
}
GAPFILL_SOURCE = "okx_gapfill"   # 合成点 source 哨兵；后端一律引用本常量
GAPFILL_ENABLED = os.getenv("GAPFILL_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
GAPFILL_STALENESS_MINUTES = int(os.getenv("GAPFILL_STALENESS_MINUTES", "60"))   # 真实 bar 超此分钟数判休市
GAPFILL_PERP_FRESH_MINUTES = int(os.getenv("GAPFILL_PERP_FRESH_MINUTES", "12")) # perp 自身新鲜度
GAPFILL_STEP_PCT = float(os.getenv("GAPFILL_STEP_PCT", "0.05"))   # 单根 5m 跳变上限（抓坏价）
GAPFILL_SEAM_PCT = float(os.getenv("GAPFILL_SEAM_PCT", "0.15"))   # 补点段首点 seam 上限（抓坏锚点）

# ============================================================
# 新闻源配置
# ============================================================
NEWS_SOURCES = {
    "jin10": {
        "enabled": True,
        "language": "zh",
        "name": "Jin10",
    },
    # CNBC Top News：全球突发 + 财经为主，每日数十条新增，覆盖 Fed / 监管 / 公司事件 / 地缘等。
    # 比之前用的 Bloomberg RSS 稳定，且非加密专项。
    # 备选 feed（按 id 切换）：100003114=Top News, 15839069=Markets, 19834094=Investing。
    "cnbc": {
        "enabled": True,
        "type": "rss",
        "language": "en",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "name": "CNBC",
    },
    # InvestingLive（原 ForexLive）：英文宏观/外汇快讯，分钟级，普通 nginx 直连稳定。
    "investinglive": {
        "enabled": True,
        "type": "rss",
        "language": "en",
        "url": "https://investinglive.com/feed/news",
        "name": "InvestingLive",
    },
    # FinancialJuice：英文版 jin10，秒级短快讯；Cloudflare 源，靠 rss_source 的 Accept 头 + 429 退避。
    "financialjuice": {
        "enabled": True,
        "type": "rss",
        "language": "en",
        "url": "https://www.financialjuice.com/feed.ashx?xml=rss",
        "name": "FinancialJuice",
    },
}

# ============================================================
# Polymarket 预测市场配置
# ============================================================
POLYMARKET = {
    "enabled": True,
    "api_url": "https://clob.polymarket.com",
    "gamma_url": "https://gamma-api.polymarket.com",
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
        "params": {"symbol": "BTC/USDT", "threshold_pct": 0.5, "window_minutes": 15},
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
        "name": "sector_spike",
        "rule_type": "sector_spike",
        "params": {
            "period": "24h",
            "metric": "median",
            "threshold_pct": 8.0,
            "direction": "both",
            "min_token_count": 10,
            "top_n": 8,
        },
        "channels": ["wechat_work"],
        "cooldown_minutes": 55,
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
# 价格告警：陈旧数据保护
# ============================================================
# 当前价 bar 距今超过这个分钟数（源停更，如期货休市/周末/掉线）则不告警，
# 避免对同一根旧 bar 每个扫描周期反复推送。设为 0 关闭此保护。
ALERT_PRICE_MAX_STALENESS_MINUTES = int(os.getenv("ALERT_PRICE_MAX_STALENESS_MINUTES", "30"))

# ============================================================
# 远程板块管道健康告警
# ============================================================
REMOTE_MONITORING_ENABLED = os.getenv("REMOTE_MONITORING_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "on",
}
REMOTE_MONITOR_ALERT_COOLDOWN_MINUTES = int(os.getenv("REMOTE_MONITOR_ALERT_COOLDOWN_MINUTES", "60"))
REMOTE_MONITOR_SFTP_FAILURE_THRESHOLD = int(os.getenv("REMOTE_MONITOR_SFTP_FAILURE_THRESHOLD", "3"))
REMOTE_MONITOR_WAL_MAX_MB = int(os.getenv("REMOTE_MONITOR_WAL_MAX_MB", "512"))

# ============================================================
# 标注事件合并
# ============================================================
# 断档阈值（news-impact-engine Phase 2）：相邻触发扫描点(end_dt)间隔 > 此分钟数 → 上一个窗口走完、另起一个。
# 5min = 一个快照步长（跳一格即断档）。开市丢快照造成的虚假劈窗由 gap-repair 补洞后 compute-on-read 自愈。
ANNOTATION_EVENT_MERGE_GAP_MINUTES = int(os.getenv("ANNOTATION_EVENT_MERGE_GAP_MINUTES", "5"))

# 最新窗口 live 余量（A 策略①，2026-06-28 简化）：**只**冻结最新那个窗口，且仅当它结束于此余量内
# （还在生长边缘、可能随新 bar 合并）。超过此余量没动（收盘/静默）就判走完、可标。更早窗口一律可标。
ANNOTATION_SETTLE_MARGIN_MINUTES = int(os.getenv("ANNOTATION_SETTLE_MARGIN_MINUTES", "30"))

# ============================================================
# Dune Analytics 休眠配置：当前 app/API 不加载，仅保留给旧查询脚本手动试验。
# ============================================================
DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")
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
# 远程数据源（BMAC SFTP）配置
# ============================================================
# 仅声明默认值，实际值从 .env 读取。具体含义见 docs/specs/remote_data_integration.md §5。
REMOTE_DATA_ROOT = os.getenv("REMOTE_DATA_ROOT", "/root/data_center/data/").rstrip("/") + "/"
LOCAL_CACHE_DIR = os.getenv("LOCAL_CACHE_DIR", "data/remote_cache")
REMOTE_OFFSET = os.getenv("REMOTE_OFFSET", "30m")
REMOTE_PULLER_POLL_SECONDS = int(os.getenv("REMOTE_PULLER_POLL_SECONDS", "3600"))

# ============================================================
# CoinMarketCap 板块分类配置
# ============================================================
CMC_API_KEY = os.getenv("CMC_API_KEY", "")
CMC_API_BASE_URL = os.getenv("CMC_API_BASE_URL", "https://pro-api.coinmarketcap.com")
CMC_CACHE_TTL_DAYS = int(os.getenv("CMC_CACHE_TTL_DAYS", "7"))
# CMC 限速 ~30 调用/分钟，请求间隔 2.5s 保险
CMC_REQUEST_INTERVAL_SECONDS = float(os.getenv("CMC_REQUEST_INTERVAL_SECONDS", "2.5"))

# 板块白名单：大组名 → 该组下关心的 CMC category 名（精确匹配 CMC 的 category.name 字段）。
# 起步版 ~50 个板块，按需增删。改完用 `python run.py refresh-sectors` 强制刷新本地缓存。
# 详见 docs/specs/remote_data_integration.md 附录 A。
SECTOR_WHITELIST: dict[str, list[str]] = {
    "公链龙头": [
        "Layer 1", "Smart Contracts",
        "Ethereum Ecosystem", "Solana Ecosystem", "BNB Chain Ecosystem",
        "Avalanche Ecosystem", "TRON Ecosystem",
    ],
    "L2 / 扩容": [
        "Layer 2", "Rollups", "Modular Blockchain",
    ],
    "DeFi": [
        "Decentralized Exchange (DEX) Token", "Lending & Borrowing", "Yield Farming",
        "Liquid Staking Derivatives", "Derivatives", "Perpetuals",
    ],
    "AI 板块": [
        "AI & Big Data", "AI Agents", "AI Memes", "AI Agent Launchpad",
    ],
    "Meme 主流": [
        "Memes", "Cat-Themed",
        "Four.Meme Ecosystem", "Pump Fun Ecosystem",
    ],
    "RWA": [
        "Real World Assets Protocols", "Tokenized Stock",
        "xStocks Ecosystem", "Tokenized Gold",
    ],
    "GameFi / 元宇宙": [
        "Gaming", "Metaverse", "Play To Earn",
    ],
    "隐私": [
        "Privacy",
    ],
    "DePIN / 存储": [
        "DePIN", "Filesharing", "Storage",
    ],
    "体育 / IP": [
        "Sports", "Soccer",
    ],
    "稳定币 / 收益": [
        "Stablecoin", "Algorithmic Stablecoin",
    ],
    "聪明钱组合": [
        "a16z Portfolio", "Multicoin Capital Portfolio", "Paradigm Portfolio",
        "Coinbase Ventures Portfolio",
    ],
    "新币 / 上币事件": [
        "Binance Launchpool", "Binance HODLer Airdrops",
    ],
}


def all_whitelisted_cmc_categories() -> list[str]:
    """扁平化 SECTOR_WHITELIST 拿到所有 CMC category 名称（去重保序）。"""
    seen: dict[str, None] = {}
    for group_cats in SECTOR_WHITELIST.values():
        for name in group_cats:
            seen.setdefault(name)
    return list(seen.keys())


def cmc_category_to_group(name: str) -> str | None:
    """给定一个 CMC category name，返回它所属的中文大组名；不在白名单内返回 None。"""
    for group, cats in SECTOR_WHITELIST.items():
        if name in cats:
            return group
    return None


# ============================================================
# 数据清理配置
# ============================================================
DATA_RETENTION = {
    # 2026-07-09 用户拍板 30→90：共振分 S 的稀有度锚定/回放校准需要 60-90 天基线。
    # 每日 03:17 data_retention job 按此清理（services/data_retention.py）；放宽=多留数据，无删数风险，
    # 远程磁盘容量部署时看一眼。
    "price_snapshots_days": 90,     # 5分钟快照保留天数
    "news_items_days": 90,          # 新闻保留天数
    "prediction_markets_days": 30,  # 预测市场快照保留天数
    "alert_logs_days": 90,          # 告警日志保留天数
}
