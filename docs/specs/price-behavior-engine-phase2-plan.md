# 价格行为引擎 Phase 2 —— 工作台/结论页重组 Implementation Plan

> **状态：已完成（2026-07-10 全部 8 个任务落地 main，88811ea…P2-T8 收尾提交）。** 设计对齐于 2026-07-09（用户三轮口述 + 答疑）。Phase 1 = `price-behavior-engine-plan.md`（已上线）。落地与计划的偏差：无（同步相关整链删除含 `window_signals.pearson_correlation`、`ReferenceChange.correlation`、UI/tests 全链；prompt 版本 `v12-20260710`）。

**Goal:** 页面职责重组——**标注页 = 工作台**（0.5+ 段窗口 + 0.3 簇拥上下文 + S 证据 + 窗口三类标注 = 人工审核本身）；**行为面板 = 结论页**（日趋势 + 三类构成）；**S 计算统一为 rolling 一套**（判级读数 = 屏幕曲线的峰值，所见即所判）。

---

## 锁定的设计（2026-07-09 用户拍板）

1. **S 只留 rolling（30 点拖尾）**。段的判级读数 = "段起 → 段止 + 1h" 内 rolling 曲线峰值（逐参照取各自峰，最强者按 HI/MID cutoff 判级；ESS/coverage 取峰值时刻所在窗口的值）。`s_score` 事件窗路径删除；校准脚本改同口径错位对照。**所见即所判：曲线爬多高，机器判多强。**
2. **标注单位 = 0.5 档及以上段**（0.3 段不标注）。0.3 段作上下文：
   - 窗口列表加"±1h 簇拥 0.3 段 ×N"列（渐进式共振的量化提示）；
   - 跨资产图档位色带：**全部段**（0.3/0.5/0.8）按方向色 × 档位深浅覆盖，呈现"小推 → 爆发"的形态。
3. **窗口级三类标注**（= 人工审核，替代行为面板的确认按钮）：`news_driven`（有唯一 driver）/ `pure_resonance`（纯宏观共振，无具体新闻）/ `sentiment_tech`（情绪/技术面异动）。保存标注时回写段 `human_class`。机器六类保留在底层，展示/聚合归并三类（macro_news/industry_news/no_ref_news → news_driven；pure_resonance → pure_resonance；sentiment/no_ref_pending → sentiment_tech；无对照加注记）。
4. **新闻角色体系不动**：driver 唯一 + redundant + noise（DeepSeek 自动标 + 多轮 refine 沿用，训练导出语义不变）。
5. **行为面板 = 结论页**：保留日趋势四联图；新增三类构成结论卡（14d 计数/占比趋势，human_class 优先）；撤段明细表、联动曲线、审核按钮（联动曲线搬去标注页）。
6. **标注关联迁移**：段边界（0.3 基座合并）≠ 旧 0.5 单档窗口边界 → `annotation_id` 关联从 `(start,end)` 精确匹配改为**区间重叠匹配（重叠 ≥50%）**，历史标注不丢。
7. **开关退役**：`BEHAVIOR_REPLACES_ANNOTATION_WINDOWS` 删除，标注页固定段源（不再两套窗口口径并行）。
8. **同步相关概念退役（2026-07-09 追加）**：标注页对标行不再显示 correlation 与前/后段，简化为**绝对起点 → 终点 + 窗口内涨跌幅**（收益率类照旧 bp）；时序上下文由 rolling S 曲线 + 档位色带承担。`ReferenceChange` 去 `correlation`、加 `price_start/price_end`；**payload 的 `reference_change_segments` 保留**（prompt 方向链交叉验证仍用）；后端 `window_signals.pearson_correlation` / `_reference_correlations_for_window` 整链删除（v11 起已无 payload 消费者，本期 UI 消费者也退役）。

## 任务分解（开工时按 repo 惯例展开 TDD 步骤）

### Task 1 — rolling 读数统一（后端核心）
- `resonance_score.py`：新增 `rolling_peak(btc_chg, ref_chg, t_btc, t_ref, seg_start, seg_end, tail_min=60, points=30)` → `(peak_s, ess_at_peak, coverage_at_peak) | None`（在 rolling_s 序列上取 max|S|）；删除 `s_score` 事件窗函数。
- `behavior_classifier.classify` / `annotation_service._window_signals_payload` / `behavior_calibration` 全部改用 rolling_peak；spec §6 数值 fixture 按新口径重算并更新测试。
- settle 语义微调：读数尾窗 = 段止 + 1h + rolling 窗自身跨度（曲线要完全滑过），settle 门槛相应 +145min 复核。

### Task 2 — 三类化
- `human_class` 取值改三类（REVIEWABLE_CLASSES → news_driven/pure_resonance/sentiment_tech）；六类→三类归并 helper（展示/聚合共用）；`aggregate_day` 构成按三类（human 优先、机器映射）；历史 PIT 行是六类口径 → 读取时归并、不重写历史。

### Task 3 — 标注保存回写
- `AnnotationCreateRequest` 加 `window_class`（三类，新保存必填）；`upsert_annotation` 按区间重叠（≥50%）定位对应段、写 `human_class`；找不到段时只存标注不报错（并行期兜底）。

### Task 4 — 标注页窗口源固定段化
- `load_price_windows` 固定读 `behavior_segments`（tier_idx≥1），删开关与旧扫描路径（保留显式 threshold 调试参数走原始扫描）；`PriceWindowSchema` 加 `tier_idx/tier_max/net_pct/s_peak/ess/machine_class/cluster03_count`；`annotation_id` 重叠匹配。
- `cluster03_count` = 段起止 ±1h 内 0.3 档段数。

### Task 5 — 标注页 UI（工作台化）
- 窗口列表样式段化（档位芯片/净幅/S chips/ESS/机器预分类/簇拥 0.3 ×N）；
- 三类选择器（保存必选，替代原"无明显新闻"勾选语义：pure_resonance 与 sentiment_tech 都隐含 no_clear_news）；
- 对标行简化（锁定设计 8）：`标签 绝对起点 → 终点 (窗口涨跌幅)`，bp 类照旧；correlation 与前/后段展示移除；
- 跨资产图加档位色带（全段含 0.3，方向色×深浅）；
- rolling S 曲线组 + 同步参照数从行为面板迁入（放跨资产图下方，同一时间轴）。

### Task 6 — 行为面板瘦身（结论页化）
- 撤段明细/联动曲线/审核按钮；新增三类构成结论卡：14d 三类堆叠柱 + 情绪占比趋势线（分母≥5 规则沿用）+ 无对照注记。

### Task 7 — prompt v12
- 输出加 `window_class` 建议（三类）；证据描述改 rolling 峰值口径（字段名不变，语义注明"曲线峰值"）；redundant 逻辑不动；版本 bump + 守卫单测更新。

### Task 8 — 收尾
- 全量回归（pytest/vitest/build）；地图三件套 + spec 状态同步；旧 `s_score` 引用清扫；
- 同步相关整链删除：`window_signals.pearson_correlation`、`annotation_service._reference_correlations_for_window` 及相关测试（确认无残余消费者后删）。

## 风险与迁移注记

- §6 数值案例 fixture 需按 rolling 口径重算（公式锁定机制随之更新）。
- 重叠匹配阈值 50% 属经验值，上线后用最近 30 天已标窗口回放验证（应 100% 找回旧标注）。
- 三类构成的历史对比：PIT 旧行六类，读取归并即可，勿重写。
- 行为面板撤联动曲线后，`/api/behavior/linkage` 仍保留（标注页消费）。
