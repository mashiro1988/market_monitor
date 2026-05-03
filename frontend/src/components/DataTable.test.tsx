import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { DataTable } from "./DataTable";

type Row = {
  name: string;
  value: number;
};

test("renders empty state", () => {
  render(<DataTable<Row> rows={[]} empty="没有记录" columns={[{ key: "name", header: "名称", cell: (row) => row.name }]} />);
  expect(screen.getByText("没有记录")).toBeInTheDocument();
});

test("renders rows and columns", () => {
  render(
    <DataTable<Row>
      rows={[{ name: "BTC", value: 1 }]}
      columns={[
        { key: "name", header: "名称", cell: (row) => row.name },
        { key: "value", header: "数值", cell: (row) => row.value }
      ]}
    />
  );
  expect(screen.getByText("名称")).toBeInTheDocument();
  expect(screen.getByText("BTC")).toBeInTheDocument();
});
