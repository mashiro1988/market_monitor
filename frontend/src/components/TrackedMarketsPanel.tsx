import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { TrackedMarket } from "../api/types";
import { Button, SelectControl, TextInput } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";

const kindOptions = [
  { label: "Slug (单个 market 或 event)", value: "slug" },
  { label: "Tag (家族 / 自动发现)", value: "tag" }
];

function extractSlug(input: string): string {
  const trimmed = input.trim();
  const urlMatch = trimmed.match(/polymarket\.com\/(?:event|market)\/([\w-]+)/i);
  if (urlMatch) return urlMatch[1];
  return trimmed;
}

function looksLikeQuestion(input: string): boolean {
  return /\s/.test(input) || input.includes("?") || input.includes("？");
}

export function TrackedMarketsPanel() {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["prediction-tracked"],
    queryFn: () => api.predictionTracked()
  });

  const [kind, setKind] = useState("slug");
  const [identifier, setIdentifier] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  const create = useMutation({
    mutationFn: (resolvedId: string) =>
      api.createPredictionTracked({
        kind: kind as "slug" | "tag",
        identifier: resolvedId,
        display_name: displayName.trim() || null
      }),
    onSuccess: (row) => {
      setSuccessMsg(`已添加 ${row.kind}: ${row.identifier}`);
      setErrorMsg("");
      setIdentifier("");
      setDisplayName("");
      queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] });
    },
    onError: (err) => {
      setSuccessMsg("");
      if (err instanceof ApiError) {
        setErrorMsg(err.payload.message || "添加失败");
      } else {
        setErrorMsg("添加失败");
      }
    }
  });

  const submit = () => {
    const resolved = kind === "slug" ? extractSlug(identifier) : identifier.trim();
    if (!resolved) {
      setErrorMsg("请输入 slug 或 tag");
      return;
    }
    if (kind === "slug" && looksLikeQuestion(resolved)) {
      setErrorMsg("看起来是市场标题，不是 slug。请到该市场的 Polymarket 页面，复制 URL 末尾那一段（如 fed-decision-in-june-825）。");
      return;
    }
    create.mutate(resolved);
  };

  const toggle = useMutation({
    mutationFn: (row: TrackedMarket) =>
      api.updatePredictionTracked(row.id, { enabled: !row.enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] })
  });

  const remove = useMutation({
    mutationFn: (row: TrackedMarket) => api.deletePredictionTracked(row.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] })
  });

  return (
    <details className="panel tracked-panel">
      <summary>
        <h2>跟踪管理</h2>
        <span className="muted-text">{list.data ? `共 ${list.data.length} 条` : ""}</span>
      </summary>

      <div className="tracked-add-row">
        <SelectControl label="类型" value={kind} onChange={setKind} options={kindOptions} />
        <TextInput
          label={kind === "slug" ? "slug 或 Polymarket URL" : "tag"}
          value={identifier}
          onChange={(v) => {
            setIdentifier(v);
            setErrorMsg("");
            setSuccessMsg("");
          }}
          placeholder={kind === "slug" ? "fed-decision-in-june-825" : "fed"}
        />
        <TextInput
          label="显示名（可选）"
          value={displayName}
          onChange={setDisplayName}
          placeholder="2026 年 6 月 FOMC"
        />
        <Button onClick={submit} disabled={create.isPending}>
          {create.isPending ? "添加中..." : "添加"}
        </Button>
      </div>
      {kind === "slug" ? (
        <div className="muted-text small">
          slug 来自 Polymarket 市场页 URL 末尾，例如 <code>polymarket.com/event/<strong>fed-decision-in-june-825</strong></code>。可以直接粘贴整个 URL，会自动提取。
        </div>
      ) : null}
      {errorMsg ? <div className="state-view error">{errorMsg}</div> : null}
      {successMsg ? <div className="state-view success-text">{successMsg}</div> : null}

      {list.isLoading ? (
        <LoadingState />
      ) : list.error ? (
        <ErrorState error={list.error} />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>类型</th>
                <th>Identifier</th>
                <th>显示名</th>
                <th>启用</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((row) => (
                <tr key={row.id}>
                  <td>{row.kind}</td>
                  <td><code>{row.identifier}</code></td>
                  <td>{row.display_name || "—"}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={row.enabled}
                      disabled={toggle.isPending}
                      onChange={() => toggle.mutate(row)}
                    />
                  </td>
                  <td>
                    <button
                      className="link-button danger"
                      disabled={remove.isPending}
                      onClick={() => {
                        if (window.confirm(`删除 ${row.identifier}?`)) remove.mutate(row);
                      }}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!(list.data ?? []).length ? (
                <tr><td colSpan={5} className="muted-text">尚未跟踪任何 slug/tag</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
