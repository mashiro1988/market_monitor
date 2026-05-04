import { useMemo } from "react";
import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function SelectControl({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: { label: string; value: string }[] }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}

export function TextInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

export function Button({ children, onClick, disabled, kind = "primary" }: { children: ReactNode; onClick?: () => void; disabled?: boolean; kind?: "primary" | "secondary" | "ghost" }) {
  return (
    <button className={`button ${kind}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" | "neutral" }) {
  return (
    <div className={`stat ${tone ?? "neutral"}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export type MultiOption = { label: string; value: string; group?: string };

export function MultiSelectControl({
  label,
  values,
  onChange,
  options,
  emptyLabel = "全部"
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  options: MultiOption[];
  emptyLabel?: string;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, MultiOption[]>();
    options.forEach((opt) => {
      const g = opt.group ?? "";
      const arr = map.get(g) ?? [];
      arr.push(opt);
      map.set(g, arr);
    });
    return Array.from(map.entries());
  }, [options]);

  const toggle = (v: string) => {
    onChange(values.includes(v) ? values.filter((x) => x !== v) : [...values, v]);
  };

  const summary = values.length === 0 ? emptyLabel : `已选 ${values.length}`;

  return (
    <details className="multi-select">
      <summary>
        <span>{label}</span>
        <strong>{summary}</strong>
      </summary>
      <div className="multi-select-popup">
        <div className="multi-select-actions">
          <button type="button" className="link-button" onClick={() => onChange(options.map((o) => o.value))}>全选</button>
          <button type="button" className="link-button" onClick={() => onChange([])}>清空</button>
        </div>
        {groups.map(([group, opts]) => (
          <div key={group || "_"}>
            {group ? <div className="multi-select-group">{group}</div> : null}
            {opts.map((opt) => (
              <label key={opt.value} className="multi-select-option">
                <input
                  type="checkbox"
                  checked={values.includes(opt.value)}
                  onChange={() => toggle(opt.value)}
                />
                <span>{opt.label}</span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </details>
  );
}
