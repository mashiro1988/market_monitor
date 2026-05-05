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

  const create = useMutation({
    mutationFn: () =>
      api.createPredictionTracked({
        kind: kind as "slug" | "tag",
        identifier: identifier.trim(),
        display_name: displayName.trim() || null
      }),
    onSuccess: () => {
      setIdentifier("");
      setDisplayName("");
      setErrorMsg("");
      queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMsg(err.payload.message || "添加失败");
      } else {
        setErrorMsg("添加失败");
      }
    }
  });

  const toggle = useMutation({
    mutationFn: (row: TrackedMarket) =>
      api.updatePredictionTracked(row.id, { enabled: !row.enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] })
  });

  const remove = useMutation({
    mutationFn: (row: TrackedMarket) => api.deletePredictionTracked(row.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] })
  });

  const trimmedId = identifier.trim();
  const submitDisabled = !trimmedId || create.isPending;

  return (
    <details className="panel tracked-panel">
      <summary>
        <h2>跟踪管理</h2>
        <span className="muted-text">{list.data ? `共 ${list.data.length} 条` : ""}</span>
      </summary>

      <div className="tracked-add-row">
        <SelectControl label="类型" value={kind} onChange={setKind} options={kindOptions} />
        <TextInput
          label={kind === "slug" ? "slug" : "tag"}
          value={identifier}
          onChange={setIdentifier}
          placeholder={kind === "slug" ? "fed-decision-in-june-825" : "fed"}
        />
        <TextInput
          label="显示名（可选）"
          value={displayName}
          onChange={setDisplayName}
          placeholder="2026 年 6 月 FOMC"
        />
        <Button onClick={() => create.mutate()} disabled={submitDisabled}>
          {create.isPending ? "添加中..." : "添加"}
        </Button>
      </div>
      {errorMsg ? <div className="state-view error">{errorMsg}</div> : null}

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
