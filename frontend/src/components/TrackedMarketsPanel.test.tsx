import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { TrackedMarketsPanel } from "./TrackedMarketsPanel";

vi.mock("../api/client", () => ({
  api: {
    predictionTracked: vi.fn().mockResolvedValue([
      { id: 1, kind: "slug", identifier: "what-will-the-fed-rate-be-at-the-end-of-2026",
        display_name: null, enabled: true, notes: null, market: "macro",
        events: [{ link_id: 5, event_id: 3, display_no: 3, name: "美联储议息" }] }
    ]),
    researchEvents: vi.fn().mockResolvedValue({ items: [] }),
    createPredictionTracked: vi.fn(),
    updatePredictionTracked: vi.fn(),
    deletePredictionTracked: vi.fn(),
    researchEventMarketAttach: vi.fn(),
    researchEventMarketDetach: vi.fn()
  },
  apiErrorText: () => "err",
  ApiError: class extends Error {}
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("TrackedMarketsPanel", () => {
  it("默认展开,只有 slug 一个输入(2026-08-29 反馈:显示名输入退役),归属列带事件徽章", async () => {
    wrap(<TrackedMarketsPanel eventType="macro" />);
    expect(screen.getByText("跟踪管理")).toBeTruthy();
    expect(screen.getByPlaceholderText("fed-decision-in-june-825")).toBeTruthy();
    expect(screen.queryByText(/显示名（可选）/)).toBeNull();
    expect(await screen.findByText(/what-will-the-fed-rate/)).toBeTruthy();
    expect(screen.getByText(/#3 美联储议息/)).toBeTruthy();
  });
});
