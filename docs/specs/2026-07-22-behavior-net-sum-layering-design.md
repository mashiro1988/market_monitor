# 行为面板第三幅 · 净幅合计按强/弱段分层（设计稿）

- 日期：2026-07-22　状态：待评审　发起：用户（07-20 阴跌案例复盘）
- 关联：docs/specs/price-behavior-engine-plan.md（段/档位/日汇总口径）、price-behavior-engine-phase2-plan.md（行为面板）

## 1. 背景与问题

日趋势第二幅"强度"按段内**峰值 15 分钟变动**定档（冲击力口径）。长时间阴跌段——如
2026-07-20 04:40–05:35 净跌 0.927% 但 15 分钟力度从未触及 0.5 档——被归入 0.3 档，
在强度图上与一个 15 分钟 −0.31% 的小段无法区分。07-20 实测：

| 口径 | 涨 | 跌 |
|---|---|---|
| 0.3 档段数 | 10 | 9 |
| 冲击力 ≥0.5 档段数 | 6 | 1 |
| 净幅合计 | +5.347% | −5.008% |
| 其中冲击力 ≥0.5 档贡献 | +3.995% | −0.557% |
| 其中仅 0.3 档贡献 | +1.352% | −4.451% |

跌方 −5.0% 里有 −4.45% 来自 0.3 档碎步段（阴跌），现有三幅图都不直接展示这一构成。
当日实际净涨 +0.71%（64700→65158），"多头脉冲、空头阴磨"的结构只能靠人工对账推出。

## 2. 目标 / 非目标

**目标**：把第三幅"涨/跌段净幅合计"每根柱拆成两层，显性回答"这根柱子由谁贡献"：
- 亮层 = **强段**贡献（冲击力 0.5 档及以上的段的净幅合计）；
- 暗层 = **弱段**贡献（仅触及 0.3 档的段的净幅合计）。

**非目标**（明确不做）：
- 不改段检测、档位阈值（0.3/0.5/0.8）、合并/稀释规则、告警、分类管线；
- 不改 PIT 日汇总的落库结构（behavior_daily_summaries 不加列）；
- 不动第一幅（0.3 档计数+净差线）与第二幅（冲击力强度）；
- 不引入"按净幅重新定档"的第二套档位口径（方案 A 已否）。

## 3. 口径定义

- **强段** = `tier_idx >= 1`（冲击力触及 0.5 档及以上）。与第二幅强度图、"构成段"
  （behavior_classifier.day_direction_extras 中情绪拆分的分母）完全同一批段，可互相对账。
- **弱段** = `tier_idx == 0`（仅 0.3 档）。
- 恒等式：`强段净幅Σ + 弱段净幅Σ ≡ 现有总净幅Σ`（涨、跌两侧各自成立）。
  柱高、方向（涨在上/跌在下）、总量与现状完全一致，只是柱内分层。
- 净幅与档位在段落地时即固定（settle 后不变，人工改判只动分类不动 net_pct/tier_idx），
  因此拆分值可安全地读时现算（compute-on-read），历史日与盘中同口径。

## 4. 接口变更（后端）

`services/behavior_classifier.day_direction_extras()` 增加两个返回键，随
`BehaviorDailySchema`（schemas/behavior.py）新增字段下发：

| 字段 | 含义 | 符号/精度 |
|---|---|---|
| `up_net_sum_strong` | 当日强段（tier_idx≥1、direction>0）net_pct 之和 | ≥0，round 4 位 |
| `down_net_sum_strong` | 当日强段（direction<0）net_pct 之和 | ≤0（沿用 down_net_sum 负值约定），round 4 位 |

- 弱段值不下发，前端用 `总 − 强` 求得（两者均 4 位舍入，残差 ≤1e-4 可忽略，前端钳位到 0）。
- `net_pct is None` 的段按 0 处理（与现有 up_net_sum 逻辑一致）。
- 无段日两字段为 0.0。PIT 历史行照常提供总量（counts/down_net_sum），extras 仍逐日现算——
  与现有 up_net_sum/sent_* 的读取路径完全相同，不新增查询。

## 5. 前端变更（frontend/src）

**api/types.ts · BehaviorDailySchema（TS 镜像）**：补 `up_net_sum_strong` / `down_net_sum_strong`
两个可选字段（不加则 buildDailyRows 通不过类型检查，`npm run build` 失败）。

**behaviorFormat.ts · DailyRow 新增：**

| 字段 | 计算 |
|---|---|
| `upSumStrong` | `Math.abs(up_net_sum_strong ?? 0)` |
| `upSumWeak` | `max(0, upSum − upSumStrong)` |
| `downSumStrongNeg` | `-Math.abs(down_net_sum_strong ?? 0)` |
| `downSumWeakNeg` | `min(0, downSumNeg − downSumStrongNeg)` |

**BehaviorPage.tsx · 第三幅**改为四段堆叠（stackId 不变，仍 stackOffset="sign"）：

| Bar | dataKey | 颜色 | 名称（tooltip） |
|---|---|---|---|
| 涨·强段 | upSumStrong | `#5eead4`（现 UP） | 涨·强段Σ |
| 涨·弱段 | upSumWeak | `#2f9e88` | 涨·弱段Σ |
| 跌·强段 | downSumStrongNeg | `#fb7185`（现 DOWN） | 跌·强段Σ |
| 跌·弱段 | downSumWeakNeg | `#ad4159` | 跌·弱段Σ |

- 暗色两值已按可视化校验流程验证：与相应亮色的区分度 ΔE≥17（含常见色觉缺陷模拟）、
  对深底对比度 ≥3:1。堆叠顺序：强段贴零轴，弱段在外侧。
- 四个 Bar **实心渲染**（去掉现柱的 `opacity={0.75}`）：分层后颜色本身承载语义，
  且上述校验按原始色值进行；带透明度会把暗色压到对比度线以下。
- 小标题改为：`涨/跌段净幅合计（%）· 亮=强段(0.5档+) 暗=弱段(0.3档)`。
- 悬浮提示（recharts Tooltip）自动按四个 name 分行显示，无额外开发。

## 6. 边界与一致性

- 恒等式保证图形总高不变；07-20 之外的历史日无需回填——现算即得。
- live（盘中现算）与 PIT（已固化）两条路径共用 extras，行为一致。
- 强段定义引用 `tier_idx >= 1` 而非字面 0.5：跨资产阈值不同（BEHAVIOR_TIERS），
  一旦扩展到非 BTC 品种口径依然成立。
- 盘中注意：未 settle 的强段已计入强段Σ，但尚未进"构成段"分母（classification 未落，
  settle 后自愈）。当日两读数可短暂不等，测试不得在盘中日断言二者相等。

## 7. 测试计划

**后端（pytest，扩展 tests/test_behavior_api.py 或新增用例）：**
1. 混合日：强涨/弱涨/强跌/弱跌各若干 → 四个数值正确、恒等式成立；
2. 压线段：tier_idx==1（恰 0.5 档）计入强段；tier_idx==0 计入弱段；
3. 空日 → 两字段 0.0；单方向日 → 另一侧 0.0；
4. ~~net_pct=None 段不炸、按 0 计~~（计划评审更正：net_pct 列非空约束，None 进不了库；
   实现保留 `is not None` 纯防御，不设库内用例）。

**前端：** `npm run build` 通过；核对前先按既有 `ssh mmon` 快照流程拉一份线上库
备份到本地（本地库滞后、无 07-20 段数据），再起服务在浏览器核对 07-20 柱：
涨 = 亮 +4.0 / 暗 +1.35，跌 = 亮 −0.56 / 暗 −4.45，总高与改造前一致。

## 8. 验收标准

- 07-20 四层读数与第 1 节表格一致（±0.01）；
- 任意日亮+暗 = 改造前柱高；第一、二幅像素级不变；
- 后端测试全绿；前端构建无错。

## 9. 文档同步（与实现同一 commit）

ARCHITECTURE.md / DATAFLOW.md / DECISIONS.md / PENDING.md 四张地图按仓库规矩同步；
会话中新解释的术语（PIT、pytest、compute-on-read、堆叠柱）补进 GLOSSARY.md。
