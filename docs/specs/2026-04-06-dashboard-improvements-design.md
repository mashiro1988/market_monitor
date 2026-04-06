# Dashboard Improvements Design

> **日期**: 2026-04-06
> **范围**: 三个独立改进，可并行实现

---

## 1. Polymarket 市场过滤修复

### 问题
当前 tag 搜索返回无关市场（足球、高尔夫、电竞），原因：
- tracked_tags 大部分无法匹配到宏观金融市场
- 无 volume 下限过滤，低流量杂项市场混入

### 方案：slug 核心 + tag 补充（双层）

**层 1 — 手动 slug 列表（保证关键市场不漏）**
在 `config.py` 的 `POLYMARKET.tracked_slugs` 填入 10-15 个核心宏观市场 slug。每次扫描优先获取这些市场，不依赖搜索。

**层 2 — tag 搜索 + 严格过滤（补充发现）**
在 `polymarket_source.py` 的 `_search_markets()` 返回后新增过滤：
1. `volume_num > 10000`（过滤低流动性）
2. 关键词黑名单：过滤 question 中包含球队、球员、城市气温、esports 等词的市场

**预存在 bug 需同步修复**：`fetch()` 中 slug 循环将 `market_id` 加入 `seen_ids`，tag 循环将 `market_id:outcome` 加入 `seen_ids`，两者 key 格式不一致导致跨循环去重失效。实现时统一改为 `market_id:outcome` 格式。

**改动范围**：
- `config.py`：填充 `tracked_slugs` 列表，调整 `tracked_tags` 精简为高质量标签
- `scanners/sources/polymarket_source.py`：修复 dedup key 不一致问题；在 `_search_markets()` 返回后、`_parse_market()` 调用前做 volume 过滤（`market.get("volume_num", 0) > 10000`）和关键词黑名单过滤

**不改动**：数据库模型、PredictionScanner、UI 页面

---

## 2. 新闻 LLM 重要度评分

### 问题
RSS 来源（CoinDesk/CoinTelegraph/The Block/Reuters）importance 硬编码为 `5`（`rss_source.py`），无差别中等分，无法区分重要程度。wallstreetcn/jin10 的启发式打分（important flag → 8，否则 4/5）粒度同样粗糙。所有来源均缺乏基于内容的智能评分。

### 方案：DeepSeek Chat 批量打分

**新增文件**：`scanners/scorer.py`

```
class NewsScorer:
    - __init__: 读取 DEEPSEEK_API_KEY，无 key 时 enabled=False
    - score_batch(records: list[NewsRecord]) -> list[int]
      - 构建 prompt，批量发送（每批最多 20 条）
      - 返回 1-10 的整数列表，顺序与输入对应
      - 超时/异常时返回 None 列表（降级）
```

**调用时机与顺序**：
1. `NewsScanner.scan()` 收集完所有 source 数据后，调用 `self.scorer.score_batch(all_records)` 批量打分（此时 DB session 尚未开启）
2. 将分数写回 `NewsRecord.importance` 字段
3. 再进入 `_save_records()` 执行去重 + 入库

**重要**：scorer 调用必须在 DB session 开启之前完成，避免 LLM 超时期间长期持有数据库连接。

**去重与打分的关系**：打分在去重之前，因此被去重过滤掉的条目也会被打分（浪费少量 token），但这简化了代码结构。被去重过滤的条目本就不多（已存在于 DB 中），可接受。

**历史数据**：已入库的旧条目不做回填，仅对当次扫描新抓取的条目打分。

**Prompt 设计**：
```
你是一个加密货币和宏观经济投资者的新闻重要性评估助手。
对以下新闻列表，从投资决策角度评估重要性（1-10分）：
- 10分：重大政策变化、央行决议、系统性风险事件
- 7-9分：重要经济数据、监管动态、市场结构变化  
- 4-6分：一般市场新闻、行业动态
- 1-3分：噪音、娱乐性内容、无关信息

返回 JSON 数组，仅包含整数分数，顺序与输入一致。
输入：[{"title": "...", "content": "...前200字"}, ...]
```

**环境变量**：`.env` 新增 `DEEPSEEK_API_KEY`
**API**：`https://api.deepseek.com/v1/chat/completions`，model=`deepseek-chat`，OpenAI 兼容接口

**降级策略**：无 API Key 或调用失败时跳过打分，保留原始 importance 值（wallstreetcn/jin10 的启发式值，RSS 保持默认 5）

**实例化**：`NewsScorer` 在 `NewsScanner.__init__()` 中实例化，存为 `self.scorer`

**不改动**：数据库模型（importance 字段已存在）、UI 页面

---

## 3. 市场概览跨资产时序图

### 问题
市场概览只有最新快照的 metric 卡片，无历史走势，price_snapshots 表中的历史数据未被 UI 消费。

### 方案：页面底部跨资产对比图

**位置**：`pages/1_市场概览.py` 底部，现有 metric 卡片之后，详细数据表之前。

**交互控件**：
1. 时间范围 selectbox：`1小时 / 6小时 / 24小时 / 7天`
2. 品种 multiselect：所有品种混合，格式为 `[类别] 名称`（如 `[加密] BTC`、`[美股] 道琼斯`）；类别中文名复用页面已有的 `CLASS_NAMES` 字典，默认空选

**图表**：
- 使用 Plotly `go.Scatter`，每个品种一条线
- **归一化处理**：以所选时间窗口内该品种的第一个数据点为基准，转换为相对涨跌幅（%），解决不同量级品种（BTC vs 标普）无法直接叠加的问题
- X 轴：时间，Y 轴：涨跌幅（%）
- 无选中品种时隐藏图表，显示提示文字

**数据查询**：
```python
session.query(PriceSnapshot)
    .filter(PriceSnapshot.symbol.in_(selected_symbols))
    .filter(PriceSnapshot.timestamp >= cutoff)
    .order_by(PriceSnapshot.timestamp.asc())
```

**缓存**：`@st.cache_data(ttl=120)`，以 `(selected_symbols_tuple, time_range)` 为 key

**位置**：图表区域置于所有 metric 卡片之后、`st.expander`（详细数据表）之前，即 `st.markdown("---")` 和 expander 之间插入。

**不改动**：现有 metric 卡片布局、数据库模型

---

## 实现顺序建议

1. **Polymarket 过滤**（最简单，纯配置+过滤逻辑）
2. **跨资产时序图**（独立 UI 改动）
3. **LLM 打分**（新增外部依赖，需要 API Key 配置）
