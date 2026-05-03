import type { ReactNode } from "react";
import { EmptyState } from "./StateViews";

export type Column<T> = {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
};

export function DataTable<T>({ columns, rows, empty = "暂无数据" }: { columns: Column<T>[]; rows: T[]; empty?: string }) {
  if (!rows.length) {
    return <EmptyState title={empty} />;
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.className}>{column.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key} className={column.className}>{column.cell(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
