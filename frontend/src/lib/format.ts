// Formatting helpers. Amounts are EUR (the platform is EUR-only today).

const eur = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });

export function money(n: number | null | undefined): string {
  return n == null ? "—" : eur.format(n);
}

export function monthLabel(iso: string): string {
  // "2024-03-01" -> "Mar 2024"
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

export function dateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("it-IT");
}
