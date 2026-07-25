import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { dateLabel } from "../lib/format";
import { useAccounts } from "../lib/accounts";
import { useT } from "../lib/i18n";
import { useMeta } from "../lib/meta";
import { useAsync } from "../lib/useAsync";

export function Upload() {
  const { t } = useT();
  const { sourceLabel } = useAccounts();
  const { sources, acceptAttr } = useMeta();
  const [source, setSource] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const files = useAsync(() => api.files(), []);

  // poll the files list so parse status updates as the worker processes them
  const reload = files.reload;
  useEffect(() => {
    const id = setInterval(reload, 5000);
    return () => clearInterval(id);
  }, [reload]);

  async function send(file: File) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.upload(file, source || undefined);
      setMsg({ ok: true, text: `Queued ${r.filename}${r.source ? ` as ${sourceLabel(r.source)}` : ""}. Parsing…` });
      setTimeout(reload, 800);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void send(f);
  }

  return (
    <div className="fade-in">
      <div className="panel">
        <div
          className={`dropzone ${over ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
        >
          {busy ? t("up.uploading") : t("up.drop")}
          <span className="hint">{t("up.hint")}</span>
          <input
            ref={inputRef}
            type="file"
            accept={acceptAttr}
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void send(f);
            }}
          />
        </div>

        {msg ? <div className={`state ${msg.ok ? "" : "error"}`}>{msg.text}</div> : null}

        {/* The source is worked out from the file's CONTENT, so asking up front
            makes the user do the system's job and advertises the supported list
            as if it were a limit. Kept only as a recovery path, folded away. */}
        <details className="override">
          <summary>{t("up.override")}</summary>
          <label className="field" style={{ marginTop: 9 }}>
            {t("up.source")}
            <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">{t("up.autodetect")}</option>
              {sources.map((s) => (
                <option key={s} value={s}>{sourceLabel(s)}</option>
              ))}
            </select>
          </label>
          <p className="hint" style={{ marginTop: 7 }}>{t("up.override.hint")}</p>
        </details>
      </div>

      <div className="panel">
        <div className="panel-head"><h2>{t("up.recent")}</h2></div>
        {files.loading && !files.data ? <div className="state">{t("common.loading")}</div> : null}
        {files.error ? <div className="state error">{files.error}</div> : null}
        {files.data && files.data.files.length === 0 ? (
          <div className="empty">
            <div className="big">{t("up.emptyBig")}</div>
            <div className="sub">{t("up.emptySub")}</div>
          </div>
        ) : null}
        {files.data && files.data.files.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>{t("up.when")}</th>
                <th>{t("common.source")}</th>
                <th>{t("up.file")}</th>
                <th>{t("up.status")}</th>
                <th className="num">{t("up.new")}</th>
                <th className="num">{t("up.dup")}</th>
                <th>{t("up.note")}</th>
              </tr>
            </thead>
            <tbody>
              {files.data.files.map((f, i) => (
                <tr key={`${f.filename}-${i}`}>
                  <td className="mono dim">{dateLabel(f.uploaded_at)}</td>
                  <td>{sourceLabel(f.source)}</td>
                  <td className="desc" title={f.filename}>{f.filename}</td>
                  <td>
                    <span className={`pill ${f.status}`}>{f.status}</span>
                  </td>
                  <td className="num mono">{f.rows_new}</td>
                  <td className="num mono dim">{f.rows_duplicate}</td>
                  <td className="desc dim" title={f.error ?? ""}>{f.error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </div>
  );
}
