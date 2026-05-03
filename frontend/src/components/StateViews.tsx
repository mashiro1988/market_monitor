import type { ReactNode } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { ApiError } from "../api/client";

export function LoadingState({ label = "加载中" }: { label?: string }) {
  return (
    <div className="state-view">
      <Loader2 className="spin" size={18} />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ title = "暂无数据", children }: { title?: string; children?: ReactNode }) {
  return (
    <div className="state-view muted">
      <span>{title}</span>
      {children}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof ApiError ? `${error.payload.code}: ${error.payload.message}` : error instanceof Error ? error.message : "未知错误";
  return (
    <div className="state-view error">
      <AlertTriangle size={18} />
      <span>{message}</span>
    </div>
  );
}
