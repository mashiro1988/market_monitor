"""
统一配置文件 - Investment Agent
"""
import os
from datetime import datetime

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
DEEPSEEK_REASONER_READ_TIMEOUT = float(os.getenv("DEEPSEEK_REASONER_READ_TIMEOUT", "420"))
# 批量调用一次喂多个窗口，单次思考时间 = 单窗口 × 倍数；read timeout 也要相应放大。
DEEPSEEK_REASONER_BATCH_READ_TIMEOUT = float(os.getenv("DEEPSEEK_REASONER_BATCH_READ_TIMEOUT", "600"))
DEEPSEEK_REASONER_EFFORT = os.getenv("DEEPSEEK_REASONER_EFFORT", "max")  # "high" | "max"
# 标注 max_tokens 护栏（同时覆盖 reasoning + content，只按实际用量计费）。
# 2026-08-20 实锤：快照更新后 v4-pro 思考显著变长，7 月定的 8000/16000 被思考吃光
# → content 空 → 502 反复重试白烧 token。提档并改环境变量可调；单窗超时 240→420 配套。
DEEPSEEK_REASONER_MAX_TOKENS = int(os.getenv("DEEPSEEK_REASONER_MAX_TOKENS", "16000"))
DEEPSEEK_REASONER_BATCH_MAX_TOKENS = int(os.getenv("DEEPSEEK_REASONER_BATCH_MAX_TOKENS", "24000"))

# 事件池 AI 梳理(pool sweep,2026-08-13 design):按钮触发,reasoner 全池盘点。
# 窗口/上限给足预算:800 条标题+摘要约 6-7 万 token 输入,v4-pro 上下文放得下;
# max_tokens 同时覆盖 reasoning_content + content,盘点思考量大,给到 24k。
RESEARCH_SWEEP_DAYS = int(os.getenv("RESEARCH_SWEEP_DAYS", "7"))
RESEARCH_SWEEP_MAX_NEWS = int(os.getenv("RESEARCH_SWEEP_MAX_NEWS", "800"))
RESEARCH_SWEEP_MAX_TOKENS = int(os.getenv("RESEARCH_SWEEP_MAX_TOKENS", "24000"))
RESEARCH_SWEEP_MAX_NEW_EVENTS = int(os.getenv("RESEARCH_SWEEP_MAX_NEW_EVENTS", "8"))

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
    "prediction": 60,   # 2026-08-28 起小时级:降频+永久保留替代"5min+30天滚动删除"
}
# 游标同步"至少回看"地板（小时，两源共用）：平时每轮固定回看 24h，晚到 ≤24h 的 bar 自动进库
# （原 gap_repair 的覆盖搬进主路径）；停机更久时窗口按库内游标自动拉长（见 sync_window_start）。
SYNC_MIN_LOOKBACK_HOURS = int(os.getenv("SYNC_MIN_LOOKBACK_HOURS", "24"))

# ── yfinance 请求整形（2026-07-22 治本：告别 16 并发突发；参数可环境变量覆盖）──
YF_REQUEST_TIMEOUT_SEC = int(os.getenv("YF_REQUEST_TIMEOUT_SEC", "10"))    # 单请求超时
YF_STAGE_BUDGET_SEC = int(os.getenv("YF_STAGE_BUDGET_SEC", "180"))         # 阶段软预算，保 5min 周期
YF_JITTER_MIN_SEC = float(os.getenv("YF_JITTER_MIN_SEC", "0.3"))           # 品种间随机抖动下限
YF_JITTER_MAX_SEC = float(os.getenv("YF_JITTER_MAX_SEC", "0.8"))           # 品种间随机抖动上限
# 指数退避（2026-07-27）：开市却取数落空视为限流信号，该品种冷却 5/10/20/40/60 分钟封顶，
# 成功即归零。游标窗口每轮回看 24h，跳过的轮次会在恢复后自动补齐 → 退避不丢数据。
YF_BACKOFF_BASE_MINUTES = float(os.getenv("YF_BACKOFF_BASE_MINUTES", "5"))
YF_BACKOFF_MAX_MINUTES = float(os.getenv("YF_BACKOFF_MAX_MINUTES", "60"))

# ── 市场概览卡片 freshness 标注阈值（分钟）──
FRESHNESS_STALE_MINUTES = int(os.getenv("FRESHNESS_STALE_MINUTES", "15"))   # 开市中滞后→黄标
FRESHNESS_DOWN_MINUTES = int(os.getenv("FRESHNESS_DOWN_MINUTES", "60"))    # 开市中滞后→红标"源中断"

# 价格源健康告警（2026-07-27 P0）：红标品种/采集异常推企业微信，判定与卡片同源。
# 不建模交易所假日 → 美股假日当天会误报一次，推送正文已带提示语。
PRICE_SOURCE_MONITORING_ENABLED = os.getenv("PRICE_SOURCE_MONITORING_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "on",
}
PRICE_SOURCE_ALERT_COOLDOWN_MINUTES = int(os.getenv("PRICE_SOURCE_ALERT_COOLDOWN_MINUTES", "60"))

# 预测市场图表的「活跃」宽限期（分钟）：最后一笔快照落后于表内最新快照超过该值的市场，
# 视为已停止跟踪（软删除后快照断流），整体从 /predictions 与 families 图表消失。
# 基准取表内最新快照时间而非墙钟，调度器宕机时不会误杀全部市场。
PREDICTION_ACTIVE_GRACE_MINUTES = int(os.getenv(
    "PREDICTION_ACTIVE_GRACE_MINUTES",
    str(SCAN_INTERVALS["prediction"] * 2 + 30),   # 小时扫描下=150:单次抓取失败不掉图
))

# 「跨资产走势」净值基准：取窗口起始时刻之前最后一笔收盘作基准，向前回看上限（天）。
MARKET_HISTORY_BASELINE_LOOKBACK_DAYS = int(os.getenv("MARKET_HISTORY_BASELINE_LOOKBACK_DAYS", "7"))

# ============================================================
# 新闻影响力引擎 · 主题台账（docs/specs/news-impact-engine-plan.md，Phase 1）
# ============================================================
# 【已冻结·遗留 2026-08-08】主题分类种子表：打标已停判停写 topic（语义归类改走研究事件池，
# news-research-phase1-event-pool.md §13.4 切换）。枚举保留仅为历史数据可读（news_items.topic
# 存量值）与 theme_ledger 遗留代码引用，勿新增消费者。
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
# 【已冻结·遗留 2026-08-08】a-priori 量级已停判停写（用户拍板；spec §4.2 实测它是较差的
# 重要性信号）。枚举保留仅为历史数据可读（news_items.magnitude_tier 存量值）。
NEWS_MAGNITUDE_TIERS = ("大", "中", "小")
# 方向：相对风险资产（BTC/纳指）的应然影响。（在役：打标唯一还在判的内容标签）
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

# 待标注回溯下限（2026-08-08 用户拍板）：早于北京 2026-07-16 00:00（= UTC 07-15 16:00）的
# 窗口不再进待标注列表——7 月中旬前的积压不补标。只截"全量回溯"的显示下限，不动行为段
# 与历史标注数据；已标注列表的 needs_review 时代守卫同步以此为界（否则老标注全体误亮橙标）。
ANNOTATION_BACKLOG_FLOOR_UTC = datetime(2026, 7, 15, 16, 0)

# 标注页「宏观同期对标」清单：(symbol, 中文标签[, 单位])。增减对标资产只改这里。
# symbol 必须是 price_snapshots 里在采的（config 价格源内）。
# 第三项可选 "bp"：收益率类品种按基点显示（+10.0bp = 上行 0.10 个百分点），缺省按涨跌%。
# 七个对标 = 风险资产 / 亚洲风险资产 / 地缘供给 / 避险 / 利率 / 美元流动性 / 加密贝塔。
ANNOTATION_REFERENCE_ASSETS = [
    ("NQ=F", "纳指"),
    ("NIY=F", "日经225"),   # 2026-07-12 起 CME 日经期货（Globex ~23h）；^N225 现货只有东京时段，退役出参照
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
    # ^N225 现货参照已退役（2026-07-12，旧档 [0.42,0.68,1.16] 供追溯）——只有东京时段、样本薄
    "NIY=F": [0.32, 0.52, 0.83],    # 2026-07-12 定档：CME 日经期货 30d 5206 bars，双锚偏差 3.9%（rarity/volratio 中点）
    # US_2Y：2026-07-09 校准首跑 30d 仅 3 个有效 15min 样本——CNBC 债券快照撑不起 5min 严格网格，
    # S 不可用，维持禁用；标注页 reference_changes/三段展示走容差取点、不受影响。数据源修好后再校准。
    "US_2Y": None,
}
# 共振参照资产（有序 = 展示/S 计算顺序）；与 ANNOTATION_REFERENCE_ASSETS 同源，BTC 是主品种不进参照。
BEHAVIOR_REF_SYMBOLS = ["NQ=F", "NIY=F", "GC=F", "US_2Y", "DX-Y.NYB", "CL=F"]
# 共振分 S 判级 cutoff（回放校准项）：max|S| ≥ HI 共振；MID~HI 弱共振（仅展示证据）；< MID 独立。
BEHAVIOR_S_HI = float(os.getenv("BEHAVIOR_S_HI", "0.5"))
BEHAVIOR_S_MID = float(os.getenv("BEHAVIOR_S_MID", "0.3"))
# ESS（有效样本数 (Σw)²/Σw²）低于此值标"证据薄"——分数靠一两根 bar 撑起，对插针/坏数据敏感。
BEHAVIOR_ESS_THIN = float(os.getenv("BEHAVIOR_ESS_THIN", "5"))
# 大窗口内参照覆盖（按 BTC 权重质量算）低于此比例 → 该参照不出分（休市/缺数 = 分数地基不实 → 无对照）。
# 2026-07-12 用户定稿（不再修改）：覆盖率 ≥50%——CME 每日维护最长 1 小时（≤12/30 点），
# 50% 门槛能穿过维护时段不断线；数据连续性问题由休市 perp 代理补点解决（另立项）。
# （2026-07-10 曾试 0.95，代价是维护后 2h+ 黑窗 + 日经全场稀疏，2026-07-12 回退定稿。）
BEHAVIOR_COVERAGE_MIN = float(os.getenv("BEHAVIOR_COVERAGE_MIN", "0.5"))
# 新闻命中：段窗 ± 分钟内存在重要新闻。判据 2026-08-08 起复用事件池闸门口径
# （llm_importance ≥ EVENT_LINK_MIN_IMPORTANCE 或未评分放行）；旧量级口径随量级停判退役。
BEHAVIOR_NEWS_WINDOW_MIN = int(os.getenv("BEHAVIOR_NEWS_WINDOW_MIN", "30"))
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
        # CME 日经225期货（Globex ~23h/日）：行为引擎日经参照的替代源（^N225 现货只有东京时段），
        # 2026-07-12 用户拍板；^N225 保留用于市场概览展示。
        "日经期货": "NIY=F",
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
    # OKX 商品/指数代理永续：作为独立行情入库，不再改写对应期货曲线。
    # key 是页面显示名，value 同时是 OKX instId 与数据库原始 symbol。
    "perp_proxy": {
        "纳指代理永续": "QQQ-USDT-SWAP",
        "原油永续": "CL-USDT-SWAP",
        "黄金永续": "XAU-USDT-SWAP",
    },
}

# ============================================================
# 休市补点（gap-fill）已于 2026-07-27 退役并删除采集代码（用户拍板）。
# OKX 永续现按 PRICE_SOURCES["perp_proxy"] 作为独立行情入库，不再合成代理价。
# 下面这个哨兵常量**必须保留**：库内仍有 2026-07-02~07-15 的 4,163 行历史合成点
# （填的是休市空档，无真实数据可替代），后端据此识别、前端据此标「代理价」。
# 历史退役记录见 docs/superpowers/specs/2026-06-28-okx-gapfill-market-overview-design.md
# ============================================================
GAPFILL_SOURCE = "okx_gapfill"   # 历史合成点 source 哨兵；后端一律引用本常量

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
    # 2026-08-06 下线（用户拍板）：30 天未评分率 52%（分钟级晚到,多走滚动回补的不打分侧门）
    # + 长文综述类信息价值低。存量行保留展示；重开只需翻回 True。
    "investinglive": {
        "enabled": False,
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
# 加密新闻源（web3 二期A design §1）
# **刻意不并进 NEWS_SOURCES**：那个字典是宏观白名单，标注上下文与自动标注的候选
# 新闻都从它取源（services/annotation_service.py::_annotation_news_sources），
# 加密源混进去会直接污染已校准的标注池。分开放 = 结构上防污染。
# ============================================================
CRYPTO_NEWS_ENABLED = os.getenv("CRYPTO_NEWS_ENABLED", "1") == "1"
BLOCKBEATS_API_KEY = os.getenv("BLOCKBEATS_API_KEY", "")

CRYPTO_NEWS_SOURCES = {
    # BlockBeats Pro API：老的 open-api/open-flash 已软下线（匿名请求恒返回空数组，
    # 2026-08-09 服务器实探），现走 Pro API + api-key 请求头。取全量而非仅重要档：
    # 二期B 要归因的小币新闻基本都落在非重要档里，只取重要档等于掐断原料。
    "blockbeats": {
        "enabled": False,  # 2026-08-21 credit 耗尽停用;续费改回 True 即恢复,代码全保留
        "language": "zh",
        "name": "BlockBeats",
        "api_url": "https://api-pro.theblockbeats.info/v1/newsflash",
        "page_size": 30,          # Pro API 单页上限 50
        "max_pages": 2,           # 5 分钟一轮，60 条足够覆盖（实测约 70-150 条/天）
        "lang": "cn",
    },
    # 币安官方公告：上新/下架/合约上市——"某币为什么突然拉起来"命中率最高的官方口径。
    # 站点 CMS 接口，无服务承诺、可能改版；失败上抛由 NewsScanner 记源错误。
    "binance_ann": {
        "enabled": True,
        "language": "en",
        "name": "Binance公告",
        "api_url": "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query",
        "page_size": 20,
    },
    # 2026-08-21 断供重组(design: 2026-08-21-crypto-news-rss-sources-design.md):
    # 三个免费官方订阅流走通用 RSS 采集器(type=rss 由 create_crypto_rss_sources 识别),
    # 无 key 无 credit;url 一律配重定向后的终点地址,省每轮一次白跑。
    "panews": {
        "enabled": True,
        "type": "rss",
        "language": "zh",
        "name": "PANews",
        "url": "https://www.panewslab.com/rss.xml?lang=zh&type=NEWS",  # 快讯体裁,~148 条/天
    },
    "wublock": {
        "enabled": True,
        "type": "rss",
        "language": "zh",
        "name": "吴说区块链",
        "url": "https://www.wublock123.com/feed",  # Atom 格式,feedparser 通吃;~69 条/天
    },
    "coindesk": {
        "enabled": True,
        "type": "rss",
        "language": "en",
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss",  # 机构/监管英文首发;~28 条/天
    },
}

# 币安公告目录：(catalogId, 中文说明)。营销活动类目录刻意不订。
BINANCE_ANN_CATALOGS = (
    (48, "新币上线"),
    (161, "下架"),
)

# ============================================================
# Polymarket 预测市场配置
# ============================================================
POLYMARKET = {
    "enabled": True,
    "api_url": "https://clob.polymarket.com",
    "gamma_url": "https://gamma-api.polymarket.com",
    # AI 提案管线的垃圾市场门槛(USD):public-search 候选低于此交易量直接不要。
    # 手动搜索通道不受此限(人是有意找的)。
    "proposal_min_volume": 10_000,
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
# 远程数据源（BMAC SFTP）配置
# ============================================================
# 仅声明默认值，实际值从 .env 读取。具体含义见 docs/specs/remote_data_integration.md §5。
REMOTE_DATA_ROOT = os.getenv("REMOTE_DATA_ROOT", "/root/data_center/data/").rstrip("/") + "/"
LOCAL_CACHE_DIR = os.getenv("LOCAL_CACHE_DIR", "data/remote_cache")
REMOTE_OFFSET = os.getenv("REMOTE_OFFSET", "30m")
REMOTE_PULLER_POLL_SECONDS = int(os.getenv("REMOTE_PULLER_POLL_SECONDS", "3600"))

# 板块资金流勾稽门（2026-08-07 净资金流入 spec §5.2）。
# 宽表新增 quote_volume / taker_buy_quote_asset_volume 两个矩阵后，每轮扫描先过闸再算钱：
# 任一项不达标 → 该市场资金流整轮写 None（涨跌照常）+ 告警，绝不让错数上页面。
# 恒等式 0 <= 主动买入额 <= 总成交额 的逐格违规占比上限（浮点噪声留 0.1% 余量）
FLOW_IDENTITY_VIOLATION_MAX_RATIO = float(os.getenv("FLOW_IDENTITY_VIOLATION_MAX_RATIO", "0.001"))
# 同一恒等式**只看最新一根 bar**时的违规占比上限。
# 为什么要单设一道：全矩阵占比会被历史稀释 —— 2000 行 × 480 列里坏掉整根最新 bar
# 也才 0.05%，压根够不着上面那个 0.1% 的线（2026-08-07 本地彩排实测）。而最新 bar
# 正是 1h 列直接读的那根、也是写入损坏最常出现的地方。放宽到 5% 是为了容忍个别币
# 抽风，不至于每小时误报整个市场。
FLOW_LATEST_BAR_VIOLATION_MAX_RATIO = float(os.getenv("FLOW_LATEST_BAR_VIOLATION_MAX_RATIO", "0.05"))
# 最新一根 bar 上「成交额缺失率 − 收盘价缺失率」的上限：新字段大面积缺数时拦下
FLOW_NAN_GAP_MAX = float(os.getenv("FLOW_NAN_GAP_MAX", "0.05"))
# 勾稽失败告警的冷却分钟数（同 marker 冷却窗内只推一次）
FLOW_GATE_ALERT_COOLDOWN_MINUTES = int(os.getenv("FLOW_GATE_ALERT_COOLDOWN_MINUTES", "60"))

# ============================================================
# CoinMarketCap 板块分类配置
# ============================================================
CMC_API_KEY = os.getenv("CMC_API_KEY", "")
CMC_API_BASE_URL = os.getenv("CMC_API_BASE_URL", "https://pro-api.coinmarketcap.com")
CMC_CACHE_TTL_DAYS = int(os.getenv("CMC_CACHE_TTL_DAYS", "7"))
# CMC 限速 ~30 调用/分钟，请求间隔 2.5s 保险
CMC_REQUEST_INTERVAL_SECONDS = float(os.getenv("CMC_REQUEST_INTERVAL_SECONDS", "2.5"))

# 板块轮动的「巨头剔除名单」：这些 symbol 不参与任何板块聚合数字
# （涨跌均值/中位、资金流求和、成分币计数），明细表里仍列出但标「不计入」。
#
# 为什么必须剔（2026-08-16 本机快照实测）：
#   资金流是**求和**口径而不是等权平均，BTC 一个币约 +60~70M 的 24h 现货净流入
#   直接盖住整个板块 —— 白名单里 10 个含 BTC/ETH 的板块中 9 个方向反转（本该显示
#   资金流出，页面显示大幅流入），且彼此数字几乎相同，完全没有板块区分度。
#   涨跌是等权平均，受影响小（最多 0.3 个百分点），但同样剔除以保证全页同一口径。
# WBTC/WBETH 的价格就是 BTC/ETH 本身，留着等于同一资产在板块里重复计一次。
# 币安以后新上包装币（如 WETH、cbBTC）在这里加一行即可，算法侧不用动。
# ↓ 要增删就改这一行（顺序即页面上展示的顺序）
SECTOR_EXCLUDED_SYMBOL_LIST: tuple[str, ...] = ("BTC", "ETH", "WBTC", "WBETH")

SECTOR_EXCLUDED_SYMBOLS: set[str] = set(SECTOR_EXCLUDED_SYMBOL_LIST)
# 页面/告警上统一的剔除说明文案（只此一处，前端与企微推送共用同一句话）
SECTOR_EXCLUSION_NOTE = "已剔除 " + "/".join(SECTOR_EXCLUDED_SYMBOL_LIST)

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
# 研究事件池(docs/specs/news-research-phase1-event-pool.md)
# ============================================================
# 挂接总开关 = 回滚阀(spec §13.5):置 0 停挂接,已建表与数据原地保留。
EVENT_LINK_ENABLED = os.getenv("EVENT_LINK_ENABLED", "1") == "1"
# 挂接闸门线:llm_importance ≥ 6 **或未评分放行**(未评分=评分调用失败,不是不重要;
# 2026-07-28 线上库 30 天校准,71 条人工 driver 召回 96%,见 spec §4.2)。
EVENT_LINK_MIN_IMPORTANCE = int(os.getenv("EVENT_LINK_MIN_IMPORTANCE", "6"))
# 挂接黑名单:(来源, 标题正则),命中直接盖游标不发调用。人工维护,
# **禁止按频率自动生成**(会误杀 FinancialJuice 统一前缀这类真新闻,spec §4.4)。
NEWS_EVENT_LINK_BLACKLIST = (
    ("jin10", r"^金十数据整理："),
    ("jin10", r"^金十数据全球财经早餐"),
)
# 观测层(spec §8.1):基线=新闻前最近快照,终点=新闻后 N 分钟内最后快照。
EVENT_OBS_REACTION_MINUTES = int(os.getenv("EVENT_OBS_REACTION_MINUTES", "10"))
EVENT_OBS_SYMBOLS = ("BTC/USDT",)
# 立案/重开自动回扫范围(小时);深回扫由工作台按钮传天数。
EVENT_BACKSCAN_DEFAULT_HOURS = int(os.getenv("EVENT_BACKSCAN_DEFAULT_HOURS", "72"))

# ============================================================
# 新闻补评分扫描(docs/specs/2026-08-06-news-rescore-and-source-cut-design.md)
# ============================================================
# 入库时评分失败/走了不打分侧门(滚动回补、停机回补)的新闻,每轮扫描顺带补一小批。
NEWS_RESCORE_ENABLED = os.getenv("NEWS_RESCORE_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "on",
}
NEWS_RESCORE_WINDOW_HOURS = int(os.getenv("NEWS_RESCORE_WINDOW_HOURS", "72"))
NEWS_RESCORE_LIMIT = int(os.getenv("NEWS_RESCORE_LIMIT", "24"))          # 每轮 ≤2 批
NEWS_RESCORE_MAX_ATTEMPTS = int(os.getenv("NEWS_RESCORE_MAX_ATTEMPTS", "3"))  # 毒条目重试上限

# ============================================================
# 数据清理配置
# ============================================================
DATA_RETENTION = {
    # 2026-08-02 用户拍板(news-research-phase1 spec §12):价格快照与新闻**永久保留**
    # (None=永不清理)。年增约 0.7-1GB,不值得为省磁盘毁掉事件池历史;观测层因此维持
    # 读时现算。预测市场快照(全库最大表,~2.4万行/天)与告警日志和事件历史无关,维持原值。
    # None 的跳过守卫见 services/data_retention.py::_cutoff。
    # (历史:2026-07-09 曾拍 30→90 给共振分 S 校准留基线,该需求被永久保留自然覆盖。)
    "price_snapshots_days": None,   # 永久
    "news_items_days": None,        # 永久
    "prediction_markets_days": None,  # 2026-08-28 起永久(小时级快照,年增量约 30 万行)
    "alert_logs_days": 90,          # 告警日志保留天数
}
