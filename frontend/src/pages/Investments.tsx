// Wealth: everything money turns into that is not consumption.
//
// Securities, but also pension funds, term deposits and accumulation policies —
// they are all wealth changing form rather than being spent, so they sit on the
// same side of the line. A section per destination kind is rendered only when
// that kind has movements, so the page grows with the data instead of showing
// empty panels.
//
// Insurance is deliberately NOT here by default: a protection policy is real
// consumption, and only an accumulation policy is wealth. That split is a manual
// reclassification, never inferred from a transfer description.
//
// Investments on the two levels the statements actually support.
//
// CONTRIBUTIONS are always knowable: money leaving towards investing, including
// a plain transfer to an outside broker. POSITIONS need the source to disclose
// the instrument, which a bank transfer never does. The page shows both and
// states the gap between them — presenting only the instruments we happen to
// know would quietly understate what was invested.
//
// No market prices anywhere: the last price we have is the one printed on a
// statement, so anything derived from it is labelled as of that date rather
// than dressed up as today's value.

import { lazy, Suspense, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Row, SeriesDef } from "../components/charts";
import { RankBars, Sparkline, type RankItem } from "../components/primitives";
import { colorFor, seriesColor } from "../lib/colors";
import { dateLabel, money, monthShort } from "../lib/format";
import { useT } from "../lib/i18n";
import { useLang } from "../lib/lang";
import { useAsync } from "../lib/useAsync";

const StackedArea = lazy(() => import("../components/charts").then((m) => ({ default: m.StackedArea })));
const chartFallback = <div className="chart-fallback">Loading chart…</div>;

const KNOWN = "known";
const UNKNOWN = "unknown";

export function Investments() {
  const { t } = useT();
  const { lang } = useLang();
  // Cumulative by default: this page is about accumulated wealth, and an area
  // chart reads as a running total. Monthly stays available because it answers
  // a different question — the pace of contribution, not the amount built up.
  const [cumulative, setCumulative] = useState(true);
  const inv = useAsync(() => api.investments(lang), [lang]);

  const d = useMemo(() => {
    const data = inv.data;
    if (!data || (!data.months.length && !data.holdings.length)) return null;

    // Contributions over time, split by whether the instrument is known. Stacked
    // so the two read as parts of one total rather than competing series.
    const series: SeriesDef[] = [
      { key: KNOWN, label: t("inv.known"), category: "investments" },
      { key: UNKNOWN, label: t("inv.unknown"), category: "other" },
    ];
    // The months arrive per kind, so fold them into one row per month.
    const byMonth = new Map<string, Row>();
    for (const m of data.months) {
      const row = byMonth.get(m.month) ?? { month: monthShort(m.month) };
      row[KNOWN] = ((row[KNOWN] as number) ?? 0) + (m.into_known ?? 0);
      row[UNKNOWN] = ((row[UNKNOWN] as number) ?? 0) + (m.into_unknown ?? 0);
      byMonth.set(m.month, row);
    }
    const monthly: Row[] = [...byMonth.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([, row]) => row);
    // Running totals per series, so the stacked bands keep adding up.
    let runK = 0;
    let runU = 0;
    const stackData: Row[] = cumulative
      ? monthly.map((r) => {
          runK += (r[KNOWN] as number) ?? 0;
          runU += (r[UNKNOWN] as number) ?? 0;
          return { month: r.month, [KNOWN]: runK, [UNKNOWN]: runU };
        })
      : monthly;
    // Sparkline always shows the monthly pace, whatever the chart is showing.
    const spark = monthly.map((r) => ((r[KNOWN] as number) ?? 0) + ((r[UNKNOWN] as number) ?? 0));

    // Keyed by ISIN so each row is distinct; colours come from the ramp because
    // an ISIN is not a category code.
    const split: RankItem[] = data.holdings.map((h, i) => ({
      category: h.isin ?? h.instrument ?? String(i),
      label: h.instrument ?? h.isin ?? "—",
      value: h.invested,
      color: seriesColor(i),
    }));

    const unknownPct = data.total_contributed
      ? (data.total_in_unknown / data.total_contributed) * 100
      : 0;
    return { ...data, series, stackData, spark, split, unknownPct };
  }, [inv.data, t, cumulative]);

  return (
    <div className="fade-in">
      {inv.error ? <div className="panel state error">{inv.error}</div> : null}
      {inv.loading && !inv.data ? <div className="panel state">{t("common.loading")}</div> : null}
      {inv.data && !d ? (
        <div className="panel empty">
          <div className="big">{t("inv.empty")}</div>
          <div className="sub">{t("inv.emptySub")}</div>
        </div>
      ) : null}

      {d ? (
        <>
          <div className="kpis kpis-4">
            <div className="kpi">
              {/* Gross, so that the two tiles beside it add up to this one. Net
                  is the footnote: three figures where two do not sum to the
                  first is an invitation to misread them. */}
              <div className="k">{t("inv.invested")}</div>
              <div className="v">{money(d.total_contributed)}</div>
              <div className="foot">
                <span className="dim">{t("inv.net", { v: money(d.total_invested) })}</span>
                <span className="spark" style={{ color: "var(--series-1)" }}>
                  <Sparkline values={d.spark} />
                </span>
              </div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.known")}</div>
              <div className="v">{money(d.total_in_known_instruments)}</div>
              <div className="foot">
                <span className="dim">
                  {d.holdings.length} {t("inv.instruments")}
                </span>
              </div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.unknown")}</div>
              <div className="v">{money(d.total_in_unknown)}</div>
              <div className="foot">
                <span className="dim">{Math.round(d.unknownPct)}% {t("inv.ofTotal")}</span>
              </div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.returns")}</div>
              <div className="v">{money(d.total_returned)}</div>
              <div className="foot">
                <span className="dim">{t("inv.returns.foot")}</span>
              </div>
            </div>
          </div>

          {/* One row per destination kind that actually has movements. With a
              single kind this says nothing new, so it is not rendered. */}
          {d.kinds.length > 1 ? (
            <div className="panel">
              <div className="panel-head">
                <h2>{t("inv.kinds")}</h2>
                <span className="hint">{t("inv.kinds.hint")}</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>{t("inv.kind")}</th>
                    <th className="num">{t("inv.investedCol")}</th>
                    <th className="num">{t("inv.returns")}</th>
                    <th className="num">{t("common.movements")}</th>
                    <th>{t("inv.detailLevel")}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.kinds.map((k) => (
                    <tr key={k.category}>
                      <td>
                        <span className="cat-cell">
                          <span className="swatch" style={{ background: colorFor(k.category) }} />
                          {k.category_label}
                        </span>
                      </td>
                      <td className="num mono">{money(k.net_invested)}</td>
                      <td className="num mono dim">{money(k.returned)}</td>
                      <td className="num mono dim">{k.n_movements}</td>
                      <td className="dim">
                        {k.has_instruments ? t("inv.withInstruments") : t("inv.amountOnly")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {/* Honesty note, not decoration: without it the instrument table reads
              as the whole portfolio when it may be only part of it. */}
          {d.total_in_unknown > 0 ? (
            <div className="panel notice">{t("inv.unknownNote")}</div>
          ) : null}

          <div className="panel">
            <div className="panel-head">
              <h2>{cumulative ? t("inv.flow.cum") : t("inv.flow")}</h2>
              <span className="hint">
                {cumulative ? t("inv.flow.cum.hint") : t("inv.flow.hint")}
              </span>
              <button
                className="toggle"
                aria-pressed={cumulative}
                onClick={() => setCumulative((v) => !v)}
                style={{ marginLeft: "auto" }}
              >
                <span className="sw" /> {t("inv.cumulative")}
              </button>
            </div>
            <Suspense fallback={chartFallback}>
              <StackedArea data={d.stackData} series={d.series} />
            </Suspense>
          </div>

          {d.holdings.length ? (
            <>
              <div className="panel">
                <div className="panel-head">
                  <h2>{t("inv.allocation")}</h2>
                  <span className="hint">{t("inv.allocation.hint")}</span>
                </div>
                <RankBars items={d.split} />
              </div>

              <div className="panel">
                <div className="panel-head">
                  <h2>{t("inv.positions")}</h2>
                  <span className="hint">{t("inv.positions.hint")}</span>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>{t("inv.instrument")}</th>
                      <th>ISIN</th>
                      <th className="num">{t("inv.units")}</th>
                      <th className="num">{t("inv.investedCol")}</th>
                      <th className="num">{t("inv.avgPrice")}</th>
                      <th className="num">{t("inv.lastPrice")}</th>
                      <th className="num">{t("inv.trades")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.holdings.map((h) => (
                      <tr key={h.isin ?? h.instrument}>
                        <td className="desc" title={h.instrument ?? ""}>{h.instrument ?? "—"}</td>
                        <td className="mono dim">{h.isin ?? "—"}</td>
                        <td className="num mono">{h.units.toFixed(4)}</td>
                        <td className="num mono">{money(h.invested)}</td>
                        <td className="num mono dim">
                          {h.units ? money(h.invested / h.units) : "—"}
                        </td>
                        <td className="num mono dim" title={h.last_trade ? t("inv.asOf", { d: dateLabel(h.last_trade) }) : ""}>
                          {h.last_price != null ? money(h.last_price) : "—"}
                        </td>
                        <td className="num mono dim">{h.n_trades}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="panel-foot dim">{t("inv.priceCaveat")}</div>
              </div>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
