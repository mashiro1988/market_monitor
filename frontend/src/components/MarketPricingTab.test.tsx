import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { MarketPricingTab } from "./MarketPricingTab";

vi.mock("../api/client", () => ({
  api: {
    predictions: vi.fn().mockResolvedValue({ markets: [], latest_timestamp: null }),
    predictionFamilies: vi.fn().mockResolvedValue([]),
    predictionTracked: vi.fn().mockResolvedValue([]),
    predictionSearch: vi.fn().mockResolvedValue([]),
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
  it("渲染提案按钮、常设观测与空态", async () => {
    wrap(<MarketPricingTab eventType="macro" />);
    expect(screen.getByText("找市场提案")).toBeTruthy();
    expect(screen.getByText("常设观测")).toBeTruthy();
    expect(await screen.findByText("本线暂无常设市场")).toBeTruthy();
  });
});
