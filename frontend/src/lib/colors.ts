// Categorical color assignment (validated 8-slot palette, fixed
// order, never cycled). Colors are CSS custom properties (--series-1..8, defined
// in styles.css for light+dark) so SVG marks swap with the theme automatically.
//
// Color follows the ENTITY (category code), not its rank: fixed code→slot maps,
// one per co-occurrence family (spend, asset). Codes outside both maps (and the
// aggregated tail) fold to the muted "Other" gray — beyond 8 identities per
// family the correct move is folding, never a 9th generated/cycled hue.

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

// The wealth-destination family (membership truth: /meta asset_categories;
// this map is presentation, and an asset code it doesn't know folds to gray
// like any other). Assets never share a CHART with spend categories — the
// spend views exclude them — so the same validated slots are reused in fixed
// order. Mixed transaction TABLES can show both families; there the label
// beside the swatch carries identity, per the no-color-alone rule.
const ASSET: Record<string, number> = {
  investments: 1,
  crypto: 2,
  pension_fund: 3,
  deposits: 4,
  insurance_savings: 5,
};

const OTHER_COLOR = "var(--cat-other)";

export function colorFor(code: string | null | undefined): string {
  const slot = code ? (PRIMARY[code] ?? ASSET[code]) : undefined;
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
