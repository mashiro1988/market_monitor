import { describe, expect, it } from "vitest";
import { eventRowChips, fmtObs } from "./ResearchPage";
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
});

describe("eventRowChips", () => {
  const base: ResearchEventItem = {
    id: 1, name: "苹果调价", status: "active", gate_keywords: null, created_from: "manual",
    merged_into_id: null, closed_reason: null, evidence_count: 3, today_new: 0,
    badge_count: 0, days_since_last: 0, last_evidence_at: null,
  } as ResearchEventItem;

  it("今日新增/徽章数按需出现", () => {
    const chips = eventRowChips({ ...base, today_new: 2, badge_count: 1 });
    expect(chips.map((c) => c.text)).toEqual(["今日 +2", "徽章 1"]);
  });

  it("3 天以上无新证据才提示下沉(spec §9.1)", () => {
    expect(eventRowChips({ ...base, days_since_last: 2 })).toEqual([]);
    expect(eventRowChips({ ...base, days_since_last: 5 }).map((c) => c.text))
      .toEqual(["5 天无新证据"]);
  });
});
