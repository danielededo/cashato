import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { colorFor, EXPENSE_COLOR, INCOME_COLOR, topNWithOther } from "../lib/colors";
import { money, monthLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const axisTick = { fill: "var(--muted)", fontSize: 12 };
const tooltipStyle = {
  background: "var(--surface-1)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--text-primary)",
};

export function Dashboard() {
  const summary = useAsync(() => api.summary("it"), []);
  const monthly = useAsync(() => api.monthly(), []);
  const catMonthly = useAsync(() => api.categoriesMonthly("it"), []);

  return (
    <>
      <h1>Dashboard</h1>

      {/* KPI tiles from the category totals */}
      {summary.data && (
        <div className="grid cols-3" style={{ marginBottom: 16 }}>
          {(() => {
            const income = summary.data.categories.reduce((s, c) => s + (c.income ?? 0), 0);
            const expense = summary.data.categories.reduce((s, c) => s + (c.expense ?? 0), 0);
            const net = income + expense;
            return (
              <>
                <Tile label="Income" value={money(income)} cls="pos" />
                <Tile label="Expense" value={money(expense)} cls="neg" />
                <Tile label="Net" value={money(net)} cls={net >= 0 ? "pos" : "neg"} />
              </>
            );
          })()}
        </div>
      )}

      <div className="grid cols-2">
        <div className="card">
          <h2>Spending by category</h2>
          <Chart state={summary}>
            {summary.data && <CategoryDonut data={summary.data.categories} />}
          </Chart>
        </div>

        <div className="card">
          <h2>Monthly income vs expense</h2>
          <Chart state={monthly}>
            {monthly.data && <MonthlyBars data={monthly.data.months} />}
          </Chart>
        </div>
      </div>

      <div className="card">
        <h2>Category trends (monthly)</h2>
        <Chart state={catMonthly}>
          {catMonthly.data && <CategoryTrends rows={catMonthly.data.rows} />}
        </Chart>
      </div>
    </>
  );
}

function Tile({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={`value ${cls ?? ""}`}>{value}</div>
    </div>
  );
}

// Generic loading/error wrapper for a chart card.
function Chart<T>({ state, children }: { state: { loading: boolean; error: string | null; data: T | null }; children: React.ReactNode }) {
  if (state.loading) return <div className="state">Loading…</div>;
  if (state.error) return <div className="state error">{state.error}</div>;
  return <>{children}</>;
}

function CategoryDonut({ data }: { data: { category: string; category_label: string; expense: number | null }[] }) {
  const slices = topNWithOther(
    data.filter((c) => (c.expense ?? 0) < 0),
    (c) => c.expense ?? 0,
    7,
  );
  if (!slices.length) return <div className="state">No spending yet.</div>;
  return (
    <>
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie data={slices} dataKey="value" nameKey="label" innerRadius={60} outerRadius={95} paddingAngle={2} stroke="var(--surface-1)" strokeWidth={2}>
            {slices.map((s) => (
              <Cell key={s.category} fill={colorFor(s.category)} />
            ))}
          </Pie>
          <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => money(v)} />
        </PieChart>
      </ResponsiveContainer>
      {/* legend = relief for the light-mode low-contrast slots */}
      <div className="legend">
        {slices.map((s) => (
          <span key={s.category}>
            <span className="swatch" style={{ background: colorFor(s.category) }} />
            {s.label} · {money(s.value)}
          </span>
        ))}
      </div>
    </>
  );
}

function MonthlyBars({ data }: { data: { month: string; income: number | null; expense: number | null }[] }) {
  const rows = data.map((m) => ({
    month: monthLabel(m.month),
    Income: m.income ?? 0,
    Expense: Math.abs(m.expense ?? 0),
  }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={rows} barGap={2}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} stroke="var(--baseline)" />
        <YAxis tick={axisTick} stroke="var(--baseline)" width={72} tickFormatter={(v) => money(v)} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => money(v)} />
        <Legend />
        <Bar dataKey="Income" fill={INCOME_COLOR} radius={[4, 4, 0, 0]} />
        <Bar dataKey="Expense" fill={EXPENSE_COLOR} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function CategoryTrends({ rows }: { rows: { month: string; category: string; category_label: string; total: number | null }[] }) {
  // top 5 categories by total |spend|
  const byCat = new Map<string, { label: string; total: number }>();
  for (const r of rows) {
    const e = byCat.get(r.category) ?? { label: r.category_label, total: 0 };
    e.total += Math.abs(r.total ?? 0);
    byCat.set(r.category, e);
  }
  const top = [...byCat.entries()].sort((a, b) => b[1].total - a[1].total).slice(0, 5).map(([c]) => c);

  // pivot to { month, [label]: value } (values as absolute magnitude)
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const labelOf = new Map(top.map((c) => [c, byCat.get(c)!.label]));
  const pivot = months.map((m) => {
    const o: Record<string, number | string> = { month: monthLabel(m) };
    for (const c of top) o[labelOf.get(c)!] = 0;
    for (const r of rows) if (r.month === m && top.includes(r.category)) o[labelOf.get(r.category)!] = Math.abs(r.total ?? 0);
    return o;
  });

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={pivot}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} stroke="var(--baseline)" />
        <YAxis tick={axisTick} stroke="var(--baseline)" width={72} tickFormatter={(v) => money(v)} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => money(v)} />
        <Legend />
        {top.map((c) => (
          <Line key={c} type="monotone" dataKey={labelOf.get(c)!} stroke={colorFor(c)} strokeWidth={2} dot={{ r: 3 }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
