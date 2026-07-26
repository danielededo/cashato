import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Sign, TransactionFilters, TransactionRow } from "../api/types";
import { colorFor } from "../lib/colors";
import { dateLabel, isoDate, money, num } from "../lib/format";
import { TransactionDetail } from "../components/TransactionDetail";
import { useAccounts } from "../lib/accounts";
import { useT } from "../lib/i18n";
import { useMeta } from "../lib/meta";
import { useLang } from "../lib/lang";
import { useAsync } from "../lib/useAsync";

const PAGE = 50;
const SIGNS: { key: Sign | ""; tkey: string }[] = [
  { key: "", tkey: "common.all" },
  { key: "income", tkey: "common.income" },
  { key: "expense", tkey: "common.expense" },
];
const DATE_PRESETS = [
  { key: "30d", label: "30d" },
  { key: "90d", label: "90d" },
  { key: "6m", label: "6m" },
  { key: "12m", label: "12m" },
  { key: "ytd", label: "YTD" },
  { key: "all", label: "All" },
  { key: "custom", label: "Custom" },
] as const;
type DatePreset = (typeof DATE_PRESETS)[number]["key"];
const PRESET_DAYS: Record<string, number> = { "30d": 30, "90d": 90, "6m": 182, "12m": 365 };

type SortKey = "date" | "description" | "category" | "amount" | "account";

export function Transactions() {
  const [params] = useSearchParams();
  const { lang } = useLang();
  const { t } = useT();
  const { accounts, accountLabel, accountShort } = useAccounts();
  const { categoryCodes, catLabel, sourceLabel } = useMeta();
  const [detailKey, setDetailKey] = useState<string | null>(null);

  // Filters apply INSTANTLY — no Apply button. Search is deferred so typing never
  // blocks on a fetch (react-best-practices: useDeferredValue).
  const [search, setSearch] = useState(params.get("q") ?? "");
  const deferredSearch = useDeferredValue(search);
  const [sign, setSign] = useState<Sign | "">((params.get("sign") as Sign) ?? "");
  const [source, setSource] = useState(params.get("source") ?? "");
  const [category, setCategory] = useState(params.get("category") ?? "");
  const [includeTransfers, setIncludeTransfers] = useState(true);
  const [dateFrom, setDateFrom] = useState(params.get("date_from") ?? "");
  const [dateTo, setDateTo] = useState(params.get("date_to") ?? "");
  const [showAdvanced, setShowAdvanced] = useState(Boolean(params.get("category") || params.get("date_from")));
  const [datePreset, setDatePreset] = useState<DatePreset>(params.get("date_from") ? "custom" : "all");
  const [offset, setOffset] = useState(0);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "date", dir: "desc" });

  useEffect(() => {
    setOffset(0);
  }, [deferredSearch, sign, source, category, includeTransfers, dateFrom, dateTo, sort]);

  // URL params are read at mount AND on every later change: a nav click to the
  // same route (or a drill-down while already here) clears/changes the query
  // string without remounting, and the filters must follow or the address bar
  // and the list contradict each other.
  useEffect(() => {
    setSearch(params.get("q") ?? "");
    setSign((params.get("sign") as Sign) ?? "");
    setSource(params.get("source") ?? "");
    setCategory(params.get("category") ?? "");
    setDateFrom(params.get("date_from") ?? "");
    setDateTo(params.get("date_to") ?? "");
    setShowAdvanced(Boolean(params.get("category") || params.get("date_from")));
    setDatePreset(params.get("date_from") ? "custom" : "all");
  }, [params]);

  // Sorting is a QUERY param, not a client-side shuffle: re-sorting one loaded
  // page under a global-looking header showed "the biggest of the newest 50",
  // not the biggest overall. The server orders the whole filtered set.
  const query = useMemo<TransactionFilters>(
    () => ({
      lang,
      q: deferredSearch || undefined,
      sign: sign || undefined,
      source: source || undefined,
      category: category || undefined,
      include_transfers: includeTransfers,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      sort: sort.key,
      order: sort.dir,
      limit: PAGE,
      offset,
    }),
    [lang, deferredSearch, sign, source, category, includeTransfers, dateFrom, dateTo, sort, offset],
  );

  const state = useAsync(() => api.transactions(query), [query]);

  const rows = state.data?.transactions ?? [];

  function applyDatePreset(key: DatePreset) {
    setDatePreset(key);
    const now = new Date();
    if (key === "all") {
      setDateFrom("");
      setDateTo("");
    } else if (key === "ytd") {
      setDateFrom(isoDate(new Date(now.getFullYear(), 0, 1)));
      setDateTo(isoDate(now));
    } else if (key !== "custom") {
      const f = new Date(now);
      f.setDate(f.getDate() - PRESET_DAYS[key]);
      setDateFrom(isoDate(f));
      setDateTo(isoDate(now));
    }
  }

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }
  const caret = (key: SortKey) => (sort.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : "");

  async function recategorize(row: TransactionRow, code: string) {
    setOverrides((o) => ({ ...o, [row.natural_key]: code }));
    try {
      await api.feedback(row.natural_key, code, "frontend");
    } catch (e) {
      // Roll back by DELETING the key: writing "" would corrupt the select of
      // a null-category row (Review.tsx does the same).
      setOverrides((o) => {
        const next = { ...o };
        delete next[row.natural_key];
        return next;
      });
      alert(`Feedback failed: ${e instanceof Error ? e.message : e}`);
    }
  }

  // Filters over data that EXISTS: offering a bank you have not loaded is
  // noise, and the list follows the ingested accounts rather than a fixed one.
  const presentSources = useMemo(
    () => [...new Set(accounts.filter((a) => a.transactions > 0).map((a) => a.source))].sort(),
    [accounts],
  );

  const total = state.data?.total ?? 0;
  const of = lang === "it" ? "di" : "of";
  const activeFilters = [
    sign && (sign === "income" ? t("common.income") : t("common.expense")),
    source && sourceLabel(source),
    category && catLabel(category),
    (dateFrom || dateTo) && `${dateFrom || "…"} → ${dateTo || "…"}`,
    !includeTransfers && `${t("tx.exclude")} ${t("tx.transfers").toLowerCase()}`,
    deferredSearch && `“${deferredSearch}”`,
  ].filter(Boolean) as string[];

  return (
    <div className="fade-in">
      <div className="panel">
        <div className="toolbar">
          <label className="search">
            <span className="ico">⌕</span>
            <input type="text" value={search} placeholder={t("tx.search")} onChange={(e) => setSearch(e.target.value)} />
          </label>
          {SIGNS.map((s) => (
            <button key={s.tkey} className="chip" aria-pressed={sign === s.key} onClick={() => setSign(s.key)}>
              {t(s.tkey)}
            </button>
          ))}
          <button className="chip" aria-pressed={source === ""} onClick={() => setSource("")}>
            {t("tx.allSources")}
          </button>
          {presentSources.map((s) => (
            <button key={s} className="chip" aria-pressed={source === s} onClick={() => setSource(source === s ? "" : s)}>
              {sourceLabel(s)}
            </button>
          ))}
          <button className="disclosure" onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? "▾ " : "▸ "}{t("tx.filters")}
          </button>
        </div>

        {showAdvanced ? (
          <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--rule)", display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="toolbar">
              <span className="field" style={{ alignSelf: "center" }}>{t("tx.range")}</span>
              {DATE_PRESETS.map((p) => (
                <button key={p.key} className="chip" aria-pressed={datePreset === p.key} onClick={() => applyDatePreset(p.key)}>
                  {p.key === "custom" ? t("tx.custom") : p.key === "all" ? t("common.all") : p.label}
                </button>
              ))}
            </div>
            {datePreset === "custom" ? (
              <div className="toolbar">
                <label className="field">{t("tx.from")}<input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
                <label className="field">{t("tx.to")}<input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
              </div>
            ) : null}
            <div className="toolbar">
              <label className="field">
                {t("common.category")}
                <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="">{t("common.all")}</option>
                  {categoryCodes.map((c) => <option key={c} value={c}>{catLabel(c)}</option>)}
                </select>
              </label>
              <label className="field">
                {t("tx.transfers")}
                <select className="input" value={includeTransfers ? "include" : "exclude"} onChange={(e) => setIncludeTransfers(e.target.value === "include")}>
                  <option value="include">{t("tx.include")}</option>
                  <option value="exclude">{t("tx.exclude")}</option>
                </select>
              </label>
            </div>
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="filter-summary">
          <span><span className="n">{total.toLocaleString(lang === "it" ? "it-IT" : "en-US")}</span> {t("tx.matchN")}</span>
          {activeFilters.length ? <span className="dim">· {activeFilters.join(" · ")}</span> : <span className="dim">· {t("tx.noFilters")}</span>}
        </div>

        {state.error ? <div className="state error">{state.error}</div> : null}
        {state.loading && !state.data ? <div className="state">{t("common.loading")}</div> : null}
        {state.data && total === 0 ? (
          <div className="empty">
            <div className="big">{t("tx.emptyBig")}</div>
            <div className="sub">{t("tx.emptySub")}</div>
          </div>
        ) : null}
        {state.data && total > 0 ? (
          <>
            <table>
              <thead>
                <tr>
                  {(
                    [
                      ["date", "common.date", ""],
                      ["description", "common.description", ""],
                      ["category", "common.category", ""],
                      ["amount", "common.amount", "num "],
                      ["account", "common.account", ""],
                    ] as [SortKey, string, string][]
                  ).map(([key, tkey, extra]) => (
                    <th
                      key={key}
                      className={`${extra}sortable`}
                      role="button"
                      tabIndex={0}
                      aria-sort={sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                      onClick={() => toggleSort(key)}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && toggleSort(key)}
                    >
                      {t(tkey)}{caret(key)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((tx) => {
                  const cat = overrides[tx.natural_key] ?? tx.category ?? "";
                  return (
                    <tr key={tx.natural_key}>
                      <td className="mono dim">{dateLabel(tx.value_date)}</td>
                      {/* The row also holds a category <select>, so the whole
                          row cannot be the click target — the description is. */}
                      <td className="desc">
                        <button
                          className="link-cell"
                          title={t("tx.investigate")}
                          onClick={() => setDetailKey(tx.natural_key)}
                        >
                          {tx.description}
                        </button>
                      </td>
                      <td>
                        <span className="cat-cell">
                          <span className="swatch" style={{ background: colorFor(cat) }} />
                          <select className="cat" value={cat} onChange={(e) => recategorize(tx, e.target.value)}>
                            {!categoryCodes.includes(cat) && cat ? <option value={cat}>{catLabel(cat)}</option> : null}
                            {categoryCodes.map((c) => <option key={c} value={c}>{catLabel(c)}</option>)}
                          </select>
                        </span>
                      </td>
                      <td className="num"><span className={`amt ${num(tx.amount) < 0 ? "neg" : "pos"}`}>{money(num(tx.amount))}</span></td>
                      <td className="dim" title={accountLabel(tx.account)}>{accountShort(tx.account)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="pager">
              <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t("common.prev")}</button>
              <span className="count">{offset + 1}–{Math.min(offset + PAGE, total)} {of} {total}</span>
              <button className="btn" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>{t("common.next")}</button>
            </div>
          </>
        ) : null}
      </div>

      {detailKey ? (
        <TransactionDetail naturalKey={detailKey} onClose={() => setDetailKey(null)} />
      ) : null}
    </div>
  );
}
