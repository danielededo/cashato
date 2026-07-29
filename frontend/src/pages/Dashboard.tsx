import { lazy, Suspense, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Row, SeriesDef } from "../components/charts";
import { Delta, Heatmap, RankBars, Sparkline, type HeatRow, type RankItem } from "../components/primitives";
import { colorFor } from "../lib/colors";
import { dateLabel, isoDate, money, num } from "../lib/format";
import { monthShort } from "../lib/format";
import { useT } from "../lib/i18n";
import { PrivacyToggle } from "../lib/privacy";
import { useLang } from "../lib/lang";
import { monthsFor, PERIODS, splitWindows, type PeriodKey } from "../lib/period";
import { useAsync } from "../lib/useAsync";

const StackedArea = lazy(() => import("../components/charts").then((m) => ({ default: m.StackedArea })));
const MonthlyBars = lazy(() => import("../components/charts").then((m) => ({ default: m.MonthlyBars })));

const TOP_RANK = 8;
const TOP_STACK = 6;

function endOfMonth(iso: string): string {
  const d = new Date(iso);
  // isoDate, not toISOString: the latter shifts to UTC and would return the
  // 29th for a June month-end in Europe/Rome, dropping a day from the drill-down.
  return isoDate(new Date(d.getFullYear(), d.getMonth() + 1, 0));
}

export function Dashboard() {
  const [period, setPeriod] = useState<PeriodKey>("12m");
  const [compare, setCompare] = useState(true);
  const { lang } = useLang();
  const { t } = useT();
  const monthly = useAsync(() => api.monthly(), []);
  const catMonthly = useAsync(() => api.categoriesMonthly(lang), [lang]);
  // Decorative: a missing/empty profile just falls back to the impersonal
  // heading, so swallow the failure rather than surfacing a page-level error.
  const profile = useAsync(() => api.profile().catch(() => null), []);
  // Detected server-side from the data's rhythm; a failure only hides the
  // panel, the rest of the dashboard owes it nothing.
  const rec = useAsync(() => api.recurring(lang).catch(() => null), [lang]);
  const navigate = useNavigate();

  const months = monthly.data?.months;
  const catRows = catMonthly.data?.rows;

  // Merchant ranking is aggregated server-side (case-insensitive grouping,
  // refunds netted), so it cannot be derived from the monthly rows above:
  // fetch both windows and let the compare toggle decide what to show.
  const windows = useMemo(() => {
    if (!months) return null;
    const { current, previous } = splitWindows(months.map((m) => m.month), monthsFor(period));
    if (!current.length) return null;
    return { current, previous };
  }, [months, period]);
  const merch = useAsync(async () => {
    if (!windows) return null;
    const { current, previous } = windows;
    const cur = api.merchants({ lang, date_from: current[0], date_to: endOfMonth(current[current.length - 1]), limit: TOP_RANK });
    const prev = previous.length
      ? api.merchants({ lang, date_from: previous[0], date_to: endOfMonth(previous[previous.length - 1]), limit: 100 })
      : Promise.resolve(null);
    return Promise.all([cur, prev]).catch(() => null);
  }, [windows, lang]);
  const merchItems = useMemo<RankItem[] | null>(() => {
    const [cur, prev] = merch.data ?? [null, null];
    if (!cur?.merchants.length) return null;
    const prevBy = new Map((prev?.merchants ?? []).map((m) => [m.merchant.toLowerCase(), num(m.total_spent)]));
    return cur.merchants.map((m) => ({
      category: m.merchant,
      label: m.merchant,
      value: num(m.total_spent),
      prev: compare ? (prevBy.get(m.merchant.toLowerCase()) ?? null) : null,
      // A merchant is not a category code, so colorFor would render it grey;
      // its dominant category ties it visually to the panels above.
      color: colorFor(m.category ?? ""),
    }));
  }, [merch.data, compare]);

  const d = useMemo(() => {
    if (!months || !catRows) return null;
    const allMonths = months.map((m) => m.month);
    const { current, previous } = splitWindows(allMonths, monthsFor(period));
    const cur = new Set(current);
    const prev = new Set(previous);
    const byMonth = new Map(months.map((m) => [m.month, m]));

    const stats = (win: string[]) => {
      let income = 0, expense = 0, net = 0;
      const sInc: number[] = [], sExp: number[] = [], sNet: number[] = [];
      for (const m of win) {
        const r = byMonth.get(m);
        const i = num(r?.income), e = num(r?.expense), n = r?.net != null ? num(r.net) : i + e;
        income += i; expense += e; net += n;
        sInc.push(i); sExp.push(Math.abs(e)); sNet.push(n);
      }
      return { income, expense, net, sInc, sExp, sNet, n: win.length };
    };
    const c = stats(current);
    const p = stats(previous);

    // category spend (magnitude of negative totals) per window + per current month
    const curCat = new Map<string, { label: string; spend: number }>();
    const prevCat = new Map<string, { spend: number }>();
    const monthCat = new Map<string, Map<string, number>>();
    const movByMonth = new Map<string, number>();
    let movCur = 0, movPrev = 0;
    for (const r of catRows) {
      const inCur = cur.has(r.month), inPrev = prev.has(r.month);
      if (inCur) { movCur += r.n_movements; movByMonth.set(r.month, (movByMonth.get(r.month) ?? 0) + r.n_movements); }
      if (inPrev) movPrev += r.n_movements;
      const t = num(r.total);
      if (t >= 0) continue;
      const mag = -t;
      if (inCur) {
        const e = curCat.get(r.category) ?? { label: r.category_label, spend: 0 };
        e.spend += mag; curCat.set(r.category, e);
        let mm = monthCat.get(r.month);
        if (!mm) { mm = new Map(); monthCat.set(r.month, mm); }
        mm.set(r.category, (mm.get(r.category) ?? 0) + mag);
      }
      if (inPrev) { const e = prevCat.get(r.category) ?? { spend: 0 }; e.spend += mag; prevCat.set(r.category, e); }
    }

    const ranked = [...curCat.entries()].sort((a, b) => b[1].spend - a[1].spend);
    const rankItems: RankItem[] = ranked.slice(0, TOP_RANK).map(([category, v]) => ({
      category, label: v.label, value: v.spend, prev: prevCat.get(category)?.spend ?? null,
    }));
    const stackSeries: SeriesDef[] = ranked.slice(0, TOP_STACK).map(([category, v]) => ({ key: category, label: v.label, category }));
    const stackData: Row[] = current.map((m) => {
      const row: Row = { month: monthShort(m) };
      for (const s of stackSeries) row[s.key] = monthCat.get(m)?.get(s.category) ?? 0;
      return row;
    });
    const heatRows: HeatRow[] = ranked.slice(0, TOP_RANK).map(([category, v]) => ({ category, label: v.label }));
    const barsData = current.map((m) => {
      const r = byMonth.get(m);
      return { month: monthShort(m), Income: Math.round(num(r?.income)), Expense: Math.round(Math.abs(num(r?.expense))) };
    });

    const expAbs = Math.abs(c.expense);
    const savings = c.income > 0 ? (c.net / c.income) * 100 : 0;
    const savingsPrev = p.income > 0 ? (p.net / p.income) * 100 : 0;
    const avg = c.n ? expAbs / c.n : 0;
    const avgPrev = p.n ? Math.abs(p.expense) / p.n : 0;

    return {
      current, previous, cur, hasPrev: previous.length > 0,
      c, p, expAbs, savings, savingsPrev, avg, avgPrev, movCur, movPrev,
      movSpark: current.map((m) => movByMonth.get(m) ?? 0),
      rankItems, stackSeries, stackData, heatRows, barsData, monthCat,
    };
  }, [months, catRows, period]);

  const loading = monthly.loading || catMonthly.loading;
  const error = monthly.error ?? catMonthly.error;
  const pv = (x: number) => (compare && d?.hasPrev ? x : null);

  const chartFallback = <div className="chart-fallback">{t("common.loadingChart")}</div>;
  const drillCategory = (category: string) => navigate(`/transactions?category=${category}&sign=expense`);
  const drillCell = (category: string, month: string) =>
    navigate(`/transactions?category=${category}&sign=expense&date_from=${month}&date_to=${endOfMonth(month)}`);

  const rangeNote =
    compare && d && d.hasPrev
      ? `${monthShort(d.current[0])}–${monthShort(d.current[d.current.length - 1])} vs ` +
        `${monthShort(d.previous[0])}–${monthShort(d.previous[d.previous.length - 1])}`
      : "";

  return (
    <div className="fade-in">
      <header className="greeting">
        <h1>
          {profile.data?.given_name
            ? t("home.hello", { name: profile.data.given_name })
            : t("home.helloAnon")}
          <PrivacyToggle />
        </h1>
        <p>{t("home.subtitle")}</p>
      </header>

      {/* Not an error: a joint account legitimately names two people. Surfaced
          so loading someone else's statements by mistake does not go unnoticed. */}
      {profile.data?.mixed_holders ? (
        <div className="panel notice">
          <strong>{t("home.mixed", { n: profile.data.people.length })}</strong>{" "}
          {profile.data.people.join(" · ")} — {t("home.mixed.hint")}
        </div>
      ) : null}

      {error ? <div className="panel state error">{error}</div> : null}
      {loading && !d ? <div className="panel state">{t("dash.reconciling")}</div> : null}

      {d ? (
        <>
          {/* Page-level controls live IN the page: portaled into the topbar
              they crowded the chrome (user feedback). */}
          <div className="page-controls">
            <div className="segmented" role="group" aria-label={t("dash.period")}>
              {PERIODS.map((pp) => (
                <button key={pp.key} aria-pressed={period === pp.key} onClick={() => setPeriod(pp.key)}>
                  {pp.key === "all" ? t("common.all") : pp.label}
                </button>
              ))}
            </div>
            <button
              className="toggle"
              aria-pressed={compare}
              onClick={() => setCompare((v) => !v)}
              title={t("dash.compareTitle")}
            >
              <span className="sw" /> {t("dash.compare")}
            </button>
            {rangeNote ? <span className="range-note">{rangeNote}</span> : null}
          </div>

          {/* The page's one headline (hero-number pattern): net for the
              period, colored by sign, with the vs-previous delta beside it. */}
          <section className="hero">
            <div className="eyebrow">{t("kpi.net")}</div>
            <div className={`figure ${d.c.net >= 0 ? "pos" : "neg"}`}>
              {money(d.c.net)}
              <Delta current={d.c.net} previous={pv(d.p.net)} goodWhenUp />
            </div>
            <div className="sub">{t("dash.hero.sub", { n: d.movCur })}</div>
          </section>

          <div className="kpis kpis-4">
            <Kpi label={t("kpi.income")} value={money(d.c.income)} spark={d.c.sInc} color="var(--income)">
              <Delta current={d.c.income} previous={pv(d.p.income)} goodWhenUp />
            </Kpi>
            <Kpi label={t("kpi.expense")} value={money(d.expAbs)} spark={d.c.sExp} color="var(--expense)">
              <Delta current={d.expAbs} previous={pv(Math.abs(d.p.expense))} goodWhenUp={false} />
            </Kpi>
            <Kpi label={t("kpi.savings")} value={`${Math.round(d.savings)}%`} spark={d.c.sNet} color="var(--series-3)">
              <Delta current={d.savings} previous={pv(d.savingsPrev)} goodWhenUp unit="pp" />
            </Kpi>
            <Kpi label={t("kpi.avg")} value={money(d.avg)} spark={d.c.sExp} color="var(--series-4)">
              <Delta current={d.avg} previous={pv(d.avgPrev)} goodWhenUp={false} />
            </Kpi>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{t("panel.spendingOverTime")}</h2>
              <span className="hint">{t("panel.spendingOverTime.hint", { n: TOP_STACK })}</span>
            </div>
            <Suspense fallback={chartFallback}>
              <StackedArea data={d.stackData} series={d.stackSeries} />
            </Suspense>
          </div>

          <div className="grid wide-narrow">
            <div className="panel">
              <div className="panel-head">
                <h2>{t("panel.categories")}</h2>
                <span className="hint">{t("panel.categories.hint")}</span>
              </div>
              <RankBars items={d.rankItems} onSelect={drillCategory} />
            </div>
            <div className="panel">
              <div className="panel-head">
                <h2>{t("panel.incomeExpense")}</h2>
              </div>
              <Suspense fallback={chartFallback}>
                <MonthlyBars data={d.barsData} />
              </Suspense>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{t("panel.intensity")}</h2>
              <span className="hint">{t("panel.intensity.hint")}</span>
            </div>
            <Heatmap
              rows={d.heatRows}
              months={d.current}
              monthLabels={d.current.map(monthShort)}
              valueOf={(cat, m) => d.monthCat.get(m)?.get(cat) ?? 0}
              onPick={drillCell}
            />
          </div>

          {merchItems ? (
            <div className="panel">
              <div className="panel-head">
                <h2>{t("panel.merchants")}</h2>
                <span className="hint">{t("panel.merchants.hint")}</span>
              </div>
              <RankBars
                items={merchItems}
                onSelect={(m) => navigate(`/transactions?merchant=${encodeURIComponent(m)}`)}
              />
              {merch.data?.[0] && merch.data[0].n_merchants > merchItems.length ? (
                <div className="panel-foot dim">
                  {t("panel.merchants.more", { n: merch.data[0].n_merchants - merchItems.length })}
                </div>
              ) : null}
            </div>
          ) : null}

          {/* Recurring commitments, independent of the selected period: a
              subscription is a fact about the present, not about a window. */}
          {rec.data?.items.some((i) => i.active) ? (
            <div className="panel">
              <div className="panel-head">
                <h2>{t("rec.title")}</h2>
                <span className="hint">{t("rec.hint")}</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>{t("rec.what")}</th>
                    <th>{t("rec.cadence")}</th>
                    <th className="num">{t("rec.amount")}</th>
                    <th className="num">{t("rec.perMonth")}</th>
                    <th>{t("rec.next")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rec.data.items
                    .filter((i) => i.active)
                    .map((i) => (
                      <tr key={`${i.description}|${i.amount}`}>
                        <td className="desc" title={i.description}>
                          <span className="cat-cell">
                            <span className="swatch" style={{ background: colorFor(i.category) }} />
                            {i.description}
                          </span>
                        </td>
                        <td className="dim">{t(`cadence.${i.cadence}`)}</td>
                        <td className="num mono">{money(num(i.amount))}</td>
                        <td className="num mono dim">{money(num(i.monthly_equivalent))}</td>
                        <td className="dim">{i.next_expected ? dateLabel(i.next_expected) : "—"}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
              <div className="panel-foot dim">
                {t("rec.totals", {
                  out: money(Math.abs(num(rec.data.monthly_expense))),
                  inc: money(num(rec.data.monthly_income)),
                })}
                {rec.data.items.length > rec.data.n_active
                  ? ` · ${t("rec.inactive", { n: rec.data.items.length - rec.data.n_active })}`
                  : ""}
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function Kpi({
  label,
  value,
  cls,
  spark,
  color,
  children,
}: {
  label: string;
  value: string;
  cls?: string;
  spark: number[];
  color: string;
  children: React.ReactNode;
}) {
  return (
    <div className="kpi">
      <div className="k">{label}</div>
      <div className={`v ${cls ?? ""}`}>{value}</div>
      <div className="foot">
        {children}
        <span className="spark" style={{ color }}>
          <Sparkline values={spark} />
        </span>
      </div>
    </div>
  );
}
