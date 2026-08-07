import { describe, expect, it } from "vitest";
import type { SectorFlowSide } from "../api/types";
import {
  FLOW_SORTS,
  flowSortValue,
  flowStrength,
  fmtMoney,
  fmtStrength,
} from "./sectorFlowFormat";

const side = (over: Partial<SectorFlowSide> = {}): SectorFlowSide => ({
  tokens: 10,
  net_1h: null, net_24h: null, net_168h: null, net_720h: null,
  qv_1h: null, qv_24h: null, qv_168h: null, qv_720h: null,
  ...over,
});

describe("fmtMoney", () => {
  it("缩写到 K/M/B 并带正负号", () => {
    expect(fmtMoney(980_000)).toBe("+$980.0K");
    expect(fmtMoney(46_200_000)).toBe("+$46.2M");
    expect(fmtMoney(-1_240_000_000)).toBe("-$1.2B");
  });

  it("小额不缩写", () => {
    expect(fmtMoney(842)).toBe("+$842");
    expect(fmtMoney(-30)).toBe("-$30");
  });

  it("零与缺失都显示破折号", () => {
    expect(fmtMoney(0)).toBe("—");
    expect(fmtMoney(null)).toBe("—");
    expect(fmtMoney(undefined)).toBe("—");
  });
});

describe("flowStrength", () => {
  it("净流入除以总成交额得百分比", () => {
    expect(flowStrength(500, 5000)).toBeCloseTo(10, 6);
    expect(flowStrength(-1200, 4000)).toBeCloseTo(-30, 6);
  });

  it("成交额为零或缺失时无强度可言", () => {
    expect(flowStrength(500, 0)).toBeNull();
    expect(flowStrength(500, null)).toBeNull();
    expect(flowStrength(null, 5000)).toBeNull();
  });
});

describe("fmtStrength", () => {
  it("取整并带符号", () => {
    expect(fmtStrength(12.4)).toBe("+12%");
    expect(fmtStrength(-30)).toBe("-30%");
    expect(fmtStrength(0)).toBe("0%");
    expect(fmtStrength(null)).toBe("");
  });
});

describe("flowSortValue", () => {
  it("按排序键取到对应市场与窗口的净流入", () => {
    const row = {
      flows: { spot: side({ net_24h: 500 }), swap: side({ net_24h: -900 }) },
    } as any;
    expect(flowSortValue(row, "flow_spot_24h")).toBe(500);
    expect(flowSortValue(row, "flow_swap_24h")).toBe(-900);
    expect(flowSortValue(row, "flow_spot_168h")).toBeNull();
  });

  it("整侧缺失或 flows 为空时返回 null（排序会沉底）", () => {
    expect(flowSortValue({ flows: { spot: null, swap: null } } as any, "flow_spot_24h")).toBeNull();
    expect(flowSortValue({ flows: null } as any, "flow_spot_24h")).toBeNull();
  });

  it("四个排序键都定义齐全", () => {
    expect(Object.keys(FLOW_SORTS).sort()).toEqual([
      "flow_spot_168h", "flow_spot_24h", "flow_swap_168h", "flow_swap_24h",
    ]);
  });
});
