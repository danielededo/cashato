// Period selector shared by the dashboard. The window is measured relative to the
// LATEST month present in the data (not "today"): statements can be historical, so
// "last 6 months" means the 6 most recent months that actually have data.

export const PERIODS = [
  { key: "3m", label: "3M", months: 3 },
  { key: "6m", label: "6M", months: 6 },
  { key: "12m", label: "12M", months: 12 },
  { key: "all", label: "All", months: null },
] as const;

export type PeriodKey = (typeof PERIODS)[number]["key"];

export function monthsFor(key: PeriodKey): number | null {
  return PERIODS.find((p) => p.key === key)?.months ?? null;
}

/** Current window plus the equal-length window immediately before it (for "vs
 *  previous period" comparison). previous is empty when there isn't enough history
 *  or when the window is "all". */
export function splitWindows(
  allMonths: Iterable<string>,
  months: number | null,
): { current: string[]; previous: string[] } {
  const uniq = [...new Set(allMonths)].sort();
  if (months == null) return { current: uniq, previous: [] };
  const current = uniq.slice(Math.max(0, uniq.length - months));
  const prevEnd = uniq.length - current.length;
  const previous = uniq.slice(Math.max(0, prevEnd - months), prevEnd);
  return { current, previous };
}
