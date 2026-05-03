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
