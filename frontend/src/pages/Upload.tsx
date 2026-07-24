import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { SOURCES } from "../api/types";
import { dateLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Upload() {
  const [source, setSource] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const files = useAsync(() => api.files(), []);

  // poll the files list so parse status updates as the worker processes
  useEffect(() => {
    const id = setInterval(() => files.reload(), 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function send(file: File) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.upload(file, source || undefined);
      setMsg({ ok: true, text: `Queued: ${r.filename}${r.source ? ` (${r.source})` : ""}` });
      setTimeout(() => files.reload(), 800);
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
    <>
      <h1>Upload</h1>

      <div className="card">
        <div className="filters" style={{ marginBottom: 12 }}>
          <label className="field">source (optional — else auto-detected)
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">auto-detect</option>
              {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>

        <div
          className={`dropzone ${over ? "over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
        >
          {busy ? "Uploading…" : "Drop a statement here (PDF / CSV / XLSX) or click to choose"}
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.csv,.xlsx,.xls"
            hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(f); }}
          />
        </div>

        {msg && <div className={`state ${msg.ok ? "" : "error"}`}>{msg.text}</div>}
      </div>

      <div className="card">
        <h2>Recent files</h2>
        {files.loading && !files.data && <div className="state">Loading…</div>}
        {files.error && <div className="state error">{files.error}</div>}
        {files.data && (
          <table>
            <thead>
              <tr>
                <th>When</th><th>Source</th><th>File</th><th>Status</th>
                <th className="num">New</th><th className="num">Dup</th><th>Error</th>
              </tr>
            </thead>
            <tbody>
              {files.data.files.map((f, i) => (
                <tr key={`${f.filename}-${i}`}>
                  <td>{dateLabel(f.uploaded_at)}</td>
                  <td>{f.source}</td>
                  <td className="desc" title={f.filename}>{f.filename}</td>
                  <td><span className={`pill ${f.status}`}>{f.status}</span></td>
                  <td className="num">{f.rows_new}</td>
                  <td className="num">{f.rows_duplicate}</td>
                  <td className="desc" title={f.error ?? ""}>{f.error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
