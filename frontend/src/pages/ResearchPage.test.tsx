import { describe, expect, it } from "vitest";
import { closeEventPrompt, eventCardChips, fmtObs, isHotMove, OBS_HOT_PCT, fmtScore } from "./ResearchPage";
import type { ObsResult, ResearchEventItem } from "../api/types";

describe("fmtObs", () => {
  it("pending → 计算中(spec §8.1:窗口没走完不给半熟数)", () => {
    expect(fmtObs({ status: "pending" } as ObsResult).text).toBe("计算中");
  });

  it("no_data → —(基线/终点缺快照)", () => {
    expect(fmtObs({ status: "no_data" } as ObsResult).text).toBe("—");
  });

  it("ok → 实际分钟数+带符号净变动,不谎报口径", () => {
    const r = fmtObs({ status: "ok", net_pct: 0.314, actual_minutes: 12, start: 100, end: 100.31 } as ObsResult);
    expect(r.text).toBe("12min +0.31%");
    expect(r.cls).toContain("up-text");
    const d = fmtObs({ status: "ok", net_pct: -1.2, actual_minutes: 10.5, start: 100, end: 98.8 } as ObsResult);
    expect(d.text).toBe("10.5min -1.20%");
    expect(d.cls).toContain("down-text");
  });

  it("|净变动| ≥ 阈值加 hot 类,让大波动一眼可见(ui-redesign §3)", () => {
    const hot = fmtObs({ status: "ok", net_pct: -0.42, actual_minutes: 10 } as ObsResult);
    expect(hot.cls).toContain("obs-hot");
    const mild = fmtObs({ status: "ok", net_pct: 0.12, actual_minutes: 10 } as ObsResult);
    expect(mild.cls).not.toContain("obs-hot");
  });
});

describe("isHotMove", () => {
  it("阈值边界:0.29 不算,0.30 算(正负对称)", () => {
    expect(OBS_HOT_PCT).toBe(0.3);
    expect(isHotMove({ status: "ok", net_pct: 0.29 } as ObsResult)).toBe(false);
    expect(isHotMove({ status: "ok", net_pct: 0.3 } as ObsResult)).toBe(true);
    expect(isHotMove({ status: "ok", net_pct: -0.3 } as ObsResult)).toBe(true);
  });

  it("非 ok 观测一律不高亮", () => {
    expect(isHotMove({ status: "pending" } as ObsResult)).toBe(false);
    expect(isHotMove({ status: "no_data", net_pct: null } as ObsResult)).toBe(false);
  });
});

describe("fmtScore", () => {
  it("空分显示未评分而非'—分'(会被读成一分)", () => {
    expect(fmtScore(null).text).toBe("未评分");
    expect(fmtScore(null).cls).toContain("muted");
  });

  it("有分显示 N 分", () => {
    expect(fmtScore(8).text).toBe("8分");
  });
});

describe("eventCardChips", () => {
  const base: ResearchEventItem = {
    id: 1, name: "苹果调价", status: "active", gate_keywords: null, created_from: "manual",
    merged_into_id: null, closed_reason: null, evidence_count: 3, today_new: 0,
    yesterday_new: 0, badge_count: 0, days_since_last: 0, last_evidence_at: null,
    last_evidence_bj: null,
  } as ResearchEventItem;

  it("徽章固定排首位,今日/昨日恒显示(ui-redesign §2:顺序一致)", () => {
    const chips = eventCardChips({ ...base, today_new: 2, yesterday_new: 5, badge_count: 1 });
    expect(chips.map((c) => c.text)).toEqual(["徽章 1", "今日 +2", "昨日 +5"]);
  });

  it("徽章为 0 时不占位,今日/昨日仍在且 0 值走弱化样式", () => {
    const chips = eventCardChips({ ...base, today_new: 0, yesterday_new: 3 });
    expect(chips.map((c) => c.text)).toEqual(["今日 +0", "昨日 +3"]);
    expect(chips[0].cls).toContain("muted");
    expect(chips[1].cls).not.toContain("muted");
  });

  it("3 天以上无新证据才提示下沉(spec §9.1)", () => {
    expect(eventCardChips({ ...base, days_since_last: 2 }).some((c) => c.text.includes("无新证据")))
      .toBe(false);
    expect(eventCardChips({ ...base, days_since_last: 5 }).map((c) => c.text))
      .toContain("5 天无新证据");
  });
});

describe("closeEventPrompt", () => {
  it("关键词为空时提醒留沉睡词(web3 二期A design §4:宏观加密一体)", () => {
    const msg = closeEventPrompt(null);
    expect(msg).toContain("沉睡关键词");
    expect(closeEventPrompt("")).toBe(msg);
    expect(closeEventPrompt("   ")).toBe(msg);
  });

  it("已有关键词时不啰嗦", () => {
    expect(closeEventPrompt("霍尔木兹、美伊")).toBe("");
  });
});
