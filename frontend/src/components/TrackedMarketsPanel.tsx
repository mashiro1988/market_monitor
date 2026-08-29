import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { TrackedMarket } from "../api/types";
import { Button, TextInput } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";

function extractSlug(input: string): string {
  const trimmed = input.trim();
  const urlMatch = trimmed.match(/polymarket\.com\/(?:event|market)\/([\w-]+)/i);
  if (urlMatch) return urlMatch[1];
  return trimmed;
}

function looksLikeQuestion(input: string): boolean {
  return /\s/.test(input) || input.includes("?") || input.includes("？");
}

/** 跟踪管理(2026-08-28 迁入池页市场定价页签):按线过滤,加「归属事件」列——
 *  挂接/摘下直接在表里操作,人工通道 link_source=human。 */
export function TrackedMarketsPanel({ eventType }: { eventType: "macro" | "crypto" }) {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["prediction-tracked", eventType],
    queryFn: () => api.predictionTracked(eventType)
  });
  const activeEvents = useQuery({
    queryKey: ["research-events", "active", eventType],
    queryFn: () => api.researchEvents({ status: "active", event_type: eventType })
  });

  const [identifier, setIdentifier] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] });
    void queryClient.invalidateQueries({ queryKey: ["event-markets"] });
    void queryClient.invalidateQueries({ queryKey: ["research-events"] });
  };

  const create = useMutation({
    mutationFn: (resolvedId: string) =>
      api.createPredictionTracked({
        kind: "slug",
        identifier: resolvedId,
        market: eventType
      }),
    onSuccess: (row) => {
      setSuccessMsg(`已添加 ${row.kind}: ${row.identifier}`);
      setErrorMsg("");
      setIdentifier("");
      invalidate();
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
    const resolved = extractSlug(identifier);
    if (!resolved) {
      setErrorMsg("请输入 slug 或 Polymarket URL");
      return;
    }
    if (looksLikeQuestion(resolved)) {
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
    onSuccess: invalidate
  });

  const attach = useMutation({
    mutationFn: ({ eventId, trackedId }: { eventId: number; trackedId: number }) =>
      api.researchEventMarketAttach(eventId, trackedId),
    onSuccess: () => { setErrorMsg(""); invalidate(); },
    onError: () => setErrorMsg("挂接失败")
  });

  const detach = useMutation({
    mutationFn: (linkId: number) => api.researchEventMarketDetach(linkId),
    onSuccess: () => { setErrorMsg(""); invalidate(); },
    onError: () => setErrorMsg("摘下失败")
  });

  const eventOptions = activeEvents.data?.items ?? [];

  return (
    <details className="panel tracked-panel">
      <summary>
        <h2>跟踪管理</h2>
        <span className="muted-text">{list.data ? `共 ${list.data.length} 条` : ""}</span>
      </summary>

      <div className="tracked-add-row">
        <TextInput
          label="slug 或 Polymarket URL"
          value={identifier}
          onChange={(v) => {
            setIdentifier(v);
            setErrorMsg("");
            setSuccessMsg("");
          }}
          placeholder="fed-decision-in-june-825"
        />
        <Button onClick={submit} disabled={create.isPending}>
          {create.isPending ? "添加中..." : "添加"}
        </Button>
      </div>
      <div className="muted-text small">
        slug 来自 Polymarket 市场页 URL 末尾，例如 <code>polymarket.com/event/<strong>fed-decision-in-june-825</strong></code>。可以直接粘贴整个 URL，会自动提取。
      </div>
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
                <th>Identifier</th>
                <th>显示名</th>
                <th>归属事件</th>
                <th>启用</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((row) => (
                <tr key={row.id}>
                  <td><code>{row.identifier}</code></td>
                  <td>{row.display_name || "—"}</td>
                  <td>
                    <span style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
                      {(row.events ?? []).map((e) => (
                        <span key={e.link_id} className="s-badge mid" title={e.name}>
                          #{e.display_no} {e.name}
                          <button type="button" className="link-button" title="摘下(留痕)"
                                  disabled={detach.isPending}
                                  onClick={() => detach.mutate(e.link_id)}>×</button>
                        </span>
                      ))}
                      <select value="" title="挂接到事件"
                              disabled={attach.isPending || !eventOptions.length}
                              onChange={(ev) => {
                                const eid = Number(ev.target.value);
                                if (eid) attach.mutate({ eventId: eid, trackedId: row.id });
                              }}>
                        <option value="">挂接→</option>
                        {eventOptions
                          .filter((e) => !(row.events ?? []).some((l) => l.event_id === e.id))
                          .map((e) => (
                            <option key={e.id} value={e.id}>#{e.display_no} {e.name}</option>
                          ))}
                      </select>
                    </span>
                  </td>
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
                <tr><td colSpan={5} className="muted-text">尚未跟踪任何 slug</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
