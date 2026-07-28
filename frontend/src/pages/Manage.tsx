import { useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import { dateLabel, money, num } from "../lib/format";
import { useT } from "../lib/i18n";
import { invalidateAccounts, useAccounts } from "../lib/accounts";
import { useMeta } from "../lib/meta";
import { useAsync } from "../lib/useAsync";

type Busy = null | "reprocess" | "reset";

export function Manage() {
  const { t } = useT();
  const [busy, setBusy] = useState<Busy>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [scope, setScope] = useState<"data" | "all">("data");
  const [keepFiles, setKeepFiles] = useState(false);
  const [confirm, setConfirm] = useState("");

  async function run(kind: Exclude<Busy, null>, fn: () => Promise<{ status: string; detail?: string }>) {
    setBusy(kind);
    setMsg(null);
    try {
      const r = await fn();
      // reset/reprocess change which accounts exist and what they are called
      invalidateAccounts();
      setConfirm(""); // disarm the destructive button after success
      setMsg({ ok: true, text: r.detail ?? r.status });
    } catch (e) {
      const notDeployed = e instanceof ApiError && (e.status === 404 || e.status === 405);
      setMsg({ ok: false, text: notDeployed ? t("mng.notDeployed") : e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="fade-in">
      <HealthPanel />
      <CoveragePanel />
      <AccountsPanel />

      {/* reprocess */}
      <div className="panel">
        <div className="panel-head"><h2>{t("mng.reprocess")}</h2><span className="hint">{t("mng.reprocess.hint")}</span></div>
        <div className="toolbar">
          <button className="btn" disabled={busy !== null} onClick={() => run("reprocess", api.reprocessAll)}>
            {busy === "reprocess" ? "…" : t("mng.reprocessBtn")}
          </button>
        </div>
      </div>

      {/* retrain (guided, offline) */}
      <div className="panel">
        <div className="panel-head"><h2>{t("mng.retrain")}</h2><span className="hint">{t("mng.retrain.hint")}</span></div>
        <ol className="steps">
          <li>Label the long tail with the host LLM → <code>gold.training_labels</code>:
            <pre><code>ollama serve  # host GPU
python -m cashato.ml.label --limit 2000</code></pre>
          </li>
          <li>Train the embedding kNN and register it in MLflow:
            <pre><code>python -m cashato.ml.train</code></pre>
          </li>
          <li>Recategorize existing transactions with the new model:
            <pre><code>python -m cashato.ml.recategorize</code></pre>
          </li>
          <li>The categorizer service picks up the new MLflow model version automatically.</li>
        </ol>
        <p className="footnote">
          Runs locally on the host GPU — not in-cluster. Exact commands may differ; see the ML docs.
        </p>
      </div>

      {/* reset (destructive) */}
      <div className="panel">
        <div className="panel-head"><h2>{t("mng.reset")}</h2><span className="hint">{t("mng.reset.hint")}</span></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
            <input type="radio" name="scope" checked={scope === "data"} onChange={() => setScope("data")} />
            {t("mng.reset.keep")}
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
            <input type="radio" name="scope" checked={scope === "all"} onChange={() => setScope("all")} />
            {t("mng.reset.all")}
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
            <input type="checkbox" checked={keepFiles} onChange={(e) => setKeepFiles(e.target.checked)} />
            {t("mng.reset.keepFiles")}
          </label>
          <div className="toolbar">
            <input
              className="input"
              placeholder="RESET"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              style={{ width: 120 }}
            />
            <button
              className="btn"
              style={{ borderColor: "var(--expense)", color: "var(--expense)" }}
              disabled={busy !== null || confirm !== "RESET"}
              onClick={() => run("reset", () => api.resetData(scope, keepFiles, confirm))}
            >
              {busy === "reset" ? "…" : t("mng.resetBtn")}
            </button>
          </div>
        </div>
      </div>

      {msg ? <div className={`panel state ${msg.ok ? "" : "error"}`}>{msg.text}</div> : null}
    </div>
  );
}

const MISMATCH_ROWS = 8;

/** Do the imported movements add up to the balances the statements declare?
 *
 *  Read-only rollup of gold.v_reconciliation: one row per account, plus the
 *  mismatched intervals when there are any. A discrepancy localizes a data
 *  problem to one account and date range — which is also why the panel lives
 *  here next to Reprocess, the button that usually fixes it. */
function HealthPanel() {
  const { t } = useT();
  const { accountShort, accountLabel } = useAccounts();
  const rec = useAsync(() => api.reconciliation(), []);
  const d = rec.data;

  const accounts = useMemo(() => {
    if (!d) return [];
    const by = new Map<string, { n: number; bad: number; disc: number; lastDate: string; lastBal: number }>();
    for (const iv of d.intervals) {
      const row = by.get(iv.account) ?? { n: 0, bad: 0, disc: 0, lastDate: "", lastBal: 0 };
      row.n += 1;
      const disc = num(iv.discrepancy);
      if (disc !== 0) { row.bad += 1; row.disc += disc; }
      if (iv.to_date > row.lastDate) { row.lastDate = iv.to_date; row.lastBal = num(iv.to_balance); }
      by.set(iv.account, row);
    }
    return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [d]);

  const mismatched = useMemo(
    () => (d ? d.intervals.filter((iv) => num(iv.discrepancy) !== 0) : []),
    [d],
  );

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>{t("mng.health")}</h2>
        <span className="hint">{t("mng.health.hint")}</span>
      </div>
      {rec.loading && !d ? <div className="state">{t("common.loading")}</div> : null}
      {rec.error ? <div className="state error">{rec.error}</div> : null}
      {d && d.n_intervals === 0 ? <div className="state">{t("mng.health.none")}</div> : null}
      {d && d.n_intervals > 0 ? (
        <div>
          <p className={`health-verdict ${d.n_mismatched ? "neg" : "pos"}`}>
            {d.n_mismatched
              ? t("mng.health.bad", { bad: d.n_mismatched, n: d.n_intervals })
              : t("mng.health.ok", { n: d.n_intervals })}
          </p>
          <table>
            <thead>
              <tr>
                <th>{t("mng.health.account")}</th>
                <th className="num">{t("mng.health.intervals")}</th>
                <th className="num">{t("mng.health.mismatched")}</th>
                <th className="num">{t("mng.health.netDisc")}</th>
                <th className="num">{t("mng.health.lastBalance")}</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map(([acct, r]) => (
                <tr key={acct}>
                  <td className="dim" title={accountLabel(acct)}>{accountShort(acct)}</td>
                  <td className="num mono">{r.n}</td>
                  <td className="num"><span className={`amt ${r.bad ? "neg" : ""}`}>{r.bad || "—"}</span></td>
                  <td className="num"><span className={`amt ${r.bad ? "neg" : ""}`}>{r.bad ? money(r.disc) : "—"}</span></td>
                  <td className="num"><span className="amt">{money(r.lastBal)}</span>{" "}<span className="dim">{dateLabel(r.lastDate)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {mismatched.length > 0 ? (
            <div style={{ marginTop: 14 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("mng.health.account")}</th>
                    <th>{t("mng.health.period")}</th>
                    <th className="num">{t("mng.health.expected")}</th>
                    <th className="num">{t("mng.health.actual")}</th>
                    <th className="num">{t("mng.health.diff")}</th>
                  </tr>
                </thead>
                <tbody>
                  {mismatched.slice(0, MISMATCH_ROWS).map((iv) => (
                    <tr key={`${iv.account}${iv.from_date}`}>
                      <td className="dim">{accountShort(iv.account)}</td>
                      <td className="mono dim">{dateLabel(iv.from_date)} → {dateLabel(iv.to_date)}</td>
                      <td className="num"><span className="amt">{money(num(iv.expected_delta))}</span></td>
                      <td className="num"><span className="amt">{money(num(iv.actual_delta))}</span></td>
                      <td className="num"><span className="amt neg">{money(num(iv.discrepancy))}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {mismatched.length > MISMATCH_ROWS ? (
                <p className="footnote" style={{ paddingTop: 8 }}>
                  {t("mng.health.more", { n: mismatched.length - MISMATCH_ROWS })}
                </p>
              ) : null}
              <p className="footnote" style={{ paddingTop: 8 }}>{t("mng.health.boundaryNote")}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** How far the uploaded statements reach, per SOURCE — the unit a file is
 *  uploaded for, so a dormant crypto pocket never looks "behind" while the
 *  cash account of the same export is fresh. Uploading the missing file is
 *  the fix, which is why this sits next to Upload/Reprocess. */
function CoveragePanel() {
  const { t } = useT();
  const { sourceLabel } = useMeta();
  const cov = useAsync(() => api.coverage(), []);
  const d = cov.data;

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>{t("cov.title")}</h2>
        <span className="hint">{t("cov.hint")}</span>
      </div>
      {cov.loading && !d ? <div className="state">{t("common.loading")}</div> : null}
      {cov.error ? <div className="state error">{cov.error}</div> : null}
      {d && d.sources.length === 0 ? <div className="state">{t("cov.none")}</div> : null}
      {d && d.sources.length > 0 ? (
        <div>
          <p className={`health-verdict ${d.n_stale ? "neg" : "pos"}`}>
            {d.n_stale ? t("cov.bad", { n: d.n_stale }) : t("cov.ok")}
          </p>
          <table>
            <thead>
              <tr>
                <th>{t("cov.source")}</th>
                <th>{t("cov.range")}</th>
                <th className="num">{t("cov.behind")}</th>
                <th>{t("cov.status")}</th>
              </tr>
            </thead>
            <tbody>
              {d.sources.map((s) => (
                <tr key={s.source}>
                  <td>
                    {sourceLabel(s.source)}{" "}
                    <span className="dim">
                      · {s.accounts.length} {t("cov.accounts")}
                    </span>
                  </td>
                  <td className="mono dim">
                    {s.covered_from && s.covered_until
                      ? `${dateLabel(s.covered_from)} → ${dateLabel(s.covered_until)}`
                      : "—"}
                  </td>
                  <td className="num mono">{s.stale_days} {t("cov.days")}</td>
                  <td>
                    {s.stale ? (
                      <span className="amt neg">{t("cov.stale")}</span>
                    ) : (
                      <span className="dim">{t("cov.fresh")}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.n_holes > 0 ? (
            <div style={{ marginTop: 14 }}>
              {d.sources.flatMap((s) =>
                s.holes.map((h) => (
                  <p className="footnote" key={`${s.source}${h.from_date}`}>
                    {t("cov.hole", {
                      src: sourceLabel(s.source),
                      from: dateLabel(h.from_date),
                      to: dateLabel(h.to_date),
                      n: h.days,
                    })}
                  </p>
                )),
              )}
              <p className="footnote">{t("cov.holeNote")}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Accounts as the statements describe them, with a rename override.
 *
 *  Only the override is editable: bank, product and holding modality are
 *  evidence read off the documents, and clearing the name restores the derived
 *  one rather than losing it. Accounts seen only in transactions have no
 *  metadata row, so there is nothing to attach an override to — the API 404s
 *  and we say so instead of pretending the field is editable. */
function AccountsPanel() {
  const { t } = useT();
  // The SHARED accounts cache, not a private fetch: a reset truncates
  // silver.accounts.
  const { accounts: rows } = useAccounts();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function save(id: string, name: string | null) {
    setSaving(id);
    setErr(null);
    try {
      await api.renameAccount(id, name);
      setDraft((d) => ({ ...d, [id]: "" }));
      // Every other page reads names from the shared cache; without this they
      // keep showing the old one while this panel shows the new.
      invalidateAccounts();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>{t("acc.title")}</h2>
        <span className="hint">{t("acc.hint")}</span>
      </div>
      {err ? <div className="state error">{err}</div> : null}
      {rows.length === 0 ? <div className="state">{t("acc.none")}</div> : null}
      {rows.map((a) => {
        const value = draft[a.account_id] ?? a.display_name_override ?? "";
        const described = a.bank_name !== null || a.product !== null;
        return (
          <div key={a.account_id} className="acct-row">
            {/* Display name first: the immutable parser id (hashed into
                natural_key) is reference data, not the row's identity. */}
            <div className="acct-id">
              <span>{a.display_name}</span>
              <span className="dim">
                <span className="mono">{a.account_id}</span>
                {" · "}
                {a.transactions} {t("acc.movements")}
                {a.is_joint ? ` · ${t("common.joint")}` : ""}
                {described ? "" : ` · ${t("acc.noMeta")}`}
              </span>
            </div>
            <input
              className="input"
              value={value}
              placeholder={a.display_name}
              disabled={!described || saving === a.account_id}
              onChange={(e) => setDraft((d) => ({ ...d, [a.account_id]: e.target.value }))}
            />
            <button
              className="btn"
              disabled={!described || saving === a.account_id || !value.trim()}
              onClick={() => void save(a.account_id, value)}
            >
              {t("acc.rename")}
            </button>
            <button
              className="btn"
              disabled={!described || saving === a.account_id || !a.display_name_override}
              onClick={() => void save(a.account_id, null)}
            >
              {t("acc.reset")}
            </button>
          </div>
        );
      })}
    </div>
  );
}
