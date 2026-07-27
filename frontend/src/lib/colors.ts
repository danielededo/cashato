// Categorical color assignment (validated 8-slot palette, fixed
// order, never cycled). Colors are CSS custom properties (--series-1..8, defined
// in styles.css for light+dark) so SVG marks swap with the theme automatically.
//
// Color follows the ENTITY (category code), not its rank: a fixed code→slot map.
// Beyond the 8 primary codes (and for the aggregated tail) → the muted "Other" gray.

const PRIMARY: Record<string, number> = {
  groceries: 1,
  dining: 2,
  transport: 3,
  bills: 4,
  shopping: 5,
  subscriptions: 6,
  health: 7,
  rent: 8,
};

const OTHER_COLOR = "var(--cat-other)";

export function colorFor(code: string | null | undefined): string {
  const slot = code ? PRIMARY[code] : undefined;
  return slot ? `var(--series-${slot})` : OTHER_COLOR;
}

/** Nth colour of the categorical ramp, for series with no category code of
 *  their own (holdings, instruments). Wraps around past the 8th. */
export function seriesColor(i: number): string {
  return `var(--series-${(i % 8) + 1})`;
}

// Two-series semantic colors for income vs expense (status-like, fixed).
export const INCOME_COLOR = "var(--income)";
export const EXPENSE_COLOR = "var(--expense)";
