import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { sectorLeaderboard: vi.fn(), sectorTokens: vi.fn() },
}));

import { api } from "../api/client";
import { SectorRotationPage } from "./SectorRotationPage";

const EXCLUSION_NOTE = "已剔除 BTC/ETH/WBTC/WBETH";

function leaderboard() {
  return {
    snapshot_at: { timestamp_bj: "2026-08-16 11:30:00", timestamp_utc: "2026-08-16 03:30:00" },
    exclusion_note: EXCLUSION_NOTE,
    rows: [{
      category: "Memes", group: "叙事", token_count: 132,
      ret_1h: 0.1, ret_24h: 0.15, ret_168h: 1.0, ret_720h: 2.0,
      ret_1h_median: 0.05, ret_24h_median: 0.08, ret_168h_median: 0.9, ret_720h_median: 1.8,
      flows: { spot: null, swap: null },
    }],
  };
}

function tokens() {
  return {
    category: "Memes", group: "叙事", snapshot_at: null,
    tokens: [
      { symbol: "PEPE", binance_symbol: "PEPEUSDT", market: "spot", excluded: false,
        ret_1h: 1, ret_24h: 3, ret_168h: 1, ret_720h: 1, flows: null },
      { symbol: "BTC", binance_symbol: "BTCUSDT", market: "spot", excluded: true,
        ret_1h: 1, ret_24h: 10, ret_168h: 1, ret_720h: 1, flows: null },
    ],
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SectorRotationPage />
    </QueryClientProvider>,
  );
}

describe("SectorRotationPage 巨头剔除标注", () => {
  beforeEach(() => {
    vi.mocked(api.sectorLeaderboard).mockResolvedValue(leaderboard() as any);
    vi.mocked(api.sectorTokens).mockResolvedValue(tokens() as any);
  });

  it("副标题常驻显示剔除说明 —— 一进页面就知道数字不含巨头", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/已剔除 BTC\/ETH\/WBTC\/WBETH/)).toBeInTheDocument());
  });

  it("展开板块后，被剔除的成分币行标「不计入」并整行灰显", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Memes")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Memes"));

    await waitFor(() => expect(screen.getByText("BTC")).toBeInTheDocument());
    expect(screen.getByText("不计入")).toBeInTheDocument();

    const btcRow = screen.getByText("BTC").closest("tr");
    expect(btcRow?.className).toContain("sector-token-excluded");
    // 普通成分币不该被标
    const pepeRow = screen.getByText("PEPE").closest("tr");
    expect(pepeRow?.className ?? "").not.toContain("sector-token-excluded");
  });
});
