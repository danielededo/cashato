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
