import { describe, expect, it } from "vitest";
import { affairLabel, coinChips } from "./CryptoNewsPage";
import type { NewsItem } from "../api/types";

const base = { id: 1, source: "blockbeats", title: "t", coins: [] } as unknown as NewsItem;

describe("coinChips", () => {
  it("币种按字母序展示,超过 6 个折叠成计数", () => {
    const chips = coinChips({ ...base, coins: ["SOL", "ARB", "BTC", "ETH", "OP", "TON", "SUI"] });
    expect(chips.shown).toEqual(["ARB", "BTC", "ETH", "OP", "SOL", "SUI"]);
    expect(chips.more).toBe(1);
  });

  it("没有币种时不占位", () => {
    const chips = coinChips({ ...base, coins: [] });
    expect(chips.shown).toEqual([]);
    expect(chips.more).toBe(0);
  });

  it("字段缺失(宏观新闻混入)也不炸", () => {
    expect(coinChips({ ...base, coins: undefined as unknown as string[] }).shown).toEqual([]);
  });
});

describe("affairLabel", () => {
  it("非币圈事务明示'转载宏观',让不入池这件事有解释", () => {
    expect(affairLabel(false)).toBe("转载宏观");
  });

  it("币圈事务与未判定都不加标签,避免满屏噪音", () => {
    expect(affairLabel(true)).toBe("");
    expect(affairLabel(null)).toBe("");
    expect(affairLabel(undefined)).toBe("");
  });
});
