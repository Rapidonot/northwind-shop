// OPS-1: small pieces the staff views share. Text only: nothing here renders HTML from data.
import type { ReactNode } from "react";
import type { Outcome } from "./types";

export function SampleBadge() {
  return <span className="tag">Sample data</span>;
}

export function OutcomePill({ outcome }: { outcome: Outcome }) {
  return <span className={`pill pill-${outcome}`}>{outcome}</span>;
}

export function StatusPill({ status, escalated }: { status: string; escalated: boolean }) {
  return <span className={escalated ? "pill pill-escalated" : "pill"}>{status}</span>;
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { value: T; label: string; count?: number }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.label}
          {option.count !== undefined && (
            <>
              {" "}
              <span className="chip-count">{option.count.toLocaleString("en-US")}</span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}

export function Picker({ label, options, value, onChange }: { label: string; options: readonly string[]; value: string; onChange: (value: string) => void }) {
  return (
    <select className="picker" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{label}</option>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

export function SearchField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <input className="filter-search" type="search" aria-label={label} placeholder={label} value={value} maxLength={64} onChange={(e) => onChange(e.target.value)} />;
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function Stat({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <span className="stat">
      {label}: <b className={strong ? "stat-strong" : undefined}>{value}</b>
    </span>
  );
}

export function ViewHead({ title, note, children }: { title: string; note: string; children?: ReactNode }) {
  return (
    <header className="view-head">
      <div>
        <h1>{title}</h1>
        <p className="muted">{note}</p>
      </div>
      {children}
    </header>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** Long tables show a page at a time; "Show more" adds the next page. */
export const PAGE = 50;

export function More({ shown, total, onMore, noun }: { shown: number; total: number; onMore: () => void; noun: string }) {
  return (
    <div className="table-foot">
      <span className="muted">
        Showing {shown.toLocaleString("en-US")} of {total.toLocaleString("en-US")} {noun}
      </span>
      {shown < total && (
        <button type="button" className="btn btn-outline" onClick={onMore}>
          Show more
        </button>
      )}
    </div>
  );
}
