import { describe, expect, it } from "vitest";
import { fmtPct, fmtUsd, verdictMeta } from "./strategyFormat";

describe("strategyFormat", () => {
  it("fmtPct 带符号一位小数", () => {
    expect(fmtPct(0.214)).toBe("+21.4%");
    expect(fmtPct(-0.049)).toBe("-4.9%");
    expect(fmtPct(null)).toBe("—");
  });
  it("fmtUsd 千分位", () => {
    expect(fmtUsd(3300.4)).toBe("$3,300");
    expect(fmtUsd(null)).toBe("—");
  });
  it("verdictMeta 文案与色档", () => {
    expect(verdictMeta("hold").label).toBe("持有");
    expect(verdictMeta("breach").tone).toBe("danger");
    expect(verdictMeta("no_data").label).toContain("数据");
  });
});
