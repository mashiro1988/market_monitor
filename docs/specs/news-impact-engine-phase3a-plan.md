# Phase 3a — 标注层简化（taxonomy 3 值 + redundant 导出派生）Implementation Plan

> ⚠️ **2026-06-23 部分作废**：本 plan 的 **Task 3「导出时按 topic/量级派生 redundant」已被用户否决并回滚**（嫌"代表可能被换掉"太复杂）。现状以 spec `news-impact-engine-plan.md` §标注 为准：**redundant 由人/LLM 逐条直接标**，`_derive_export_roles` 与 `test_export_redundant.py` 已删除，输入校验收 driver/redundant（无 `INPUT_CAUSAL_ROLES`），prompt 版本 v6。Task 1（去退场角色，但保留 redundant 可输入）/ Task 2（迁移）/ Task 4（prompt 去 post_hoc/contradictory）/ Task 5（前端）仍有效。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把标注 causal_role 从 4 值（driver/noise/post_hoc_explanation/contradictory）收敛到 **3 值（driver/redundant/noise）**，其中 **redundant 在导出时由 Phase-1 topic/量级自动派生**，contradictory 与 post_hoc_explanation 退场（并入 noise）。

**Architecture:** 不改标注交互的根本形态（人仍逐条标 driver）。关键招：`redundant` 不进存储、不让人/LLM 直接标，而是**导出时**按"驱动主题里量级最大+最早那条=driver、同主题其余=redundant、其它主题=noise"派生。这样既落地 spec 的「driver/redundant/noise」语义，又免去标注页大改。A 策略（窗口 settle/走完门 + 改动推复核）属 **Phase 3b**，本计划不含。

**Tech Stack:** Python 3 / SQLAlchemy / pytest（本地 python 用 `D:\anaconda\python.exe`）；React/TS 前端只动一处下拉。依赖 **Phase 1 已完成**（news.topic / magnitude_tier 已入库 + 回灌）。

---

## 关键设计澄清（实现前必读）

1. **两套角色集，别混：**
   - `NEWS_CAUSAL_ROLES = (driver, redundant, noise)` = **导出/训练口径**（candidates[].causal_role 用它）。
   - `INPUT_CAUSAL_ROLES = (driver, noise)` = **人/LLM 可直接给的**。`redundant` 派生而来，**不可手标、不可由 LLM 直接输出**；`contradictory`/`post_hoc_explanation` 退场。
2. **redundant 派生规则（导出时，不落库）：**
   - 「驱动主题」= 任一被人标 `driver` 的候选所属 topic。
   - 每个驱动主题内：**a-priori 量级最大**（大>中>小），并列取**时间最早** → 该条 = `driver`（代表）；同主题其余候选 → `redundant`。
   - 非驱动主题 / 无 topic 的候选 → `noise`。**例外**：被人标 driver 但本身无 topic（Phase1 没打上）的候选，保留 `driver`（无法分组）。
   - 含义：人标 driver = 在指认「这个 topic 在驱动」；具体代表谁由量级+时间自动定（可能不是人点的那条）。
3. **负样本口径：** 导出里 `noise` = 负样本、`driver` = 正样本、`redundant` = **既不正也不负（下游训练排除）**。导出只负责把 role 标对，排除动作在消费端按 role 做。
4. **`labels.news_roles` 仍存人工原始 driver 标注**（溯源）；派生后的三值角色只进 `candidates[].causal_role`（训练信号）。两者分离、不互相覆盖。
5. **存量数据**：库里可能已有 `post_hoc_explanation`/`contradictory` 的 news_roles → 迁移步骤把它们从 news_roles 移除（= 归 noise）。幂等。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `schemas/annotations.py:11-16` | 角色枚举 | `NEWS_CAUSAL_ROLES`→(driver,redundant,noise)；新增 `INPUT_CAUSAL_ROLES`=(driver,noise) |
| `services/annotation_service.py:657` (`_normalize_v2_labels`) | 落库校验 | 校验改用 `INPUT_CAUSAL_ROLES` |
| `services/annotation_service.py:903` (`_extract_v2_labels`) | LLM 输出校验 | 同样用 `INPUT_CAUSAL_ROLES` 滤掉非法/幻觉角色 |
| `services/annotation_service.py:631` (`_parse_news_roles`) | 读库存角色 | **不要动**——读已存 3 值角色喂导出，须留 `NEWS_CAUSAL_ROLES` |
| `services/annotation_service.py:1211-1231` | 导出 | 新增 `_derive_export_roles`，候选 `causal_role` 用派生值 |
| `services/annotation_service.py:153 / 224 / 319` | LLM prompt | `AUTO_ANNOTATE_SYSTEM_PROMPT` / `AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT` 删 post_hoc/contradictory；bump `ANNOTATION_PROMPT_VERSION` |
| `database.py:98-159` | 迁移 | 加步骤 3：post_hoc_explanation/contradictory → 移除（归 noise），幂等 |
| `frontend/src/pages/AnnotationsPage.tsx:57-80` | 角色下拉 | `ROLE_OPTIONS` 删两项，仅留 噪音/驱动 |
| `tests/test_annotation_v2.py` 等 | 测试 | 更新被拒枚举集；新增派生/迁移单测 |

---

## Task 1: 角色枚举收敛到 3 值 + 输入校验改 INPUT 集

**Files:**
- Modify: `schemas/annotations.py:11-16`
- Modify: `services/annotation_service.py:657`（落库校验）、auto-parse 校验处（搜 `非法 causal_role` / `NEWS_CAUSAL_ROLES` 的第二处）
- Test: `tests/test_annotation_v2.py`

- [ ] **Step 1: 写失败测试（RED）**

在 `tests/test_annotation_v2.py` 加（或改）用例：保存 `news_roles={id:"contradictory"}` 与 `{id:"post_hoc_explanation"}` 应被拒（ValueError / 400）；`{id:"redundant"}` 也应被拒（不可手标）；`{id:"driver"}` 通过。

```python
def test_phase3a_rejects_retired_and_derived_roles(session):
    from services import annotation_service as A
    for bad in ("contradictory", "post_hoc_explanation", "redundant"):
        req = _req([123], news_roles={123: bad})          # _req = 本文件既有请求构造助手(line 56)
        with pytest.raises(ValueError):
            A._normalize_v2_labels(req)                   # 返回 5 元组的归一化函数(line 646)
```

- [ ] **Step 2: 跑 RED**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_v2.py -q -k phase3a`
Expected: FAIL（当前 contradictory/post_hoc 仍合法）。

- [ ] **Step 3: 改枚举**

`schemas/annotations.py:11-16` 替换为：
```python
# —— 标注角色（news-impact-engine Phase 3a）——
# 导出/训练口径三值；redundant 由 topic 派生（导出时算），不可手标/LLM 直出。
NEWS_CAUSAL_ROLES = (
    "driver",     # 驱动代表（驱动主题里量级最大+最早那条）
    "redundant",  # 同簇冗余（与 driver 同 topic 的其它报道；导出派生；训练时排除，不当负样本）
    "noise",      # 噪音（默认，不落库）
)
# 人/LLM 可直接给的角色（redundant 派生而来，不在此列）。
INPUT_CAUSAL_ROLES = ("driver", "noise")
```

- [ ] **Step 4: 落库校验改 INPUT 集**

`services/annotation_service.py:657`（函数 `_normalize_v2_labels`）：`if role not in NEWS_CAUSAL_ROLES:` → `if role not in INPUT_CAUSAL_ROLES:`。确认文件顶部 import 带上 `INPUT_CAUSAL_ROLES`（与 `NEWS_CAUSAL_ROLES` 同处 import，约 line 18）。

- [ ] **Step 5: auto-parse 校验改 INPUT 集**

`services/annotation_service.py:903`（函数 `_extract_v2_labels`）：`role in NEWS_CAUSAL_ROLES` → `role in INPUT_CAUSAL_ROLES`，使 LLM 若吐出 post_hoc/contradictory/redundant 被丢弃。测试在 `tests/test_auto_annotate_batch_parser.py`。
**注意：别动 `_parse_news_roles`（:631）**——它读库里已存的角色(3 值口径)喂给导出派生，必须留在 `NEWS_CAUSAL_ROLES`；blind grep-replace `NEWS_CAUSAL_ROLES` 会误伤它。

- [ ] **Step 6: 跑 GREEN + 该文件回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_v2.py tests/test_auto_annotate_batch_parser.py -q`
Expected: PASS（修掉本任务新测 + 原有被拒-旧枚举用例仍绿；若有用例显式断言 post_hoc/contradictory 合法，按新口径改成被拒）。

- [ ] **Step 7: Commit**
```bash
git add schemas/annotations.py services/annotation_service.py tests/test_annotation_v2.py
git commit -m "feat(news-engine): Phase3a causal_role 收敛 driver/redundant/noise + 输入只收 driver/noise"
```

---

## Task 2: 迁移存量 post_hoc/contradictory → noise

**Files:**
- Modify: `database.py:98-159`（`migrate_legacy_annotations` 加步骤 3 + 常量）
- Test: `tests/test_annotation_v2.py`

- [ ] **Step 1: 写失败测试（RED）**

```python
def test_migrate_drops_retired_roles(session):
    # 直接插一行 news_roles 含 post_hoc_explanation / contradictory，跑迁移后应只剩 driver
    ...
    n = migrate_legacy_annotations(session.connection())
    roles = json.loads(<重新读出的 news_roles>)
    assert set(roles.values()) <= {"driver"}            # 退场角色已被移除
```

- [ ] **Step 2: 跑 RED** — `pytest tests/test_annotation_v2.py -q -k migrate_drops` → FAIL。

- [ ] **Step 3: 实现迁移步骤 3**

`database.py`：在 `_REACTION_UPGRADE` 旁加常量，并在 `migrate_legacy_annotations` 末尾（`return changed` 之前）加步骤 3：
```python
# news-impact-engine Phase 3a：退场角色（并入 noise = 从 news_roles 移除）。
_RETIRED_ROLES = {"post_hoc_explanation", "contradictory"}
```
```python
    # 步骤 3：v2.1 → v3（Phase 3a）：post_hoc_explanation / contradictory 移除（归 noise），幂等。
    rows = conn.execute(text(
        "SELECT id, news_roles FROM news_price_annotations WHERE news_roles IS NOT NULL"
    )).fetchall()
    for row in rows:
        try:
            roles = _json.loads(row[1]) if row[1] else {}
        except (ValueError, TypeError):
            roles = {}
        new_roles = {k: v for k, v in roles.items() if v not in _RETIRED_ROLES}
        if new_roles != roles:
            conn.execute(
                text("UPDATE news_price_annotations SET news_roles = :roles WHERE id = :id"),
                {"roles": _json.dumps(new_roles, ensure_ascii=False), "id": row[0]},
            )
            changed += 1
```

- [ ] **Step 4: 跑 GREEN** — `pytest tests/test_annotation_v2.py -q` → PASS。

- [ ] **Step 5: Commit**
```bash
git add database.py tests/test_annotation_v2.py
git commit -m "feat(news-engine): Phase3a 迁移退场角色 post_hoc/contradictory→noise"
```

---

## Task 3: 导出按 topic/量级派生 driver/redundant/noise（核心）

**Files:**
- Modify: `services/annotation_service.py`（新增 `_derive_export_roles`；改 `export_training_jsonl` 候选构建 `:1211-1231`）
- Test: `tests/test_annotation_v2.py`（或新建 `tests/test_export_redundant.py`）

- [ ] **Step 1: 写派生逻辑的失败测试（RED）**

新建 `tests/test_export_redundant.py`：
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from services.annotation_service import _derive_export_roles


def _c(id, topic, mag, t):
    return {"id": id, "topic": topic, "magnitude": mag, "time": t}


def test_rep_is_biggest_then_earliest_rest_redundant():
    cands = [
        _c(1, "地缘冲突", "中", datetime(2026, 6, 1, 12, 0)),
        _c(2, "地缘冲突", "大", datetime(2026, 6, 1, 12, 10)),   # 量级最大 → driver
        _c(3, "地缘冲突", "大", datetime(2026, 6, 1, 12, 5)),    # 同为大但更早…
    ]
    # 人只标了 #1 是 driver（指认"地缘冲突"在驱动）
    out = _derive_export_roles(cands, {1: "driver"})
    # 代表 = 量级大且最早 = #3；#1/#2 → redundant
    assert out == {3: "driver", 1: "redundant", 2: "redundant"}


def test_non_driving_topic_is_noise():
    cands = [
        _c(1, "通胀数据", "大", datetime(2026, 6, 1, 12, 0)),     # 驱动主题
        _c(2, "加密生态", "大", datetime(2026, 6, 1, 12, 1)),     # 非驱动主题
    ]
    out = _derive_export_roles(cands, {1: "driver"})
    assert out == {1: "driver", 2: "noise"}


def test_driver_without_topic_stays_driver():
    cands = [_c(1, None, None, datetime(2026, 6, 1, 12, 0))]
    out = _derive_export_roles(cands, {1: "driver"})
    assert out == {1: "driver"}


def test_no_human_driver_all_noise():
    cands = [_c(1, "通胀数据", "大", datetime(2026, 6, 1, 12, 0))]
    assert _derive_export_roles(cands, {}) == {1: "noise"}
```

- [ ] **Step 2: 跑 RED** — `pytest tests/test_export_redundant.py -q` → FAIL（函数不存在）。

- [ ] **Step 3: 实现 `_derive_export_roles`**

加到 `services/annotation_service.py`（export 函数附近）：
```python
_MAGNITUDE_RANK = {"大": 3, "中": 2, "小": 1}
_TIME_MAX = datetime.max


def _derive_export_roles(candidates_meta: list[dict], human_roles: dict[int, str]) -> dict[int, str]:
    """人工 driver 标注 + Phase1 topic/量级 → 每条候选的导出角色 driver/redundant/noise。
    驱动主题里「量级最大、并列取最早」=driver 代表，同主题其余=redundant，其它/无topic=noise；
    人标 driver 但无 topic 的保留 driver（无法分组）。详见 phase3a-plan §关键设计澄清。"""
    out: dict[int, str] = {c["id"]: "noise" for c in candidates_meta}
    driving_topics = {c["topic"] for c in candidates_meta
                      if human_roles.get(c["id"]) == "driver" and c["topic"]}
    # 人标 driver 但无 topic：保留 driver
    for c in candidates_meta:
        if human_roles.get(c["id"]) == "driver" and not c["topic"]:
            out[c["id"]] = "driver"
    # 每个驱动主题选代表
    for topic in driving_topics:
        members = [c for c in candidates_meta if c["topic"] == topic]
        members.sort(key=lambda c: (-_MAGNITUDE_RANK.get(c["magnitude"], 0), c["time"] or _TIME_MAX))
        rep_id = members[0]["id"]
        for c in members:
            out[c["id"]] = "driver" if c["id"] == rep_id else "redundant"
    return out
```
确认文件已 `from datetime import datetime`（顶部已有 `datetime`/`timedelta` import，见现有代码）。

- [ ] **Step 4: 接进 `export_training_jsonl`**

`services/annotation_service.py:1219-1231`：在构建 `candidates` 前，用候选 NewsItem 的 topic/量级/时间组 `cand_meta`，算 `derived`，候选 `causal_role` 用派生值。**注意 `by_id` 这行是替换已存在的 :1219，不是新增**（别留重复定义）：
```python
        by_id = {n.id: n for n in news_rows}
        cand_meta = [{
            "id": nid,
            "topic": (by_id.get(nid).topic if by_id.get(nid) else None),
            "magnitude": (by_id.get(nid).magnitude_tier if by_id.get(nid) else None),
            "time": (by_id.get(nid).timestamp if by_id.get(nid) else None),
        } for nid in cand_ids]
        derived = _derive_export_roles(cand_meta, roles)
        candidates = []
        for nid in cand_ids:
            n = by_id.get(nid)
            candidates.append({
                "id": nid,
                "time_bj": (n.timestamp + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if n and n.timestamp else None,
                "source": n.source if n else None,
                "title": (n.title or "") if n else "",
                "content": (n.content or "")[:1000] if n else "",
                "llm_score": n.llm_importance if n else None,
                "causal_role": derived.get(nid, "noise"),     # ← 派生三值，非 roles.get
            })
```
`labels.news_roles`（:1250）**保持不变**（仍存人工原始标注，溯源）。

- [ ] **Step 5: 加一个端到端导出断言（可选但推荐）**

在 `tests/test_annotation_v2.py` 加：构造一个窗口 + 同 topic 两条候选（量级不同）+ 人标其一为 driver，跑 `export_training_jsonl(split="all")`，断言导出 candidates 里出现 `driver` 与 `redundant` 各一、无 `post_hoc/contradictory`。

- [ ] **Step 6: 跑 GREEN** — `pytest tests/test_export_redundant.py tests/test_annotation_v2.py -q` → PASS。

- [ ] **Step 7: Commit**
```bash
git add services/annotation_service.py tests/test_export_redundant.py tests/test_annotation_v2.py
git commit -m "feat(news-engine): Phase3a 导出按 topic/量级派生 driver/redundant/noise"
```

---

## Task 4: LLM prompt 删退场角色 + 版本号

**Files:**
- Modify: `services/annotation_service.py`（单窗 prompt `:154-220`、批量 prompt `:224-312`、`ANNOTATION_PROMPT_VERSION :319`）
- Test: `tests/test_annotation_v2.py`（prompt 守卫）

- [ ] **Step 1: 写 prompt 守卫测试（RED）**
```python
def test_prompts_drop_retired_roles():
    from services import annotation_service as A
    for p in (A.AUTO_ANNOTATE_SYSTEM_PROMPT, A.AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT):   # 模块级常量(:153/:224)
        assert "post_hoc" not in p and "contradictory" not in p
    assert A.ANNOTATION_PROMPT_VERSION != "v4-20260612"
```
（两份 prompt 都是模块级常量，直接属性访问即可。）

- [ ] **Step 2: 跑 RED** → FAIL。

- [ ] **Step 3: 改 prompt 文本**

`AUTO_ANNOTATE_SYSTEM_PROMPT`（单窗）退场角色文本在 **165-166, 179, 210, 216**；`AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT`（批量）在 **232-233, 261, 292, 303**：
- causal_role 候选描述只留 **driver / noise** 两类（删 post_hoc_explanation、contradictory 两行：单窗 165-166、批量 232-233）。
- 决策步骤里 "行情综述 / 收盘总结标 post_hoc_explanation，方向相反的消息标 contradictory" 整句删掉（单窗 210、批量 292），改为 "综述/收盘总结/解释性、离题消息一律默认 noise（不必单列）"。
- 输出格式 JSON `"driver|post_hoc_explanation|contradictory"` → `"driver"`（单窗 **:216**、批量 **:303**；注释保留"只列非 noise"）。
- `ANNOTATION_PROMPT_VERSION`（:319，现 `"v4-20260612"`）改为 `"v5-20260622"`。

- [ ] **Step 4: 跑 GREEN** — `pytest tests/test_annotation_v2.py -q -k prompts_drop` → PASS。

- [ ] **Step 5: Commit**
```bash
git add services/annotation_service.py tests/test_annotation_v2.py
git commit -m "feat(news-engine): Phase3a prompt 去 post_hoc/contradictory + 版本号 v5"
```

---

## Task 5: 前端角色下拉只留 噪音/驱动

**Files:**
- Modify: `frontend/src/pages/AnnotationsPage.tsx:57-80`

- [ ] **Step 1: 改 `ROLE_OPTIONS`**
```typescript
const ROLE_OPTIONS = [
  { value: "noise", label: "噪音" },
  { value: "driver", label: "驱动" },
] as const;
```
（删 post_hoc_explanation / contradictory 两项。`REACTION_OPTIONS`、`CONFIDENCE_TIERS` 不动。）

- [ ] **Step 2: 自检无残留**

Grep 前端 `post_hoc_explanation` / `contradictory` 应无残留引用（若有渲染映射/中文标签表也一并删）。

- [ ] **Step 3: 构建前端**

Run: `D:\anaconda\python.exe run.py frontend-build`（或仓库既有前端构建命令；失败则按仓库 README）。
Expected: 构建通过、无 TS 报错。

- [ ] **Step 4: Commit**
```bash
git add frontend/src/pages/AnnotationsPage.tsx frontend/dist
git commit -m "feat(news-engine): Phase3a 标注页角色下拉收敛为 噪音/驱动"
```

---

## Task 6: 全套回归 + spec 状态

**Files:**
- Modify: `docs/specs/news-impact-engine-plan.md`（Phase 3 状态）

- [ ] **Step 1: 全套** — `D:\anaconda\python.exe -m pytest tests/ -q` → 全绿；非绿则按新口径修测试断言（不回改实现）。
- [ ] **Step 2: spec 标注**：`### Phase 3` 标题或条目下注明 "3a（taxonomy 3 值 + redundant 导出派生）已实现；3b（A 策略窗口门）待做"。细化 plan 指针指向本文件。
- [ ] **Step 3: Commit** — `docs(news-engine): Phase3a 标已实现`。

---

## 验证总览
- Task 1-4 每个 RED→GREEN 自带单测；Task 3 的 `_derive_export_roles` 是核心、4 个派生用例覆盖代表选取/非驱动/无topic/无driver。
- Task 5 前端无单测 → 构建通过 + grep 无残留。
- A 策略（窗口 settle/走完门、已标窗口被 backfill 改动推复核）= **Phase 3b**，单独 plan。
- 依赖 Phase 1（topic/magnitude_tier 已入库）；历史窗口若候选未打 Phase1 标，派生会把它们当无-topic（保留人标 driver / 否则 noise），不报错。
