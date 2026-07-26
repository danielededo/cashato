// One movement, in full — a side drawer over the list.
//
// The list shows what you scan; this shows what you investigate: how the
// category was decided and how sure that was, the raw provider signals behind
// it, which uploaded file the row came from, the other leg if it is an internal
// transfer, and the instrument if it was a trade. Most of this already existed
// in the database and was simply never surfaced.

import { useEffect } from "react";
import { api } from "../api/client";
import { dateLabel, money, num } from "../lib/format";
import { useAccounts } from "../lib/accounts";
import { useT } from "../lib/i18n";
import { useAsync } from "../lib/useAsync";

function Field({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="det-field">
      <div className="det-label">{label}</div>
      <div className={mono ? "det-value mono" : "det-value"}>{children}</div>
    </div>
  );
}

export function TransactionDetail({
  naturalKey,
  onClose,
}: {
  naturalKey: string;
  onClose: () => void;
}) {
  const { t, lang } = useT();
  const { accountLabel } = useAccounts();
  const detail = useAsync(() => api.transaction(naturalKey, lang), [naturalKey, lang]);

  // Escape closes: a drawer that traps you is worse than no drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const d = detail.data;
  const conf = d?.category_confidence;

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} role="presentation" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={t("det.title")}>
        <div className="drawer-head">
          <h2>{t("det.title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("det.close")}>✕</button>
        </div>

        {detail.loading && !d ? <div className="state">{t("common.loading")}</div> : null}
        {detail.error ? <div className="state error">{detail.error}</div> : null}

        {d ? (
          <div className="drawer-body">
            <div className="det-amount">
              <span className={`amt ${num(d.amount) < 0 ? "neg" : "pos"}`}>{money(num(d.amount))}</span>
              <span className="dim">{d.currency}</span>
            </div>
            <p className="det-desc">{d.description}</p>

            <div className="det-grid">
              <Field label={t("common.date")}>{dateLabel(d.value_date)}</Field>
              {/* Only worth showing when they differ — otherwise it is noise. */}
              {d.booking_date !== d.value_date ? (
                <Field label={t("det.booking")}>{dateLabel(d.booking_date)}</Field>
              ) : null}
              <Field label={t("common.account")}>{accountLabel(d.account)}</Field>
              <Field label={t("common.category")}>{d.category_label ?? d.category ?? "—"}</Field>
            </div>

            <div className="det-section">
              <h3>{t("det.howCategorized")}</h3>
              <div className="det-grid">
                <Field label={t("det.assignedBy")}>
                  {d.category_source ? t(`src.${d.category_source}`) : "—"}
                </Field>
                <Field label={t("det.confidence")}>
                  {conf != null ? `${Math.round(conf * 100)}%` : "—"}
                </Field>
                {/* The provider's own category is bootstrap-only and never used
                    at runtime; showing it explains surprising results. */}
                {d.native_category ? (
                  <Field label={t("det.native")}>{d.native_category}</Field>
                ) : null}
                {d.mcc ? <Field label="MCC" mono>{d.mcc}</Field> : null}
              </div>
              {d.category_source === "model" ? (
                <p className="det-note dim">{t("det.modelNote")}</p>
              ) : null}
            </div>

            {d.isin || d.instrument ? (
              <div className="det-section">
                <h3>{t("det.instrument")}</h3>
                <div className="det-grid">
                  <Field label={t("inv.instrument")}>{d.instrument ?? "—"}</Field>
                  <Field label="ISIN" mono>{d.isin ?? "—"}</Field>
                  <Field label={t("inv.units")} mono>{d.quantity ?? "—"}</Field>
                  <Field label={t("det.unitPrice")} mono>
                    {d.unit_price != null ? money(num(d.unit_price)) : "—"}
                  </Field>
                </div>
              </div>
            ) : null}

            {d.transfer_counterpart ? (
              <div className="det-section">
                <h3>{t("det.transfer")}</h3>
                <p className="det-note">
                  {t("det.transferNote")} <strong>{accountLabel(d.transfer_counterpart.account)}</strong>{" "}
                  ({money(num(d.transfer_counterpart.amount))}, {dateLabel(d.transfer_counterpart.value_date)})
                </p>
              </div>
            ) : null}

            <div className="det-section">
              <h3>{t("det.provenance")}</h3>
              <div className="det-grid">
                <Field label={t("det.file")}>{d.file_name ?? "—"}</Field>
                <Field label={t("det.uploaded")}>
                  {d.file_uploaded_at ? dateLabel(d.file_uploaded_at) : "—"}
                </Field>
              </div>
              <Field label={t("det.key")} mono>
                <span className="det-key">{d.natural_key}</span>
              </Field>
              <p className="det-note dim">{t("det.keyNote")}</p>
            </div>
          </div>
        ) : null}
      </aside>
    </>
  );
}
