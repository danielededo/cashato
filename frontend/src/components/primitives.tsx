// Lightweight, dependency-free visuals (no Recharts) — cheap enough to render in
// bulk (one sparkline per KPI, a full heatmap grid). Kept in the main bundle so
// the console paints instantly; the heavy Recharts panels stay lazy.
import { memo } from "react";
import { colorFor } from "../lib/colors";
import { money } from "../lib/format";

/* Inline sparkline. Coordinates are rounded to 1dp (rendering-svg-precision). */
export const Sparkline = memo(function Sparkline({
  values,
  width = 96,
  height = 28,
}: {
  values: number[];
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return <svg width={width} height={height} className="spark" aria-hidden />;
  let min = values[0];
  let max = values[0];
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  const span = max - min || 1;
  const dx = width / (values.length - 1);
  const pts = values
    .map((v, i) => `${(i * dx).toFixed(1)},${(height - ((v - min) / span) * (height - 2) - 1).toFixed(1)}`)
    .join(" ");
  const zeroY = max <= 0 ? 1 : min >= 0 ? height - 1 : height - ((0 - min) / span) * (height - 2) - 1;
  return (
    <svg width={width} height={height} className="spark" viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <line x1="0" y1={zeroY.toFixed(1)} x2={width} y2={zeroY.toFixed(1)} stroke="var(--rule)" strokeWidth="1" />
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
});

/* Delta chip vs previous period. goodWhenUp=false flips colour semantics
   (e.g. rising expense is "bad"). unit: "%" (relative) or "pp" (points). */
export const Delta = memo(function Delta({
  current,
  previous,
  goodWhenUp = true,
  unit = "%",
}: {
  current: number;
  previous: number | null;
  goodWhenUp?: boolean;
  unit?: "%" | "pp";
}) {
  if (previous == null || !Number.isFinite(previous) || previous === 0) {
    return <span className="delta flat">— vs prev</span>;
  }
  const change = unit === "pp" ? current - previous : ((current - previous) / Math.abs(previous)) * 100;
  const rounded = Math.round(change * 10) / 10;
  if (rounded === 0) return <span className="delta flat">±0{unit} vs prev</span>;
  const up = rounded > 0;
  const good = up === goodWhenUp;
  return (
    <span className={`delta ${good ? "up" : "down"}`}>
      {up ? "▲" : "▼"} {Math.abs(rounded)}
      {unit} vs prev
    </span>
  );
});

export interface RankItem {
  /** Identity: React key, selection value, and default colour lookup. */
  category: string;
  label: string;
  value: number;
  prev?: number | null;
  /** Explicit colour, for rankings not keyed by category code (e.g. holdings
   *  keyed by ISIN, which colorFor knows nothing about and would render grey). */
  color?: string;
}

export const RankBars = memo(function RankBars({
  items,
  selected,
  onSelect,
}: {
  items: RankItem[];
  selected?: string | null;
  onSelect?: (category: string) => void;
}) {
  if (!items.length) return <div className="chart-fallback">No spending in range.</div>;
  let max = 0;
  for (const it of items) if (it.value > max) max = it.value;
  return (
    <div className="ranks">
      {items.map((it) => (
        <div
          key={it.category}
          className="rank"
          role="button"
          tabIndex={0}
          aria-selected={selected === it.category}
          onClick={() => onSelect?.(it.category)}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect?.(it.category)}
        >
          <span className="name">
            <span className="swatch" style={{ background: it.color ?? colorFor(it.category) }} />
            {it.label}
          </span>
          <span className="track">
            <span className="fill" style={{ width: `${max ? (it.value / max) * 100 : 0}%`, background: it.color ?? colorFor(it.category) }} />
          </span>
          <span className="amt">{money(it.value)}</span>
          <span style={{ textAlign: "right" }}>
            {it.prev != null ? <Delta current={it.value} previous={it.prev} goodWhenUp={false} /> : null}
          </span>
        </div>
      ))}
    </div>
  );
});

export interface HeatRow {
  category: string;
  label: string;
}

export const Heatmap = memo(function Heatmap({
  rows,
  months,
  monthLabels,
  valueOf,
  onPick,
}: {
  rows: HeatRow[];
  months: string[];
  monthLabels: string[];
  valueOf: (category: string, month: string) => number;
  onPick?: (category: string, month: string) => void;
}) {
  if (!rows.length || !months.length) return <div className="chart-fallback">Not enough history.</div>;
  let max = 0;
  for (const r of rows) for (const m of months) { const v = valueOf(r.category, m); if (v > max) max = v; }
  const cols = `minmax(96px, max-content) repeat(${months.length}, minmax(26px, 1fr))`;
  return (
    <div className="heatmap">
      <div className="heat-grid" style={{ gridTemplateColumns: cols }}>
        <div />
        {monthLabels.map((ml, i) => (
          <div key={months[i]} className="heat-colhead">{ml}</div>
        ))}
        {rows.map((r) => (
          <div key={r.category} className="heat-row">
            <div className="heat-label">
              <span className="swatch" style={{ background: colorFor(r.category) }} />
              {r.label}
            </div>
            {months.map((m) => {
              const v = valueOf(r.category, m);
              const pct = max ? Math.round((v / max) * 100) : 0;
              return (
                <div
                  key={m}
                  className="heat-cell"
                  title={`${r.label} · ${v ? money(v) : "—"}`}
                  onClick={() => onPick?.(r.category, m)}
                  style={{ background: pct ? `color-mix(in srgb, ${colorFor(r.category)} ${pct}%, var(--panel-2))` : "var(--panel-2)" }}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
});
