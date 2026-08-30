// 持仓策略页（设计稿 §6）。数据全部来自 /api/strategy/*；本页不算任何公式。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type {
  StrategyOverview,
  StrategySettingsSchema,
  StrategySimulateResult
} from "../api/types";
import { StrategyChart } from "../components/StrategyChart";
import { EmptyState } from "../components/StateViews";
import { fmtPct, fmtPrice, fmtUsd, kindLabel, verdictMeta } from "./strategyFormat";

const SYMBOL = "VIRTUAL-USDT-SWAP";
const TONE_BG: Record<string, string> = { ok: "banner-ok", danger: "banner-danger", muted: "banner-muted" };

/** datetime-local（用户本机=北京时间）→ naive UTC ISO。 */
function localInputToUtcIso(value: string): string {
  return new Date(value).toISOString().slice(0, 19);
}

export function StrategyPage() {
  const qc = useQueryClient();
  const overviewQ = useQuery({
    queryKey: ["strategy-overview"],
    queryFn: () => api.strategyOverview(SYMBOL),
    refetchInterval: 60_000
  });
  const eventsQ = useQuery({
    queryKey: ["strategy-events"],
    queryFn: () => api.strategyEvents({ symbol: SYMBOL, limit: 30 })
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["strategy-overview"] });
    qc.invalidateQueries({ queryKey: ["strategy-events"] });
  };

  if (overviewQ.isLoading) return <EmptyState title="加载中…" />;
  if (overviewQ.isError || !overviewQ.data) return <EmptyState title="加载失败，请刷新" />;
  const ov = overviewQ.data;
  const meta = verdictMeta(ov.verdict);
  const overBudget = ov.total_occupy_usd > ov.budget_usd;
  const firstBatch = ov.batches[0];
  const lastClose = ov.chart.days.length ? ov.chart.days[ov.chart.days.length - 1].close : null;

  return (
    <div className="page strategy-page">
      {/* 决策横幅 */}
      <section className={`strategy-banner ${TONE_BG[meta.tone]}`}>
        <div>
          <strong>今日动作：{meta.label}</strong>
          <span className="banner-sub">
            {firstBatch
              ? `昨收 ${fmtPrice(lastClose)} vs 软止损 ${fmtPrice(firstBatch.soft_stop)}（余量 ${fmtPct(firstBatch.distance_pct)}）· 检查于北京 08:05`
              : "录入批次后开始跟踪"}
          </span>
          {ov.reentry ? (
            <span className="banner-sub">重入场观察中：等待收盘站回 {fmtPrice(ov.reentry.level)}</span>
          ) : null}
          {ov.data_stale ? <span className="banner-sub">⚠ 数据滞后：OKX 拉取失败，展示可能过期</span> : null}
        </div>
        <div className="banner-kpis">
          <span className={overBudget ? "kpi-danger" : ""}>
            风险占用 {fmtUsd(ov.total_occupy_usd)} / 预算 {fmtUsd(ov.budget_usd)}
            {overBudget ? " ⚠超预算" : ""}
          </span>
          <span>在用波动率 {fmtPct(ov.v_used)}（最新 {fmtPct(ov.vol_latest)}）</span>
          <span>现价 {fmtPrice(ov.live_price)}</span>
        </div>
      </section>

      <StrategyChart overview={ov} />

      <div className="strategy-grid">
        <BatchPanel ov={ov} onChanged={invalidate} />
        <section className="panel">
          <h3>动作提示流</h3>
          {(eventsQ.data ?? []).length === 0 ? (
            <p className="muted">暂无事件（每日北京 08:05 自动检查后出现）</p>
          ) : (
            <ul className="event-feed">
              {(eventsQ.data ?? []).map((e) => (
                <li key={e.id} className={`event-${e.kind}`}>
                  <span className="event-kind">{kindLabel(e.kind)}{e.pushed ? " ·已推送" : ""}</span>
                  <span className="event-msg">{e.message}</span>
                  <span className="event-time">{e.created_at.replace("T", " ").slice(5, 16)} UTC</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <CalculatorPanel defaultVol={ov.v_used} />
      </div>

      <SettingsPanel key={ov.settings.capital + "-" + ov.settings.risk_budget_pct} settings={ov.settings} onChanged={invalidate} />
    </div>
  );
}

function BatchPanel({ ov, onChanged }: { ov: StrategyOverview; onChanged: () => void }) {
  const [draft, setDraft] = useState({ batch_label: "B1", entry_at: "", entry_price: "", quantity: "", forecast: "10" });
  const [error, setError] = useState<string | null>(null);
  const createM = useMutation({
    mutationFn: (payload: Parameters<typeof api.strategyPositionCreate>[0]) => api.strategyPositionCreate(payload),
    onSuccess: onChanged,
    onError: (err) => setError(apiErrorText(err, "保存失败"))
  });
  const closeM = useMutation({
    mutationFn: (vars: { id: number; close_price: number }) =>
      api.strategyPositionUpdate(vars.id, {
        status: "closed",
        close_price: vars.close_price,
        closed_at: new Date().toISOString().slice(0, 19)
      }),
    onSuccess: onChanged
  });
  const deleteM = useMutation({ mutationFn: (id: number) => api.strategyPositionDelete(id), onSuccess: onChanged });

  return (
    <section className="panel">
      <h3>批次表</h3>
      {ov.batches.length === 0 ? <p className="muted">暂无持仓批次</p> : (
        <table className="data-table strategy-batches">
          <thead>
            <tr><th>批次</th><th>入场价</th><th>数量</th><th>预测值</th><th>软止损</th><th>占用</th><th>浮动盈亏</th><th /></tr>
          </thead>
          <tbody>
            {ov.batches.map((b) => (
              <tr key={b.id}>
                <td>{b.batch_label}</td>
                <td>{fmtPrice(b.entry_price)}</td>
                <td>{Math.round(b.quantity).toLocaleString()}</td>
                <td>{b.forecast > 0 ? `+${b.forecast}` : b.forecast}</td>
                <td>
                  {fmtPrice(b.soft_stop)}
                  {b.locked ? " 🔒锁盈" : ""}
                  {b.breached ? " ⚠破线" : ""}
                </td>
                <td>{fmtUsd(b.occupy_usd)}</td>
                <td className={b.pnl_usd >= 0 ? "pnl-pos" : "pnl-neg"}>{fmtUsd(b.pnl_usd)}</td>
                <td className="batch-actions">
                  <button
                    onClick={() => {
                      const p = window.prompt(`${b.batch_label} 平仓价格？`);
                      if (p && Number(p) > 0) closeM.mutate({ id: b.id, close_price: Number(p) });
                    }}
                  >平仓</button>
                  <button onClick={() => { if (window.confirm(`删除 ${b.batch_label} 记录？（平仓请用"平仓"，删除不留痕）`)) deleteM.mutate(b.id); }}>删</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="batch-add">
        <input placeholder="批次名 B2" value={draft.batch_label} onChange={(e) => setDraft({ ...draft, batch_label: e.target.value })} />
        <input type="datetime-local" title="入场时间（本机时区，自动转 UTC）" value={draft.entry_at} onChange={(e) => setDraft({ ...draft, entry_at: e.target.value })} />
        <input placeholder="入场价" value={draft.entry_price} onChange={(e) => setDraft({ ...draft, entry_price: e.target.value })} />
        <input placeholder="数量" value={draft.quantity} onChange={(e) => setDraft({ ...draft, quantity: e.target.value })} />
        <input placeholder="预测值" title="+5试探 +10普通看多 +15强信心 +20极度看多" value={draft.forecast} onChange={(e) => setDraft({ ...draft, forecast: e.target.value })} />
        <button
          disabled={createM.isPending}
          onClick={() => {
            setError(null);
            if (!draft.entry_at || !draft.entry_price || !draft.quantity) {
              setError("入场时间/价格/数量必填");
              return;
            }
            createM.mutate({
              symbol: SYMBOL,
              batch_label: draft.batch_label || "B?",
              entry_at: localInputToUtcIso(draft.entry_at),
              entry_price: Number(draft.entry_price),
              quantity: Number(draft.quantity),
              forecast: Number(draft.forecast) || 10
            });
          }}
        >录入批次</button>
        {error ? <span className="form-error">{error}</span> : null}
      </div>
    </section>
  );
}

function CalculatorPanel({ defaultVol }: { defaultVol: number | null }) {
  const [form, setForm] = useState({ price: "", forecast: "10", vol: "", budget_pct: "" });
  const [result, setResult] = useState<StrategySimulateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const simM = useMutation({
    mutationFn: () =>
      api.strategySimulate({
        price: Number(form.price),
        forecast: Number(form.forecast) || 10,
        vol: form.vol ? Number(form.vol) / 100 : null,
        budget_pct: form.budget_pct ? Number(form.budget_pct) / 100 : null,
        symbol: SYMBOL
      }),
    onSuccess: setResult,
    onError: (err) => setError(apiErrorText(err, "计算失败"))
  });
  return (
    <section className="panel">
      <h3>建仓计算器</h3>
      <div className="calc-form">
        <label>价格 <input value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
        <label>预测值 <input value={form.forecast} onChange={(e) => setForm({ ...form, forecast: e.target.value })} /></label>
        <label>波动率% <input placeholder={defaultVol != null ? (defaultVol * 100).toFixed(2) : "在用值"} value={form.vol} onChange={(e) => setForm({ ...form, vol: e.target.value })} /></label>
        <label>预算% <input placeholder="参数表值" value={form.budget_pct} onChange={(e) => setForm({ ...form, budget_pct: e.target.value })} /></label>
        <button disabled={!form.price || simM.isPending} onClick={() => { setError(null); simM.mutate(); }}>计算</button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {result ? (
        <ul className="calc-result">
          <li>止损价 {fmtPrice(result.stop_price)}（距离 {fmtPrice(result.stop_distance)}）</li>
          <li>应买数量 {Math.round(result.quantity).toLocaleString()} 枚</li>
          <li>名义金额 {fmtUsd(result.notional_usd)}（杠杆 {result.leverage != null ? result.leverage.toFixed(2) : "—"}×）</li>
          <li>动用预算 {fmtUsd(result.budget_usd)} · 采用波动率 {fmtPct(result.vol)}</li>
        </ul>
      ) : null}
    </section>
  );
}

function SettingsPanel({ settings, onChanged }: { settings: StrategySettingsSchema; onChanged: () => void }) {
  const [form, setForm] = useState<StrategySettingsSchema>(settings);
  const [error, setError] = useState<string | null>(null);
  const saveM = useMutation({
    mutationFn: () => api.strategySettingsUpdate(form),
    onSuccess: onChanged,
    onError: (err) => setError(apiErrorText(err, "保存失败"))
  });
  const fields: { key: keyof StrategySettingsSchema; label: string; scale?: number }[] = [
    { key: "capital", label: "本金 $" },
    { key: "risk_budget_pct", label: "风险预算 %", scale: 100 },
    { key: "x_soft", label: "软止损乘数" },
    { key: "x_hard", label: "硬防线乘数" },
    { key: "ewma_alpha", label: "EWMA α" },
    { key: "vol_update_threshold", label: "闩锁阈值 %", scale: 100 }
  ];
  return (
    <section className="panel settings-panel">
      <h3>参数（改了下次计算生效）</h3>
      <div className="calc-form">
        {fields.map((f) => (
          <label key={f.key}>
            {f.label}
            <input
              value={f.scale ? String(Number((form[f.key] * f.scale).toFixed(4))) : String(form[f.key])}
              onChange={(e) => {
                const raw = Number(e.target.value);
                if (Number.isNaN(raw)) return;
                setForm({ ...form, [f.key]: f.scale ? raw / f.scale : raw });
              }}
            />
          </label>
        ))}
        <button disabled={saveM.isPending} onClick={() => { setError(null); saveM.mutate(); }}>保存</button>
        {error ? <span className="form-error">{error}</span> : null}
      </div>
    </section>
  );
}
