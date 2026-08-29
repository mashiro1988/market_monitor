import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { MarketPricingTab } from "./MarketPricingTab";

vi.mock("../api/client", () => ({
  api: {
    predictionTracked: vi.fn().mockResolvedValue([]),
    predictionHistory: vi.fn().mockResolvedValue([]),
    researchEvents: vi.fn().mockResolvedValue({ items: [] }),
    researchMarketSweep: vi.fn(),
    researchMarketSweepApply: vi.fn(),
    researchEventMarketAttach: vi.fn(),
    researchEventMarketDetach: vi.fn(),
    createPredictionTracked: vi.fn(),
    updatePredictionTracked: vi.fn(),
    deletePredictionTracked: vi.fn()
  },
  apiErrorText: () => "err",
  ApiError: class extends Error {}
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("MarketPricingTab", () => {
  it("精简后只剩提案按钮与跟踪管理(2026-08-29 用户反馈:常设观测/手动搜索退役)", async () => {
    wrap(<MarketPricingTab eventType="macro" />);
    expect(screen.getByText("找市场提案")).toBeTruthy();
    expect(await screen.findByText("跟踪管理")).toBeTruthy();
    expect(screen.queryByText("常设观测")).toBeNull();
    expect(screen.queryByText(/手动搜索/)).toBeNull();
    expect(screen.queryByText(/显示名/)).toBeNull();
  });
});
