import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { TransactionRow } from "../api/types";
import { colorFor } from "../lib/colors";
import { dateLabel, money } from "../lib/format";
import { catLabel, useT } from "../lib/i18n";
import { useAsync } from "../lib/useAsync";

const CATEGORY_CODES = [
  "groceries", "dining", "transport", "bills", "subscriptions", "salary", "rent",
  "health", "shopping", "transfers", "investments", "crypto", "cash", "fees", "other",
];
const SAMPLE = 500;
const QUEUE = 100;

// How much can we trust a category? Colour each provenance by certainty.
const SOURCE_META: Record<string, { tkey: string; color: string; trust: "sure" | "guess" | "none" }> = {
  manual: { tkey: "src.manual", color: "var(--series-3)", trust: "sure" },
  mcc: { tkey: "src.mcc", color: "var(--series-6)", trust: "sure" },
  rule: { tkey: "src.rule", color: "var(--series-1)", trust: "sure" },
  model: { tkey: "src.model", color: "var(--series-2)", trust: "guess" },
  default: { tkey: "src.default", color: "var(--expense)", trust: "none" },
  unknown: { tkey: "src.unknown", color: "var(--cat-other)", trust: "none" },
};
const SOURCE_ORDER = ["manual", "mcc", "rule", "model", "default", "unknown"];

const LOWCONF_MAX = 0.7;

export function Review() {
  const { t, lang } = useT();
  const [mode, setMode] = useState<"other" | "lowconf">("other");
  const sample = useAsync(() => api.transactions({ limit: SAMPLE, include_transfers: false, lang }), [lang]);
  const queue = useAsync(
    () =>
      api.transactions(
        mode === "other"
          ? { category: "other", limit: QUEUE, include_transfers: false, lang }
          : { category_source: "model", max_confidence: LOWCONF_MAX, limit: QUEUE, include_transfers: false, lang },
      ),
    [lang, mode],
  );
  const [labelled, setLabelled] = useState<Record<string, string>>({});

  const trust = useMemo(() => {
    const rows = sample.data?.transactions;
    if (!rows || !rows.length) return null;
    const bySource = new Map<string, number>();
    let confSum = 0, confN = 0, other = 0;
    for (const r of rows) {
      const src = r.category_source ?? "unknown";
      bySource.set(src, (bySource.get(src) ?? 0) + 1);
      if (r.category_confidence != null) { confSum += r.category_confidence; confN += 1; }
      if (r.category === "other") other += 1;
    }
    const total = rows.length;
    const breakdown = SOURCE_ORDER.filter((s) => bySource.has(s)).map((s) => ({
      source: s, n: bySource.get(s)!, pct: (bySource.get(s)! / total) * 100, ...SOURCE_META[s],
    }));
    const sure = breakdown.filter((b) => b.trust === "sure").reduce((a, b) => a + b.n, 0);
    return { total, breakdown, surePct: (sure / total) * 100, otherPct: (other / total) * 100, avgConf: confN ? confSum / confN : 0 };
  }, [sample.data]);

  async function relabel(row: TransactionRow, code: string) {
    setLabelled((m) => ({ ...m, [row.natural_key]: code }));
    try {
      await api.feedback(row.natural_key, code, "review");
    } catch (e) {
      setLabelled((m) => { const next = { ...m }; delete next[row.natural_key]; return next; });
      alert(`Feedback failed: ${e instanceof Error ? e.message : e}`);
    }
  }

  const queueRows = (queue.data?.transactions ?? []).filter((r) => !labelled[r.natural_key]);
  const done = Object.keys(labelled).length;

  return (
    <div className="fade-in">
      <div className="panel">
        <div className="panel-head">
          <h2>{t("rev.trust")}</h2>
          <span className="hint">{t("rev.trust.hint", { n: SAMPLE })}</span>
        </div>
        {sample.loading && !trust ? <div className="state">{t("rev.estimating")}</div> : null}
        {sample.error ? <div className="state error">{sample.error}</div> : null}
        {trust ? (
          <div>
            <div className="kpis" style={{ gridTemplateColumns: "repeat(3, 1fr)", marginBottom: 12 }}>
              <div className="kpi">
                <div className="k">{t("rev.sure")}</div>
                <div className="v pos">{Math.round(trust.surePct)}%</div>
                <div className="foot"><span className="delta flat">{t("rev.sure.foot")}</span></div>
              </div>
              <div className="kpi">
                <div className="k">{t("rev.conf")}</div>
                <div className="v">{Math.round(trust.avgConf * 100)}%</div>
                <div className="foot"><span className="delta flat">{t("rev.conf.foot")}</span></div>
              </div>
              <div className="kpi">
                <div className="k">{t("rev.other")}</div>
                <div className="v neg">{Math.round(trust.otherPct)}%</div>
                <div className="foot"><span className="delta flat">{t("rev.other.foot")}</span></div>
              </div>
            </div>
            <div className="ranks">
              {trust.breakdown.map((b) => (
                <div key={b.source} className="rank" style={{ cursor: "default", gridTemplateColumns: "148px 1fr 92px" }}>
                  <span className="name"><span className="swatch" style={{ background: b.color }} />{t(b.tkey)}</span>
                  <span className="track"><span className="fill" style={{ width: `${b.pct}%`, background: b.color }} /></span>
                  <span className="amt">{b.n} · {Math.round(b.pct)}%</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{t("rev.queue")}</h2>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="hint">
              {queue.data ? t("rev.total", { n: queue.data.total }) : "…"}
              {done ? t("rev.labelled", { n: done }) : ""}
            </span>
            <div className="segmented" role="group" aria-label="Review mode">
              <button aria-pressed={mode === "other"} onClick={() => setMode("other")}>{t("rev.mode.other")}</button>
              <button aria-pressed={mode === "lowconf"} onClick={() => setMode("lowconf")}>{t("rev.mode.lowconf")}</button>
            </div>
          </div>
        </div>
        {queue.loading && !queue.data ? <div className="state">{t("common.loading")}</div> : null}
        {queue.error ? <div className="state error">{queue.error}</div> : null}
        {queue.data && queueRows.length === 0 ? (
          <div className="empty">
            <div className="big">{done ? t("rev.cleared") : t("rev.nothing")}</div>
            <div className="sub">{t("rev.nothingSub")}</div>
          </div>
        ) : null}
        {queueRows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>{t("common.date")}</th>
                <th>{t("common.description")}</th>
                <th className="num">{t("common.amount")}</th>
                <th>{t("common.account")}</th>
                <th>{t("rev.setCategory")}</th>
              </tr>
            </thead>
            <tbody>
              {queueRows.map((tx) => (
                <tr key={tx.natural_key}>
                  <td className="mono dim">{dateLabel(tx.value_date)}</td>
                  <td className="desc" title={tx.description}>{tx.description}</td>
                  <td className="num"><span className={`amt ${tx.amount < 0 ? "neg" : "pos"}`}>{money(tx.amount)}</span></td>
                  <td className="dim">{tx.account}</td>
                  <td>
                    <span className="cat-cell">
                      <span className="swatch" style={{ background: colorFor(tx.category) }} />
                      <select className="cat" defaultValue="" onChange={(e) => e.target.value && relabel(tx, e.target.value)}>
                        <option value="" disabled>{t("rev.choose")}</option>
                        {CATEGORY_CODES.filter((c) => c !== "other").map((c) => (
                          <option key={c} value={c}>{catLabel(c, lang)}</option>
                        ))}
                      </select>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </div>
  );
}
