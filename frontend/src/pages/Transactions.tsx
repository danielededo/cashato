import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { TransactionFilters, TransactionRow } from "../api/types";
import { SOURCES } from "../api/types";
import { colorFor } from "../lib/colors";
import { dateLabel, money } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const CATEGORY_CODES = [
  "groceries", "dining", "transport", "bills", "subscriptions", "salary", "rent",
  "health", "shopping", "transfers", "investments", "crypto", "cash", "fees", "other",
];
const PAGE = 50;

export function Transactions() {
  // draft filters (form) vs applied filters (drives the query)
  const [draft, setDraft] = useState<TransactionFilters>({ include_transfers: true });
  const [applied, setApplied] = useState<TransactionFilters>({ include_transfers: true });
  const [offset, setOffset] = useState(0);
  // local overrides for rows the user just recategorized (feedback is eventual)
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  const q = useMemo(() => ({ ...applied, lang: "it" as const, limit: PAGE, offset }), [applied, offset]);
  const state = useAsync(() => api.transactions(q), [q]);

  function apply() {
    setOffset(0);
    setApplied(draft);
  }
  function set<K extends keyof TransactionFilters>(k: K, v: TransactionFilters[K]) {
    setDraft((d) => ({ ...d, [k]: v === "" ? undefined : v }));
  }

  async function recategorize(row: TransactionRow, code: string) {
    setOverrides((o) => ({ ...o, [row.natural_key]: code }));
    try {
      await api.feedback(row.natural_key, code, "frontend");
    } catch (e) {
      setOverrides((o) => ({ ...o, [row.natural_key]: row.category ?? "" }));
      alert(`Feedback failed: ${e instanceof Error ? e.message : e}`);
    }
  }

  return (
    <>
      <h1>Transactions</h1>

      <div className="card">
        <div className="filters">
          <label className="field">from
            <input type="date" onChange={(e) => set("date_from", e.target.value)} />
          </label>
          <label className="field">to
            <input type="date" onChange={(e) => set("date_to", e.target.value)} />
          </label>
          <label className="field">category
            <select onChange={(e) => set("category", e.target.value)}>
              <option value="">all</option>
              {CATEGORY_CODES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="field">sign
            <select onChange={(e) => set("sign", (e.target.value || undefined) as TransactionFilters["sign"])}>
              <option value="">all</option>
              <option value="income">income</option>
              <option value="expense">expense</option>
            </select>
          </label>
          <label className="field">source
            <select onChange={(e) => set("source", e.target.value)}>
              <option value="">all</option>
              {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="field">search
            <input type="text" placeholder="description…" onChange={(e) => set("q", e.target.value)} />
          </label>
          <label className="field">transfers
            <select onChange={(e) => set("include_transfers", e.target.value !== "exclude")}>
              <option value="include">include</option>
              <option value="exclude">exclude</option>
            </select>
          </label>
          <button className="primary" onClick={apply}>Apply</button>
        </div>
      </div>

      <div className="card">
        {state.loading && <div className="state">Loading…</div>}
        {state.error && <div className="state error">{state.error}</div>}
        {state.data && (
          <>
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Description</th><th>Category</th>
                  <th className="num">Amount</th><th>Account</th><th>Source</th>
                </tr>
              </thead>
              <tbody>
                {state.data.transactions.map((t) => {
                  const cat = overrides[t.natural_key] ?? t.category ?? "";
                  return (
                    <tr key={t.natural_key}>
                      <td>{dateLabel(t.value_date)}</td>
                      <td className="desc" title={t.description}>{t.description}</td>
                      <td>
                        <span className="swatch" style={{ background: colorFor(cat) }} />
                        <select value={cat} onChange={(e) => recategorize(t, e.target.value)}>
                          {!CATEGORY_CODES.includes(cat) && cat && <option value={cat}>{cat}</option>}
                          {CATEGORY_CODES.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="num"><span className={`amt ${t.amount < 0 ? "neg" : "pos"}`}>{money(t.amount)}</span></td>
                      <td>{t.account}</td>
                      <td>{t.source}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="pager">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Prev</button>
              <span>{offset + 1}–{Math.min(offset + PAGE, state.data.total)} of {state.data.total}</span>
              <button disabled={offset + PAGE >= state.data.total} onClick={() => setOffset(offset + PAGE)}>Next →</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
