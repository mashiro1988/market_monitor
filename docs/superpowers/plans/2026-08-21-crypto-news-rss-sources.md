# 加密新闻源重组(BlockBeats 断供替代)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BlockBeats credit 耗尽断供后,接入 PANews/吴说/CoinDesk 三个免费官方订阅流走现有 RSS 采集器,恢复加密线快讯供给(≈245 条/天)。

**Architecture:** RSSSource 加一个 `market` 参数(默认 macro,宏观线零变化);config 的 CRYPTO_NEWS_SOURCES 加三条 `type: "rss"` 源;rss_source.py 加 `create_crypto_rss_sources()` 工厂,news_scanner 加密注册段挂上。打标/挂接/前端零改动(只认 market 不认源名)。

**Tech Stack:** Python + requests + feedparser(RSS/Atom 通吃),pytest,部署走服务器 deploy.sh。

**Spec:** docs/superpowers/specs/2026-08-21-crypto-news-rss-sources-design.md

---

### Task 1: config——三新源入配置,BlockBeats 停用

**Files:**
- Modify: `config.py`(CRYPTO_NEWS_SOURCES 字典,约 408-430 行)
- Test: `tests/test_crypto_config.py`

- [ ] **Step 1: 写失败测试**(追加到 tests/test_crypto_config.py 末尾)

```python
def test_crypto_rss_sources_present():
    """PANews/吴说/CoinDesk(2026-08-21 断供重组)必须以 type=rss 挂在加密配置。"""
    expect = {"panews": "zh", "wublock": "zh", "coindesk": "en"}
    for key, lang in expect.items():
        cfg = config.CRYPTO_NEWS_SOURCES[key]
        assert cfg["enabled"] is True, key
        assert cfg["type"] == "rss", key
        assert cfg["url"].startswith("https://"), key
        assert cfg["language"] == lang, key


def test_blockbeats_disabled_but_config_kept():
    """credit 耗尽停用;配置整体保留,续费改回 enabled 即恢复。"""
    bb = config.CRYPTO_NEWS_SOURCES["blockbeats"]
    assert bb["enabled"] is False
    assert bb["api_url"].startswith("https://api-pro.theblockbeats.info")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/test_crypto_config.py -q`
Expected: 2 failed(KeyError: 'panews' / assert True is False),旧 4 个通过

- [ ] **Step 3: 改 config.py**

`"blockbeats"` 条目只改一行:

```python
        "enabled": False,  # 2026-08-21 credit 耗尽停用;续费改回 True 即恢复,代码全保留
```

`"binance_ann"` 条目原样不动。在 CRYPTO_NEWS_SOURCES 字典闭合 `}` 之前(binance_ann 条目之后)追加:

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/test_crypto_config.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_crypto_config.py
git commit -m "feat(config): 加密源加 PANews/吴说/CoinDesk 三条 RSS 配置,BlockBeats credit 耗尽停用"
```

---

### Task 2: RSSSource 加 market 参数(含吴说 Atom 解析验证)

**Files:**
- Modify: `scanners/sources/rss_source.py`(`__init__` 约 24-28 行;NewsRecord 构造约 100-110 行)
- Test: `tests/test_rss_source.py`

- [ ] **Step 1: 写失败测试**(追加到 tests/test_rss_source.py 末尾,复用文件里已有的 `_Resp`)

```python
ATOM_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>吴说</title>
  <entry>
    <title>某巨鲸增持 BTC</title>
    <id>tag:wu,2026:1</id>
    <link href="https://www.wublock123.com/p/1"/>
    <updated>2026-08-20T08:34:21.000Z</updated>
    <summary>链上监测显示巨鲸买入</summary>
  </entry>
</feed>"""


def test_rss_market_defaults_to_macro(monkeypatch):
    """不传 market 的现有宏观调用方,行为一个字节不变。"""
    feed = b"<rss><channel><item><title>Fed hikes</title><guid>1</guid></item></channel></rss>"
    monkeypatch.setattr(rss_source.requests, "get", lambda *a, **k: _Resp(200, feed))
    monkeypatch.setattr(config, "proxies", lambda: {})
    records = RSSSource("financialjuice", "http://x/feed", "FinancialJuice", "en").fetch()
    assert records[0].market == "macro"


def test_rss_crypto_market_and_atom_parse(monkeypatch):
    """吴说是 Atom 格式:market 透传 + Atom 的题/摘要/时间解析一次验掉。"""
    monkeypatch.setattr(rss_source.requests, "get", lambda *a, **k: _Resp(200, ATOM_FEED))
    monkeypatch.setattr(config, "proxies", lambda: {})
    records = RSSSource("wublock", "http://x/feed", "吴说区块链", "zh",
                        market="crypto").fetch()
    assert len(records) == 1
    record = records[0]
    assert record.market == "crypto"
    assert record.title == "某巨鲸增持 BTC"
    assert record.published_at is not None
    assert "链上监测" in (record.content or "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/test_rss_source.py -q`
Expected: `test_rss_market_defaults_to_macro` 通过(NewsRecord 默认 market 就是 macro),`test_rss_crypto_market_and_atom_parse` 失败(TypeError: unexpected keyword argument 'market')。**注意确认失败原因是 market 参数不存在**,不是 Atom 解析问题。

- [ ] **Step 3: 改 rss_source.py**

`__init__` 改为:

```python
    def __init__(self, source_key: str, url: str, name: str, language: str = "en",
                 market: str = "macro"):
        self.source_key = source_key
        self.url = url
        self.name = name
        self.language = language
        # macro=宏观线 / crypto=加密线;决定落库后走哪套打分与事件池(web3 二期A 的总开关)
        self.market = market
```

`fetch()` 里 `records.append(NewsRecord(` 的构造加一行(放在 `published_at=published_at,` 之后):

```python
                    market=self.market,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/test_rss_source.py -q`
Expected: 6 passed(原 4 + 新 2)

- [ ] **Step 5: Commit**

```bash
git add scanners/sources/rss_source.py tests/test_rss_source.py
git commit -m "feat(rss): RSSSource 加 market 参数——加密源复用同一采集器,宏观默认值不变"
```

---

### Task 3: create_crypto_rss_sources 工厂 + news_scanner 挂线

**Files:**
- Modify: `scanners/sources/rss_source.py`(文件末尾 create_rss_sources 之后)
- Modify: `scanners/news_scanner.py`(第 11 行 import;第 37-44 行加密注册段)
- Test: `tests/test_rss_source.py`,`tests/test_news_scanner_crypto.py`

- [ ] **Step 1: 写失败测试**

追加到 tests/test_rss_source.py 末尾:

```python
def test_create_crypto_rss_sources_only_rss_typed_enabled(monkeypatch):
    """只认 type=rss 且 enabled 的加密源;专用采集器条目(无 type)与停用条目不归工厂。"""
    monkeypatch.setattr(config, "CRYPTO_NEWS_SOURCES", {
        "panews": {"enabled": True, "type": "rss", "url": "http://x/rss",
                   "name": "PANews", "language": "zh"},
        "coindesk": {"enabled": False, "type": "rss", "url": "http://y/rss",
                     "name": "CoinDesk", "language": "en"},
        "binance_ann": {"enabled": True, "name": "币安公告", "language": "en"},
    }, raising=False)
    sources = rss_source.create_crypto_rss_sources()
    assert [s.source_key for s in sources] == ["panews"]
    assert sources[0].market == "crypto"
    assert sources[0].name == "PANews"
```

追加到 tests/test_news_scanner_crypto.py 末尾(文件头已 import config? 没有——需在文件头 import 区补 `import config`):

```python
def test_scanner_registers_crypto_rss_and_skips_disabled_blockbeats(monkeypatch):
    """扫描器注册段:rss 型加密源自动挂上;BlockBeats 停用后有 key 也不注册。"""
    monkeypatch.setattr(config, "NEWS_SOURCES", {"jin10": {"enabled": False}}, raising=False)
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "BLOCKBEATS_API_KEY", "still-have-key", raising=False)
    monkeypatch.setattr(config, "CRYPTO_NEWS_SOURCES", {
        "blockbeats": {"enabled": False},
        "panews": {"enabled": True, "type": "rss", "url": "http://x/rss",
                   "name": "PANews", "language": "zh"},
    }, raising=False)

    scanner = NewsScanner()
    keys = {getattr(s, "source_key", None) for s in scanner.sources}
    assert "panews" in keys
    assert all(type(s).__name__ != "BlockBeatsSource" for s in scanner.sources)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/test_rss_source.py tests/test_news_scanner_crypto.py -q`
Expected: 工厂测试失败(AttributeError: no attribute 'create_crypto_rss_sources'),扫描器测试失败(panews 不在 keys)

- [ ] **Step 3: 实现**

scanners/sources/rss_source.py 文件末尾(create_rss_sources 之后)追加:

```python
def create_crypto_rss_sources() -> list[RSSSource]:
    """CRYPTO_NEWS_SOURCES 里 type=rss 的加密源(2026-08-21 断供重组)。

    BlockBeats/币安公告这类专用采集器条目没有 type 键,天然不归这里;
    加密源一律 market="crypto",落库即走加密打标与加密事件池。
    """
    sources = []
    for key, cfg in getattr(config, "CRYPTO_NEWS_SOURCES", {}).items():
        if not cfg.get("enabled") or cfg.get("type") != "rss":
            continue
        url = cfg.get("url", "")
        if not url:
            continue
        sources.append(RSSSource(
            source_key=key,
            url=url,
            name=cfg.get("name", key),
            language=cfg.get("language", "zh"),
            market="crypto",
        ))
    return sources
```

scanners/news_scanner.py 第 11 行 import 改为:

```python
from scanners.sources.rss_source import create_crypto_rss_sources, create_rss_sources
```

加密注册段(`if getattr(config, "CRYPTO_NEWS_ENABLED", False):` 块内,binance_ann 注册之后)追加:

```python
            # RSS 型加密源(PANews/吴说/CoinDesk,2026-08-21 断供重组):配置驱动,加源=加配置
            self.sources.extend(create_crypto_rss_sources())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/test_rss_source.py tests/test_news_scanner_crypto.py -q`
Expected: 10 passed(rss 7 + scanner 3)

- [ ] **Step 5: Commit**

```bash
git add scanners/sources/rss_source.py scanners/news_scanner.py tests/test_rss_source.py tests/test_news_scanner_crypto.py
git commit -m "feat(sources): 加密 RSS 源工厂挂进扫描器——PANews/吴说/CoinDesk 上线,配置驱动"
```

---

### Task 4: 全量回归 + 设计稿状态更新

**Files:**
- Modify: `docs/superpowers/specs/2026-08-21-crypto-news-rss-sources-design.md`(头部状态行)

- [ ] **Step 1: 全量测试**

Run: `$env:PYTHONUTF8="1"; & D:\anaconda\python.exe -m pytest tests/ -q`
Expected: 全绿(2026-08-20 基线全过;若有失败,先判断是否本次改动引入,不是则报告用户)

- [ ] **Step 2: 设计稿状态行更新**

把 `状态:设计已确认,待实施` 改为(commit 号以 Task 3 实际为准):

```markdown
状态:已实施(2026-08-21),实施计划 docs/superpowers/plans/2026-08-21-crypto-news-rss-sources.md
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-21-crypto-news-rss-sources-design.md docs/superpowers/plans/2026-08-21-crypto-news-rss-sources.md
git commit -m "docs(specs): 加密源重组标记已实施,实施计划落盘"
```

---

### Task 5: 部署与线上验收

前提:Task 1-4 已提交到 main。deploy.sh 自带部署前 DB 快照(VACUUM INTO backups/)。

- [ ] **Step 1: 推送并部署**

```bash
git push
ssh mmon "cd /opt/market_monitor && ./deploy.sh"
```

Expected: deploy.sh 走完 git pull → pip → 前端构建 → systemctl restart,无报错

- [ ] **Step 2: 服务与日志验收**(重启后等一个扫描周期,约 5 分钟)

```bash
ssh mmon "systemctl --no-pager status market-monitor | head -5"
ssh mmon "journalctl -u market-monitor --since '-10 min' | grep -E 'PANews|吴说|CoinDesk|RSS' | tail -20"
```

Expected: 服务 active;三源各自出现 `RSS <名> 获取 N 条新闻` 且 N > 0;**不再出现** BlockBeats 采集失败刷屏

- [ ] **Step 3: 接口与落库验收**

```bash
ssh mmon "curl -s https://mmon.top/api/crypto/news/sources"
ssh mmon "sqlite3 /opt/market_monitor/market_monitor.db \"SELECT source, COUNT(*), MAX(published_at) FROM news_items WHERE market='crypto' AND source IN ('panews','wublock','coindesk') GROUP BY source\""
```

Expected: 接口返回含 panews/wublock/coindesk 三项、不含 blockbeats;库里三源各有新行且 market='crypto'(启动回补会把订阅流里 72h 内可见存量捞进来,首轮就该有几十条)

- [ ] **Step 4: 打标线验收**(部署后 10-15 分钟再看)

```bash
ssh mmon "sqlite3 /opt/market_monitor/market_monitor.db \"SELECT COUNT(*) FROM news_items WHERE source IN ('panews','wublock','coindesk') AND tagged_at IS NOT NULL\""
```

Expected: > 0(新源新闻进了加密打标队列并已盖章;若为 0,查 journalctl 里 CryptoTag 分片日志)

---

## Self-Review(计划完成后自查记录)

1. **Spec 覆盖**:§1 源清单变化=Task 1;§2 三处工程改动=Task 1/2/3;§3 零改动清单=无任务(靠 Task 4 全量回归兜底);§4 兜底(启动回补/单源隔离)=现有机制,Task 5 Step 3 验收;§5 测试与验收=各任务 TDD 步骤 + Task 5。无缺口。
2. **占位符**:无 TBD/TODO;所有代码步骤给了完整代码。
3. **类型一致性**:`create_crypto_rss_sources` 名称在 Task 3 测试/实现/import 三处一致;`market` 参数签名 Task 2 定义与 Task 3 工厂调用一致;config 键名(panews/wublock/coindesk,type/url)在 Task 1 与 Task 3 一致。

一处已知偏离(优于 spec 字面):spec §2 写"news_scanner 加通用循环",实施放在 rss_source.py 的工厂函数、扫描器只 extend 一行——循环逻辑与 create_rss_sources 同居一处,可独立测试,行为等价。
