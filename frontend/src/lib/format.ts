// Formatting helpers. Amounts are EUR (the platform is EUR-only today: the
// parsers drop other currencies at ingest, so one formatter is honest).
//
// Dates and month names follow the UI language. LangProvider pushes it here
// (setFormatLocale) so the ~20 call sites don't each thread a lang param;
// components re-render on a language switch, so they re-call these helpers.

import type { Lang } from "../api/types";

// en-GB, not en-US: day-first dates match what an Italian bank's data reader
// expects even in the English UI; month names come out English either way.
const LOCALES: Record<Lang, string> = { it: "it-IT", en: "en-GB" };

let locale: string = LOCALES.it;
let eur = new Intl.NumberFormat(locale, { style: "currency", currency: "EUR" });
let eur0 = new Intl.NumberFormat(locale, {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

export function setFormatLocale(lang: Lang): void {
  locale = LOCALES[lang];
  eur = new Intl.NumberFormat(locale, { style: "currency", currency: "EUR" });
  eur0 = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  });
}

export function money(n: number | null | undefined): string {
  return n == null ? "—" : eur.format(n);
}

/** Compact figure for chart axes/labels: € 1,2k / € 340. */
export function moneyShort(n: number | null | undefined): string {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1000) return `${n < 0 ? "-" : ""}€ ${(abs / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`;
  return eur0.format(n);
}

/** Short month for dense axes: "Mar '24" / "mar '24". */
export function monthShort(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString(locale, { month: "short" })} '${String(d.getFullYear()).slice(2)}`;
}

export function dateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(locale);
}

/** A local calendar date as `YYYY-MM-DD`, for date filters sent to the API.
 *
 *  NEVER use `toISOString().slice(0,10)` for this. It converts to UTC first, so
 *  a Date built from local components lands on the previous day anywhere east
 *  of Greenwich: in Europe/Rome, `new Date(2026, 5, 30)` is 2026-06-30 00:00
 *  CEST = 2026-06-29 22:00 UTC, and the filter silently excludes the last day
 *  of the range it was meant to include. */
export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Parse an exact decimal from the wire into a JS number.
 *
 *  Money arrives as a STRING (see `Money` in api/types): the server keeps it as
 *  Decimal and Pydantic will not degrade that to a JSON double. Converting to a
 *  JS number is unavoidable for charts and arithmetic, but making it an explicit
 *  call keeps it at the boundary — the alternative is silent coercion, and
 *  `"8951.0000" / 2` is NaN.
 */
export function num(v: string | number | null | undefined): number {
  if (v == null) return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}
