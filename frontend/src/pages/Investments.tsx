import { lazy, Suspense, useMemo } from "react";
import { api } from "../api/client";
import type { Row, SeriesDef } from "../components/charts";
import { RankBars, Sparkline, type RankItem } from "../components/primitives";
import { money, monthShort } from "../lib/format";
import { useT } from "../lib/i18n";
import { useLang } from "../lib/lang";
import { useAsync } from "../lib/useAsync";

const StackedArea = lazy(() => import("../components/charts").then((m) => ({ default: m.StackedArea })));
const chartFallback = <div className="chart-fallback">Loading chart…</div>;

const ASSET_CODES = ["investments", "crypto"];

export function Investments() {
  const { t, lang } = useT();
  useLang(); // labels come localized from the API via lang below
  const catMonthly = useAsync(() => api.categoriesMonthly(lang), [lang]);

  const d = useMemo(() => {
    const rows = catMonthly.data?.rows.filter((r) => ASSET_CODES.includes(r.category));
    if (!rows || !rows.length) return null;

    const months = [...new Set(rows.map((r) => r.month))].sort();
    let contrib = 0; // money put in (outflows)
    let returns = 0; // money back (inflows: dividends, sales)
    const byAsset = new Map<string, { label: string; contrib: number }>();
    const perMonth = new Map<string, Record<string, number>>();
    for (const m of months) perMonth.set(m, {});
    for (const r of rows) {
      const v = r.total ?? 0;
      if (v < 0) {
        contrib += -v;
        const e = byAsset.get(r.category) ?? { label: r.category_label, contrib: 0 };
        e.contrib += -v;
        byAsset.set(r.category, e);
        perMonth.get(r.month)![r.category] = (perMonth.get(r.month)![r.category] ?? 0) + -v;
      } else {
        returns += v;
      }
    }

    const series: SeriesDef[] = [...byAsset.entries()]
      .sort((a, b) => b[1].contrib - a[1].contrib)
      .map(([category, v]) => ({ key: category, label: v.label, category }));
    const stackData: Row[] = months.map((m) => {
      const row: Row = { month: monthShort(m) };
      for (const s of series) row[s.key] = perMonth.get(m)?.[s.category] ?? 0;
      return row;
    });
    const split: RankItem[] = [...byAsset.entries()]
      .sort((a, b) => b[1].contrib - a[1].contrib)
      .map(([category, v]) => ({ category, label: v.label, value: v.contrib }));
    const spark = months.map((m) => Object.values(perMonth.get(m) ?? {}).reduce((a, b) => a + b, 0));

    return { contrib, returns, net: contrib - returns, series, stackData, split, spark };
  }, [catMonthly.data]);

  return (
    <div className="fade-in">
      {catMonthly.loading && !d ? <div className="panel state">{t("common.loading")}</div> : null}
      {catMonthly.error ? <div className="panel state error">{catMonthly.error}</div> : null}
      {catMonthly.data && !d ? (
        <div className="panel">
          <div className="empty">
            <div className="big">{t("inv.empty")}</div>
            <div className="sub">{t("inv.emptySub")}</div>
          </div>
        </div>
      ) : null}

      {d ? (
        <>
          <div className="kpis" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <div className="kpi">
              <div className="k">{t("inv.invested")}</div>
              <div className="v">{money(d.net)}</div>
              <div className="foot"><span className="spark" style={{ color: "var(--series-1)" }}><Sparkline values={d.spark} /></span></div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.contrib")}</div>
              <div className="v neg">{money(-d.contrib)}</div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.returns")}</div>
              <div className="v pos">{money(d.returns)}</div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>{t("inv.flow")}</h2></div>
            <Suspense fallback={chartFallback}>
              <StackedArea data={d.stackData} series={d.series} />
            </Suspense>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>{t("inv.split")}</h2></div>
            <RankBars items={d.split} />
          </div>
        </>
      ) : null}
    </div>
  );
}
