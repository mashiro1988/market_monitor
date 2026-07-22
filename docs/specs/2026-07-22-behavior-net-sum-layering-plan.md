# 行为面板第三幅净幅分层 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把行为面板第三幅"涨/跌段净幅合计"柱拆成 强段（冲击力0.5档+）/ 弱段（仅0.3档）两层，让阴跌的累计贡献可见。

**Architecture:** 后端 `day_direction_extras` 增加两个 compute-on-read 读数（强段涨/跌净幅Σ）随 daily 接口下发；前端由"总−强"求弱段，第三幅改四段实心堆叠柱。不动段检测、档位、PIT 落库、告警。

**Tech Stack:** FastAPI + pydantic schema（自动生成 TS 类型）、SQLAlchemy/SQLite、React + recharts、pytest、vitest。

**依据规格:** `docs/specs/2026-07-22-behavior-net-sum-layering-design.md`（已评审批准）

---

## 环境与纪律（每个任务开始前先读）

- **Python 一律用 `D:\anaconda\python.exe`**：裸 `python` 在本机是坏的占位程序（退出码 49）。
  `npm run build` 内部会调 `python scripts/generate_openapi_types.py`——若因此失败，先手动
  `D:\anaconda\python.exe scripts/generate_openapi_types.py` 再 `cd frontend && npx tsc -b && npx vite build`。
- **命令均为 Git Bash 语法**（Claude 的 Bash 工具执行；PowerShell 5.1 无 `&&`）。
- **每次 git commit 前**：按根目录 AGENTS.md 规矩快速扫 4 张地图（ARCHITECTURE/DATAFLOW/DECISIONS/PENDING.md），
  漂移当场修。地图是**本地私有文件（.gitignore 已排除），只更新、绝不 commit**。
- 本地 `market_monitor.db` 是滞后备份，**不要覆盖、不要写**；验证用线上快照另存单独文件。

---

### Task 1: 后端 — day_direction_extras 强/弱拆分 + schema 字段

**Files:**
- Modify: `services/behavior_classifier.py:237-272`（`day_direction_extras`）
- Modify: `schemas/behavior.py:51-66`（`BehaviorDailySchema`）
- Test: `tests/test_behavior_api.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_behavior_api.py` 末尾追加（文件已 import `datetime/timedelta/pytest/bc`）：

```python
def test_day_direction_extras_strong_weak_split(client_session):
    """净幅分层（2026-07-22 设计稿）：强段=tier_idx>=1，弱段=tier_idx==0；
    空日/单向日返回 0.0；字段经 daily 接口透传。
    注：net_pct 列非空（models/behavior.py nullable=False），None 进不了库——
    实现里的 is not None 只是纯防御，不造库内用例（设计稿 §7 第 4 条据此放弃）。"""
    client, session = client_session
    from models.behavior import BehaviorSegment

    def seg(start, direction, tier_idx, net):
        return BehaviorSegment(
            symbol="BTC/USDT", start_dt=start, end_dt=start + timedelta(minutes=30),
            direction=direction, tier_idx=tier_idx, tier_max=[0.3, 0.5, 0.8][tier_idx],
            net_pct=net, amp_pct=abs(net),
            key_ts=start, classification="count_only", class_version="v2")

    d0 = datetime(2026, 1, 15, 3, 0)
    session.add_all([
        seg(d0, +1, 1, 0.55),                        # 强涨（0.5 档压线）
        seg(d0 + timedelta(hours=1), +1, 2, 1.0),    # 强涨（0.8 档）
        seg(d0 + timedelta(hours=2), +1, 0, 0.35),   # 弱涨
        seg(d0 + timedelta(hours=4), -1, 1, -0.6),   # 强跌
        seg(d0 + timedelta(hours=5), -1, 0, -0.45),  # 弱跌
        seg(d0 + timedelta(hours=6), -1, 0, -0.9),   # 弱跌
    ])
    session.add(seg(datetime(2026, 1, 16, 3, 0), -1, 0, -0.4))   # 单向日：只有弱跌
    session.commit()

    ex = bc.day_direction_extras(session, "BTC/USDT", "2026-01-15")
    assert ex["up_net_sum"] == pytest.approx(1.9)
    assert ex["up_net_sum_strong"] == pytest.approx(1.55)
    assert ex["down_net_sum_strong"] == pytest.approx(-0.6)
    ex1 = bc.day_direction_extras(session, "BTC/USDT", "2026-01-16")
    assert ex1["up_net_sum_strong"] == 0.0 and ex1["down_net_sum_strong"] == 0.0
    ex2 = bc.day_direction_extras(session, "BTC/USDT", "2026-01-17")   # 空日
    assert ex2["up_net_sum_strong"] == 0.0 and ex2["down_net_sum_strong"] == 0.0
    day = client.get("/api/behavior/daily?days=1").json()["days"][0]   # 接口透传（当日无段 → 0.0）
    assert day["up_net_sum_strong"] == 0.0 and day["down_net_sum_strong"] == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_behavior_api.py::test_day_direction_extras_strong_weak_split -v`
Expected: FAIL，`KeyError: 'up_net_sum_strong'`

- [ ] **Step 3: 最小实现**

`services/behavior_classifier.py` · `day_direction_extras` 循环改为（保持现有行为不变，只加强段累计；
注意强段累计要在"只看构成段"的 `continue` **之后**、情绪过滤之前）：

```python
    up_sum = 0.0
    up_strong = down_strong = 0.0
    sent_up = sent_down = 0
    sent_up_sum = sent_down_sum = 0.0
    for r in rows:
        if r.direction > 0 and r.net_pct is not None:
            up_sum += r.net_pct
        if r.tier_idx is None or r.tier_idx < 1:
            continue                                   # 情绪拆分与强段Σ都只看构成段（0.5 档以上）
        if r.net_pct is not None:                      # 强段净幅Σ（净幅分层 2026-07-22）
            if r.direction > 0:
                up_strong += r.net_pct
            else:
                down_strong += r.net_pct
        effective = to_window_class(r.human_class) or to_window_class(r.classification)
        if effective != "sentiment_tech":
            continue
        if r.direction > 0:
            sent_up += 1
            sent_up_sum += r.net_pct or 0.0
        else:
            sent_down += 1
            sent_down_sum += r.net_pct or 0.0
    return {
        "up_net_sum": round(up_sum, 4),
        "up_net_sum_strong": round(up_strong, 4),
        "down_net_sum_strong": round(down_strong, 4),
        "sent_up": sent_up,
        "sent_down": sent_down,
        "sent_up_net_sum": round(sent_up_sum, 4),
        "sent_down_net_sum": round(sent_down_sum, 4),
    }
```

`schemas/behavior.py` · `BehaviorDailySchema`，在 `sent_down_net_sum` 之后加：

```python
    up_net_sum_strong: float | None = None    # 强段（tier_idx≥1）涨净幅Σ ≥0（净幅分层 2026-07-22）
    down_net_sum_strong: float | None = None  # 强段跌净幅Σ ≤0（负值约定同 down_net_sum；弱段=总−强 由前端求）
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `D:/anaconda/python.exe -m pytest tests/test_behavior_api.py tests/test_behavior_classifier.py -v`
Expected: 全 PASS（新增 1 个 + 原有全绿）

- [ ] **Step 5: 扫地图（本地）后提交**

```bash
git add services/behavior_classifier.py schemas/behavior.py tests/test_behavior_api.py
git commit -m "feat(behavior): strong/weak net-sum split in daily extras"
```

---

### Task 2: 前端 — 类型再生成 + buildDailyRows 拆分（TDD）

**Files:**
- Regenerate: `frontend/src/api/types.ts`（自动生成，勿手改）
- Modify: `frontend/src/pages/behaviorFormat.ts:8-35, 37-81`
- Test: `frontend/src/pages/behaviorFormat.test.ts`

- [ ] **Step 1: 再生成 TS 类型**

Run: `D:/anaconda/python.exe scripts/generate_openapi_types.py`
验证: `grep -n "net_sum_strong" frontend/src/api/types.ts` → `BehaviorDailySchema` 内出现两个新字段

- [ ] **Step 2: 写失败测试**

`behaviorFormat.test.ts` 现有 `buildDailyRows` 用例的 day 对象里补两个输入字段：

```ts
        down_net_sum: -3.87, up_net_sum: 2.41,
        up_net_sum_strong: 1.55, down_net_sum_strong: -0.6,
```

`toMatchObject` 断言块后追加：

```ts
    expect(rows[0].upSumStrong).toBe(1.55);
    expect(rows[0].upSumWeak).toBeCloseTo(0.86, 4);
    expect(rows[0].downSumStrongNeg).toBe(-0.6);
    expect(rows[0].downSumWeakNeg).toBeCloseTo(-3.27, 4);
```

同一 describe 里再加一个钳位用例（4 位舍入残差不得出现反号毛刺）：

```ts
  it("clamps weak layer at zero when rounding residue flips sign", () => {
    const rows = buildDailyRows({
      symbol: "BTC/USDT",
      days: [{
        utc_date: "2026-07-09", day_type: "weekday", live: true,
        counts: {}, composition: {},
        down_net_sum: -0.5, up_net_sum: 1.0,
        up_net_sum_strong: 1.0001, down_net_sum_strong: -0.5001,
        computed_at: tf("2026-07-09T10:00:00", "2026-07-09 18:00:00"),
      }],
    } as any);
    expect(rows[0].upSumWeak).toBe(0);
    expect(rows[0].downSumWeakNeg).toBe(0);
  });
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/behaviorFormat.test.ts`
Expected: FAIL（`upSumStrong` 为 undefined）

- [ ] **Step 4: 最小实现**

`behaviorFormat.ts` · `DailyRow` 类型（`upSum` 行后）加：

```ts
  upSumStrong: number;      // 强段(0.5档+)涨净幅Σ（≥0，亮层）
  upSumWeak: number;        // 弱段(0.3档)涨净幅Σ（≥0，暗层=总−强，钳位到 0）
  downSumStrongNeg: number; // 强段跌净幅Σ（≤0，亮层）
  downSumWeakNeg: number;   // 弱段跌净幅Σ（≤0，暗层）
```

`buildDailyRows` 返回对象（`upSum` 行后）加：

```ts
      upSumStrong: Math.abs(d.up_net_sum_strong ?? 0),
      upSumWeak: Math.max(0, Math.abs(d.up_net_sum ?? 0) - Math.abs(d.up_net_sum_strong ?? 0)),
      downSumStrongNeg: -Math.abs(d.down_net_sum_strong ?? 0),
      downSumWeakNeg: Math.min(0, Math.abs(d.down_net_sum_strong ?? 0) - Math.abs(d.down_net_sum ?? 0)),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/pages/behaviorFormat.test.ts`
Expected: PASS（全文件）

- [ ] **Step 6: 扫地图（本地）后提交**

```bash
git add frontend/src/api/types.ts frontend/src/pages/behaviorFormat.ts frontend/src/pages/behaviorFormat.test.ts
git commit -m "feat(frontend): strong/weak net-sum fields in daily rows"
```

---

### Task 3: 前端 — 第三幅改四段实心堆叠柱

**Files:**
- Modify: `frontend/src/pages/BehaviorPage.tsx:23-30`（颜色常量区）、`:83-94`（第三幅）

- [ ] **Step 1: 加暗色常量**

`DOWN` 常量行后加（色值已过可视化校验：与亮色 ΔE≥17、对深底对比度≥3:1）：

```tsx
const UP_DIM = "#2f9e88";    // 弱段涨（暗青）
const DOWN_DIM = "#ad4159";  // 弱段跌（暗玫红）
```

- [ ] **Step 2: 改第三幅**

小标题行改为：

```tsx
              <div className="mini-title">涨/跌段净幅合计（%）· 亮=强段(0.5档+) 暗=弱段(0.3档)</div>
```

原两个 `<Bar dataKey="upSum" ... />`、`<Bar dataKey="downSumNeg" ... />` 替换为四个**实心**
Bar（去掉 opacity；强段先声明=贴零轴，弱段在外侧；`stackOffset="sign"` 与 stackId 不动）：

```tsx
                  <Bar isAnimationActive={false} dataKey="upSumStrong" name="涨·强段Σ" stackId="n" fill={UP} />
                  <Bar isAnimationActive={false} dataKey="upSumWeak" name="涨·弱段Σ" stackId="n" fill={UP_DIM} />
                  <Bar isAnimationActive={false} dataKey="downSumStrongNeg" name="跌·强段Σ" stackId="n" fill={DOWN} />
                  <Bar isAnimationActive={false} dataKey="downSumWeakNeg" name="跌·弱段Σ" stackId="n" fill={DOWN_DIM} />
```

- [ ] **Step 3: 全量构建**

Run: `cd frontend && npm run build`（若 `python` 解析失败见"环境与纪律"的手动兜底）
Expected: 生成 `frontend/dist`，无 TS/构建错误

- [ ] **Step 4: 跑全部前端测试回归**

Run: `cd frontend && npx vitest run`
Expected: 全 PASS

- [ ] **Step 5: 扫地图（本地）后提交**

```bash
git add frontend/src/pages/BehaviorPage.tsx
git commit -m "feat(frontend): panel-3 net-sum bars layered by strong/weak, solid fill"
```

---

### Task 4: 线上快照验证（07-20 实测对账）

**Files:**
- Create: `.claude/launch.json`（若无）
- 数据: `data/mm-live.db`（线上快照，只读用途；**不覆盖 `market_monitor.db`**）

- [ ] **Step 1: 拉线上库快照**（既有免密流程，Bash 工具执行）

```bash
ssh -o BatchMode=yes mmon "/opt/market_monitor/.venv/bin/python -" <<'EOF'
import sqlite3, os
src = sqlite3.connect("file:/opt/market_monitor/market_monitor.db?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/mm_snapshot.db"); src.backup(dst); dst.close(); src.close()
print("ok", os.path.getsize("/tmp/mm_snapshot.db"))
EOF
scp -o BatchMode=yes mmon:/tmp/mm_snapshot.db D:/market_monitor/data/mm-live.db
ssh -o BatchMode=yes mmon "rm -f /tmp/mm_snapshot.db"
```

- [ ] **Step 2: 用快照起本地服务**（preview 工具，不用 Bash 起服务）

`.claude/launch.json` 配置（cmd 包一层以注入 DATABASE_URL；端口 8000 = run.py app 固定值）：

```json
{
  "version": "0.0.1",
  "configurations": [{
    "name": "mm-live-snapshot",
    "runtimeExecutable": "cmd",
    "runtimeArgs": ["/c", "set DATABASE_URL=sqlite:///D:/market_monitor/data/mm-live.db&& D:\\anaconda\\python.exe run.py app"],
    "port": 8000
  }]
}
```

preview_start `{name: "mm-live-snapshot"}` → 浏览器开 `http://127.0.0.1:8000` 行为面板页。
备注：`run.py app` 启动会执行 create_tables（对快照只补建缺失表、只加不删，无数据风险）
并尝试自动弹系统浏览器——均属预期，不用排查。

- [ ] **Step 3: 对账 07-20**

第三幅 07-20 悬浮读数应为（设计稿 §1，±0.01）：
涨·强段Σ ≈ +3.995，涨·弱段Σ ≈ +1.352，跌·强段Σ ≈ −0.557，跌·弱段Σ ≈ −4.451；
柱总高与改造前一致（涨 +5.347 / 跌 −5.008）；第一、二幅与改造前肉眼无差。

- [ ] **Step 4: 截图留证**

浏览器截图（含 07-20 悬浮框）作为验收证据贴回会话；关闭服务（preview_stop）。

---

### Task 5: 文档与词表收尾

**Files:**
- Modify（本地，不 commit）: `ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md` / `PENDING.md`
- Modify（入库）: `GLOSSARY.md`

- [ ] **Step 1: 同步四张地图（本地私有）**

- DATAFLOW：daily 接口读数清单加 `up_net_sum_strong/down_net_sum_strong`（compute-on-read）
- DECISIONS：一条"2026-07-22 第三幅净幅分层（强/弱段），拒了净幅重定档方案"（含指向设计稿）
- PENDING：本特性移入已完成/待部署区
- ARCHITECTURE：若有行为面板/读层函数清单则对齐，无相关条目可不动

- [ ] **Step 2: GLOSSARY.md 补词**（每词三行：是什么/本项目为何用/在哪个文件）

先读现有词表避免重复；候补：PIT、compute-on-read（读时现算）、pytest、vitest、堆叠柱（stacked bar）。

- [ ] **Step 3: 提交（只含 GLOSSARY）**

```bash
git add GLOSSARY.md
git commit -m "docs(glossary): terms from net-sum layering work"
```

- [ ] **Step 4: 汇报**

向用户汇报：改动清单、测试结果、07-20 截图对账结论、部署提醒（服务器需 git pull + 前端构建 + 重启，
按既有部署 runbook；本次未动 DB/配置，无迁移步骤）。
