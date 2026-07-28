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

import { lazy, Suspense, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Row, SeriesDef } from "../components/charts";
import { Donut, RankBars, Sparkline, type DonutSlice, type RankItem } from "../components/primitives";
import { useAccounts } from "../lib/accounts";
import { colorFor, seriesColor } from "../lib/colors";
import { dateLabel, money, monthShort, num } from "../lib/format";
import { useT } from "../lib/i18n";
import { useLang } from "../lib/lang";
import { useAsync } from "../lib/useAsync";

const StackedArea = lazy(() => import("../components/charts").then((m) => ({ default: m.StackedArea })));

const TOTAL = "total";

export function Investments() {
  const { t } = useT();
  const chartFallback = <div className="chart-fallback">{t("common.loadingChart")}</div>;
  const { lang } = useLang();
  // Cumulative by default: this page is about accumulated wealth, and an area
  // chart reads as a running total. Monthly stays available because it answers
  // a different question — the pace of contribution, not the amount built up.
  const [cumulative, setCumulative] = useState(true);
  const inv = useAsync(() => api.investments(lang), [lang]);
  const wealth = useAsync(() => api.wealth(), []);
  const { accountShort } = useAccounts();

  // Liquid side of the page: the balances the statements themselves declare,
  // carried forward per month. Complementary to the invested flow below — no
  // market prices, no overlap.
  const wd = useMemo(() => {
    const data = wealth.data;
    if (!data || !data.months.length) return null;

    // Colour follows the ACCOUNT (stable alphabetical slot), not its rank —
    // same reasoning as the holdings below.
    const ids = [...new Set(data.months.map((r) => r.account))].sort();
    const series: SeriesDef[] = ids.map((id) => ({
      key: id,
      label: accountShort(id),
      category: id,
      color: seriesColor(ids.indexOf(id)),
    }));

    const byMonth = new Map<string, Row>();
    for (const r of data.months) {
      const row = byMonth.get(r.month) ?? { month: monthShort(r.month) };
      row[r.account] = num(r.balance);
      byMonth.set(r.month, row);
    }
    const stackData: Row[] = [...byMonth.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([, row]) => {
        // Months before an account's first anchor: it holds nothing yet.
        for (const id of ids) row[id] = (row[id] as number | undefined) ?? 0;
        return row;
      });

    // A carried-forward figure much older than its siblings deserves a note,
    // not silence: the stack still shows it as if it were current.
    const newest = data.accounts.reduce((m, a) => (a.as_of > m ? a.as_of : m), "");
    const staleDays = (iso: string) =>
      (new Date(newest).getTime() - new Date(iso).getTime()) / 86_400_000;
    const stale = data.accounts.filter((a) => staleDays(a.as_of) > 45);

    return { total: num(data.total_liquid), oldest: data.oldest_as_of, series, stackData, stale };
  }, [wealth.data, accountShort]);

  const d = useMemo(() => {
    const data = inv.data;
    if (!data || (!data.months.length && !data.holdings.length)) return null;

    // ONE series (the known/unknown split lives in the KPI tiles) of the NET
    // flow: contributed minus returned. Money coming back from a sale is not
    // still invested. Gross stays in the hero, and the sparkline keeps the
    // gross pace with it.
    const series: SeriesDef[] = [{ key: TOTAL, label: t("inv.flow.series"), category: "investments" }];
    // The months arrive per kind, so fold them into one row per month.
    const byMonth = new Map<string, Row>();
    const grossByMonth = new Map<string, number>();
    for (const m of data.months) {
      const row = byMonth.get(m.month) ?? { month: monthShort(m.month) };
      row[TOTAL] = ((row[TOTAL] as number) ?? 0) + num(m.net_invested);
      byMonth.set(m.month, row);
      grossByMonth.set(m.month, (grossByMonth.get(m.month) ?? 0) + num(m.contributed));
    }
    const monthly: Row[] = [...byMonth.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([, row]) => row);
    let run = 0;
    const stackData: Row[] = cumulative
      ? monthly.map((r) => ({ month: r.month, [TOTAL]: (run += (r[TOTAL] as number) ?? 0) }))
      : monthly;
    const spark = [...grossByMonth.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([, v]) => v);

    // Colour follows the INSTRUMENT, not its rank: assigning by position in the
    // invested-sorted list would repaint every holding as soon as one overtakes
    // another, or as soon as a new one appears. Keyed off a stable alphabetical
    // index instead, so a given ISIN keeps its colour for good.
    const ids = data.holdings
      .map((h, i) => h.isin ?? h.instrument ?? String(i))
      .slice()
      .sort();
    const colorOf = (id: string) => seriesColor(ids.indexOf(id));

    const split: RankItem[] = data.holdings.map((h, i) => {
      const id = h.isin ?? h.instrument ?? String(i);
      return { category: id, label: h.instrument ?? h.isin ?? "—", value: num(h.invested), color: colorOf(id) };
    });
    const donut: DonutSlice[] = data.holdings.map((h, i) => {
      const id = h.isin ?? h.instrument ?? String(i);
      return { key: id, label: h.instrument ?? h.isin ?? "—", value: num(h.invested), color: colorOf(id) };
    });

    const contributed = num(data.total_contributed);
    const unknownPct = contributed ? (num(data.total_in_unknown) / contributed) * 100 : 0;
    return { ...data, series, stackData, spark, split, donut, unknownPct };
  }, [inv.data, t, cumulative]);

  // Rendered twice below: in place with the investment sections, or alone when
  // the statements declare balances but no wealth movement exists yet.
  const liquidityPanel = wd ? (
    <div className="panel">
      <div className="panel-head">
        <h2>{t("wealth.balances")}</h2>
        <span className="hint">{t("wealth.balances.hint")}</span>
      </div>
      <Suspense fallback={chartFallback}>
        <StackedArea data={wd.stackData} series={wd.series} />
      </Suspense>
      {wd.stale.length ? (
        <div className="panel-foot dim">
          {wd.stale
            .map((a) => t("wealth.staleNote", { account: accountShort(a.account), d: dateLabel(a.as_of) }))
            .join(" · ")}
        </div>
      ) : null}
    </div>
  ) : null;

  return (
    <div className="fade-in">
      {inv.error ? <div className="panel state error">{inv.error}</div> : null}
      {inv.loading && !inv.data ? <div className="panel state">{t("common.loading")}</div> : null}
      {inv.data && !d && !wealth.loading && !wd ? (
        <div className="panel empty">
          <div className="big">{t("inv.empty")}</div>
          <div className="sub">{t("inv.emptySub")}</div>
        </div>
      ) : null}
      {!d && wd ? liquidityPanel : null}

      {d ? (
        <>
          {/* Hero: gross contributed, so the known/unknown tiles below add up
              to it; net of returns is the subtitle — three figures where two
              do not sum to the first is an invitation to misread them. */}
          <section className="hero">
            <div className="eyebrow">{t("inv.invested")}</div>
            <div className="figure">
              {money(num(d.total_contributed))}
              <span className="spark" style={{ color: "var(--series-1)" }}>
                <Sparkline values={d.spark} width={140} height={34} />
              </span>
            </div>
            <div className="sub">{t("inv.net", { v: money(num(d.total_invested)) })}</div>
          </section>

          <div className="kpis kpis-4">
            <div className="kpi">
              <div className="k">{t("inv.known")}</div>
              <div className="v">{money(num(d.total_in_known_instruments))}</div>
              <div className="foot">
                <span className="dim">
                  {d.holdings.length} {t("inv.instruments")}
                </span>
              </div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.unknown")}</div>
              <div className="v">{money(num(d.total_in_unknown))}</div>
              <div className="foot">
                <span className="dim">{Math.round(d.unknownPct)}% {t("inv.ofTotal")}</span>
              </div>
            </div>
            <div className="kpi">
              <div className="k">{t("inv.returns")}</div>
              <div className="v">{money(num(d.total_returned))}</div>
              <div className="foot">
                <span className="dim">{t("inv.returns.foot")}</span>
              </div>
            </div>
            {wd ? (
              <div className="kpi">
                <div className="k">{t("wealth.liquid")}</div>
                <div className="v">{money(wd.total)}</div>
                <div className="foot">
                  <span className="dim">
                    {wd.oldest ? t("inv.asOf", { d: dateLabel(wd.oldest) }) : ""}
                  </span>
                </div>
              </div>
            ) : null}
          </div>

          {liquidityPanel}

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
                    <th className="num">{t("inv.invested")}</th>
                    <th className="num">{t("inv.returns")}</th>
                    <th className="num">{t("inv.netCol")}</th>
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
                      <td className="num mono">{money(num(k.contributed))}</td>
                      <td className="num mono dim">{money(num(k.returned))}</td>
                      <td className="num mono">{money(num(k.net_invested))}</td>
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
          {num(d.total_in_unknown) > 0 ? (
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
                <div className="alloc">
                  <Donut
                    slices={d.donut}
                    total={num(d.total_in_known_instruments)}
                    centerLabel={t("inv.known")}
                  />
                  <RankBars items={d.split} />
                </div>
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
                    {d.holdings.map((h, i) => (
                      <tr key={h.isin ?? h.instrument ?? String(i)}>
                        <td className="desc" title={h.instrument ?? ""}>{h.instrument ?? "—"}</td>
                        <td className="mono dim">{h.isin ?? "—"}</td>
                        <td className="num mono">{num(h.units).toFixed(4)}</td>
                        <td className="num mono">{money(num(h.invested))}</td>
                        <td className="num mono dim">
                          {num(h.units) ? money(num(h.invested) / num(h.units)) : "—"}
                        </td>
                        <td className="num mono dim" title={h.last_trade ? t("inv.asOf", { d: dateLabel(h.last_trade) }) : ""}>
                          {h.last_price != null ? money(num(h.last_price)) : "—"}
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
