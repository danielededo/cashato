import { useState } from "react";
import { api, ApiError } from "../api/client";
import { useT } from "../lib/i18n";
import { invalidateAccounts, useAccounts } from "../lib/accounts";

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
