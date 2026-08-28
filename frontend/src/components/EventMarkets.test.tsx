import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { EventMarkets } from "./EventMarkets";

vi.mock("../api/client", () => ({
  api: {
    researchEventMarkets: vi.fn().mockResolvedValue({
      items: [{ link_id: 1, tracked_id: 2, slug: "ceasefire-2026", display_name: null,
                market: "macro", enabled: true, link_source: "auto", confidence: 0.9,
                settled: false, waiting_first_scan: true, markets: [] }]
    }),
    predictionHistory: vi.fn().mockResolvedValue([]),
    researchMarketSweep: vi.fn(),
    researchMarketSweepApply: vi.fn(),
    researchEventMarketDetach: vi.fn()
  },
  apiErrorText: () => "err"
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("EventMarkets", () => {
  it("渲染区块头、找市场按钮与首轮采集占位", async () => {
    wrap(<EventMarkets eventId={7} eventType="macro" />);
    expect(await screen.findByText("市场定价")).toBeTruthy();
    expect(screen.getByText("找市场提案")).toBeTruthy();
    expect(await screen.findByText(/等待首轮采集/)).toBeTruthy();
  });
});
