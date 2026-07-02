import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    marketSymbols: vi.fn(),
    marketHistory: vi.fn()
  }
}));

import { api } from "../api/client";
import type { MarketHistoryResponse, NewsItem, PriceWindow } from "../api/types";
import { WindowNetValueChart } from "./WindowNetValueChart";

const mockedApi = api as unknown as {
  marketSymbols: ReturnType<typeof vi.fn>;
  marketHistory: ReturnType<typeof vi.fn>;
};

function pt(price: number, utc: string, bj: string) {
  return { symbol: "BTC/USDT", name: "BTC", price, normalized_pct: 0, timestamp_utc: utc, timestamp_bj: bj };
}

const history: MarketHistoryResponse = {
  symbols: ["BTC/USDT"],
  start: { timestamp_utc: null, timestamp_bj: null },
  end: { timestamp_utc: null, timestamp_bj: null },
  series: [
    {
      symbol: "BTC/USDT",
      name: "BTC",
      asset_class: "crypto",
      points: [
        pt(100, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
        pt(90, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
      ]
    }
  ]
};

const activeWindow = {
  symbol: "BTC/USDT",
  window_start: { timestamp_utc: "2026-06-14T21:32:00Z", timestamp_bj: "2026-06-15 05:32:00" },
  window_end: { timestamp_utc: "2026-06-14T21:34:00Z", timestamp_bj: "2026-06-15 05:34:00" }
} as unknown as PriceWindow;

const candidateNews = [
  { id: 1, title: "驱动新闻标题", timestamp_utc: "2026-06-14T21:34:00Z", timestamp_bj: "2026-06-15 05:34:00" },
  { id: 2, title: "噪音新闻标题", timestamp_utc: "2026-06-14T21:33:00Z", timestamp_bj: "2026-06-15 05:33:00" }
] as unknown as NewsItem[];

function renderChart(newsRoles: Record<number, string>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WindowNetValueChart
        activeWindow={activeWindow}
        preMinutes={30}
        postMinutes={30}
        candidateNews={candidateNews}
        newsRoles={newsRoles}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockedApi.marketSymbols.mockResolvedValue([]);
  mockedApi.marketHistory.mockResolvedValue(history);
});

test("勾选为 driver 的新闻出现在标记列表，noise 不出现", async () => {
  renderChart({ 1: "driver", 2: "noise" });
  expect(await screen.findByText("驱动新闻标题")).toBeInTheDocument();
  expect(screen.queryByText("噪音新闻标题")).not.toBeInTheDocument();
});

test("未勾选驱动时显示空提示", async () => {
  renderChart({});
  expect(await screen.findByText(/尚未选出驱动新闻/)).toBeInTheDocument();
});
