// Formatting helpers. Amounts are EUR (the platform is EUR-only today).

const eur = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });
const eur0 = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

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

export function monthLabel(iso: string): string {
  // "2024-03-01" -> "Mar 2024"
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

/** Short month for dense axes: "Mar '24". */
export function monthShort(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString("en-US", { month: "short" })} '${String(d.getFullYear()).slice(2)}`;
}

export function dateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("it-IT");
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
 *  `"8951.0000" / 2` is NaN, which is how the whole Wealth page broke.
 */
export function num(v: string | number | null | undefined): number {
  if (v == null) return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}
