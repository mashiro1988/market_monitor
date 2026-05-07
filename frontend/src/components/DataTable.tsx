import { memo, type ReactNode } from "react";
import { EmptyState } from "./StateViews";

export type Column<T> = {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
};

function DataTableInner<T>({ columns, rows, empty = "暂无数据" }: { columns: Column<T>[]; rows: T[]; empty?: string }) {
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

// memo 化：父级因 hover、textarea 输入等无关 state re-render 时，只要 columns/rows/empty 三个 prop
// 引用不变，DataTable 就跳过 re-render。配合调用方用 useMemo 稳定 columns，能消除候选新闻表的抖动。
export const DataTable = memo(DataTableInner) as typeof DataTableInner;
