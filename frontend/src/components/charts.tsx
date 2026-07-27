// Recharts-backed panels — bundled in ONE lazy chunk (bundle-dynamic-imports) so
// the console shell, KPIs, ranked bars and heatmap paint without paying for the
// charting library. Marks read CSS custom properties so they re-theme for free.
import { memo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { colorFor, EXPENSE_COLOR, INCOME_COLOR } from "../lib/colors";
import { money, moneyShort } from "../lib/format";
import { EmptyNote } from "./primitives";

const axisTick = { fill: "var(--muted)", fontSize: 11, fontFamily: "var(--f-mono)" };
const tooltipStyle = {
  background: "var(--panel)",
  border: "1px solid var(--rule-strong)",
  borderRadius: 10,
  color: "var(--text)",
  fontSize: 12,
  boxShadow: "var(--shadow)",
};
const tooltipItem = { color: "var(--text)" };
const legendStyle = { fontSize: 12, color: "var(--text-secondary)" };

export interface SeriesDef {
  key: string;
  label: string;
  category: string;
}
export type Row = { month: string } & Record<string, number | string>;

/* Spending over time — stacked area by top categories. */
export const StackedArea = memo(function StackedArea({ data, series }: { data: Row[]; series: SeriesDef[] }) {
  if (!data.length || !series.length) return <EmptyNote k="empty.noHistory" />;
  return (
    <ResponsiveContainer width="100%" height={288}>
      <AreaChart data={data} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
        <defs>
          {series.map((s) => (
            <linearGradient key={s.key} id={`g-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colorFor(s.category)} stopOpacity={0.55} />
              <stop offset="100%" stopColor={colorFor(s.category)} stopOpacity={0.06} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} tickLine={false} stroke="var(--baseline)" />
        <YAxis tick={axisTick} tickLine={false} stroke="var(--baseline)" width={56} tickFormatter={moneyShort} />
        <Tooltip contentStyle={tooltipStyle} itemStyle={tooltipItem} formatter={(v: number) => money(v)} />
        {/* A one-entry legend only repeats the panel title. */}
        {series.length > 1 ? <Legend wrapperStyle={legendStyle} /> : null}
        {series.map((s) => (
          <Area
            key={s.key}
            type="monotone"
            name={s.label}
            dataKey={s.key}
            stackId="1"
            stroke={colorFor(s.category)}
            strokeWidth={1.5}
            fill={`url(#g-${s.key})`}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
});

/* Income vs expense per month. */
export const MonthlyBars = memo(function MonthlyBars({
  data,
}: {
  data: { month: string; Income: number; Expense: number }[];
}) {
  if (!data.length) return <EmptyNote k="empty.noMovements" />;
  return (
    <ResponsiveContainer width="100%" height={288}>
      <BarChart data={data} barGap={2}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} tickLine={false} stroke="var(--baseline)" />
        <YAxis tick={axisTick} tickLine={false} stroke="var(--baseline)" width={56} tickFormatter={moneyShort} />
        <Tooltip cursor={{ fill: "var(--panel-2)" }} contentStyle={tooltipStyle} itemStyle={tooltipItem} formatter={(v: number) => money(v)} />
        <Legend wrapperStyle={legendStyle} />
        <Bar dataKey="Income" fill={INCOME_COLOR} radius={[3, 3, 0, 0]} />
        <Bar dataKey="Expense" fill={EXPENSE_COLOR} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
});
