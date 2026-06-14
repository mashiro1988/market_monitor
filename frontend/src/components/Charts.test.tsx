import { render } from "@testing-library/react";
import { expect, test } from "vitest";
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
