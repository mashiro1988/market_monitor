import React from "react";
import { render } from "@testing-library/react";
import { expect, test, vi } from "vitest";

// 给 LineChart 注入显式尺寸，否则 jsdom 下 recharts 不渲染子元素
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: any) =>
      React.cloneElement(children, { width: 800, height: 400 }),
  };
});

import { MultiLineChart } from "./Charts";

test("无数据时显示 EmptyState", () => {
  const { getByText } = render(<MultiLineChart data={[]} keys={[]} />);
  expect(getByText("当前区间没有足够数据")).toBeInTheDocument();
});

test("带 markers/highlight/baseline/valueFormatter 渲染不崩溃", () => {
  const { container } = render(
    <MultiLineChart
      data={[
        { time: "06-15 05:30", a: 1 },
        { time: "06-15 05:35", a: 0.9 }
      ]}
      keys={["a"]}
      unit=""
      baseline={1}
      valueFormatter={(v) => v.toFixed(3)}
      markers={[{ time: "06-15 05:35", role: "driver" }]}
      highlightKey="a"
    />
  );
  expect(container.querySelector(".chart-shell")).not.toBeNull();
});

const shadedBandsData = [
  { time: "06-27 04:00", "纳指 (NQ=F)": 0 },
  { time: "06-27 05:00", "纳指 (NQ=F)": 0.5 },
  { time: "06-27 06:00", "纳指 (NQ=F)": 0.8 },
];

test("不传 shadedBands 时无 ReferenceArea（默认行为不变）", () => {
  const { container } = render(
    <MultiLineChart data={shadedBandsData} keys={["纳指 (NQ=F)"]} />
  );
  expect(container.querySelectorAll(".recharts-reference-area").length).toBe(0);
});

test("传入 shadedBands 时渲染 ReferenceArea", () => {
  const { container } = render(
    <MultiLineChart
      data={shadedBandsData}
      keys={["纳指 (NQ=F)"]}
      shadedBands={[{ x1: "06-27 05:00", x2: "06-27 06:00", label: "休市代理价" }]}
    />
  );
  expect(container.querySelectorAll(".recharts-reference-area").length).toBeGreaterThan(0);
});
