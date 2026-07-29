// TS mirrors of the query-api / ingest-api response models (src/cashato/services/*).
// `category` is the stable language-neutral code; `category_label` is localized.

// Every money field is `Money` (an exact decimal STRING on the wire — see the
// note at Money below); convert deliberately with num().
export interface CategoryTotal {
  category: string;
  category_label: string;
  n_movements: number;
  income: Money | null;
  expense: Money | null;
  net: Money | null;
}
export interface SummaryResponse {
  lang: string;
  categories: CategoryTotal[];
}

export interface MonthRow {
  month: string; // ISO date (first of month)
  income: Money | null;
  expense: Money | null;
  net: Money | null;
}
export interface MonthlyResponse {
  months: MonthRow[];
}

export interface CategoryMonthRow {
  month: string;
  category: string;
  category_label: string;
  n_movements: number;
  total: Money | null;
}
export interface CategoriesMonthlyResponse {
  lang: string;
  rows: CategoryMonthRow[];
}

export interface TransactionRow {
  id: number;
  value_date: string;
  booking_date: string;
  description: string;
  amount: Money; // signed: negative = outflow
  currency: string;
  account: string;
  source: string;
  category: string | null;
  category_label: string;
  category_source: string | null;
  category_confidence: number | null;
  transfer_group: string | null;
  natural_key: string;
  /** Counterparty extracted from the description, when one exists. */
  merchant: string | null;
  /** "HH:MM:SS" when the statement text carries a time of day (POS/ATM). */
  purchase_time: string | null;
}
export interface TransactionsResponse {
  lang: string;
  total: number;
  /** Sums over ALL matching rows, not just the page; null when nothing matches. */
  sum_income: Money | null;
  /** Signed (<= 0). */
  sum_expense: Money | null;
  sum_net: Money | null;
  limit: number;
  offset: number;
  transactions: TransactionRow[];
}

export interface MerchantRow {
  merchant: string;
  n_movements: number;
  /** Net outflow at this merchant (refunds netted), positive. */
  total_spent: Money;
  avg_spent: Money;
  last_date: string;
  category: string | null;
  category_label: string;
}
export interface MerchantsResponse {
  lang: string;
  n_merchants: number;
  merchants: MerchantRow[];
}

export interface TransferPair {
  transfer_group: string;
  value_date: string;
  amount: number; // absolute
  from_account: string | null;
  to_account: string | null;
}
export interface TransfersResponse {
  n_pairs: number;
  total_volume: number;
  transfers: TransferPair[];
}

export interface ReconciliationInterval {
  account: string;
  from_date: string;
  to_date: string;
  from_balance: string;
  to_balance: string;
  expected_delta: string;
  actual_delta: string;
  discrepancy: string;
  n_movements: number;
}

export interface ReconciliationResponse {
  n_intervals: number;
  n_mismatched: number;
  intervals: ReconciliationInterval[];
}

export interface CoverageHole {
  from_date: string;
  to_date: string;
  days: number;
}
export interface CoverageSource {
  source: string;
  accounts: string[];
  n_movements: number;
  n_anchors: number;
  covered_from: string | null;
  covered_until: string | null;
  anchor_cadence_days: number | null;
  stale_days: number;
  /** Behind schedule given the source's own anchor cadence. */
  stale: boolean;
  /** Uncovered windows — a missing statement OR a quiet period; hints, not verdicts. */
  holes: CoverageHole[];
}
export interface CoverageResponse {
  today: string;
  n_stale: number;
  n_holes: number;
  sources: CoverageSource[]; // worst first
}

export interface RecurringItem {
  description: string;
  category: string | null;
  category_label: string;
  accounts: string[];
  cadence: "weekly" | "monthly" | "bimonthly" | "quarterly" | "semiannual" | "yearly";
  n_occurrences: number;
  first_date: string;
  last_date: string;
  amount: Money; // signed median per occurrence
  amount_min: Money;
  amount_max: Money;
  monthly_equivalent: Money; // signed, normalized to one month
  regularity: number;
  /** Judged against the newest data, not today's date. */
  active: boolean;
  next_expected: string | null;
}
export interface RecurringResponse {
  lang: string;
  horizon: string | null;
  n_active: number;
  monthly_expense: Money; // signed (<= 0), consumption only
  monthly_income: Money;
  items: RecurringItem[];
}

export interface BalanceMonthRow {
  month: string; // ISO date (first of month)
  account: string;
  currency: string;
  balance: Money;
  /** Date of the anchor the balance was carried from — the figure's age. */
  as_of: string;
}
export interface AccountBalance {
  account: string;
  currency: string;
  balance: Money;
  as_of: string;
}
export interface WealthResponse {
  months: BalanceMonthRow[];
  accounts: AccountBalance[];
  total_liquid: Money;
  /** The stalest figure inside the total: it is only as fresh as this. */
  oldest_as_of: string | null;
}

export interface UploadAccepted {
  status: string;
  filename: string;
  stored_as: string;
  source: string | null;
}
export interface RawFile {
  source: string;
  filename: string;
  status: string;
  rows_total: number;
  rows_new: number;
  rows_duplicate: number;
  error: string | null;
  uploaded_at: string;
  /** Only statement PDFs name the holder; exports legitimately leave this null. */
  account_holder: string | null;
}
export interface FilesResponse {
  files: RawFile[];
}
/** An account as the statements describe it. The id is opaque and stable (it is
 *  hashed into the dedup key); the rest is display metadata read off documents. */
export interface Account {
  account_id: string;
  source: string;
  bank_name: string | null;
  product: string | null;
  /** null = the document did not say, which is NOT the same as individual. */
  is_joint: boolean | null;
  currency: string | null;
  iban: string | null;
  display_name_override: string | null;
  display_name: string;
  transactions: number;
  first_movement: string | null;
  last_movement: string | null;
}
export interface AccountsResponse {
  accounts: Account[];
}

/** Who the ingested statements belong to. Every field is nullable: "no PDF
 *  ingested yet" is a normal state, not an error. */
export interface Profile {
  display_name: string | null;
  given_name: string | null;
  source: string | null;
  /** Distinct PEOPLE, compared by name tokens so word order does not split one. */
  people: string[];
  /** More than one person across the files. Legitimate for a joint account. */
  mixed_holders: boolean;
  variants: string[];
}
export interface FeedbackAccepted {
  status: string;
  natural_key: string;
  category: string;
}

export type Lang = "it" | "en";
export type Sign = "income" | "expense";

export interface TransactionFilters {
  lang?: Lang;
  account?: string;
  source?: string;
  category?: string;
  category_source?: string;
  sign?: Sign;
  date_from?: string;
  date_to?: string;
  q?: string;
  merchant?: string;
  min_amount?: number;
  max_amount?: number;
  min_confidence?: number;
  max_confidence?: number;
  include_transfers?: boolean;
  sort?: "date" | "amount" | "description" | "category" | "account";
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

/** Exact decimal as it arrives on the wire.
 *
 *  Money is `Decimal` server-side (the project rule) and Pydantic
 *  serializes that to a JSON STRING, not a number — a JSON number is an IEEE754
 *  double, exactly what the rule exists to avoid. Typed as `string` so every use
 *  is forced through `num()` and the conversion to a JS number is deliberate. */
export type Money = string;

/** A position, aggregated from the trades a source disclosed. */
export interface Holding {
  isin: string | null;
  instrument: string | null;
  asset_class: string | null;
  units: Money;
  /** Cash cost basis: what actually left the account (fees included). */
  invested: Money;
  n_trades: number;
  first_trade: string | null;
  last_trade: string | null;
  /** Last price seen ON A STATEMENT, not a market quote — it ages. */
  last_price: Money | null;
  value_at_last_price: Money | null;
}

export interface InvestmentMonth {
  month: string;
  /** Wealth destination kind: investments, crypto, pension_fund, deposits, … */
  category: string;
  contributed: Money | null;
  returned: Money | null;
  net_invested: Money | null;
  /** Contributions whose instrument the source disclosed. */
  into_known: Money | null;
  /** Contributions with no instrument detail (e.g. transfer to an outside broker). */
  into_unknown: Money | null;
  n_movements: number;
}

/** One destination kind, rolled up. Present only when it has movements. */
export interface WealthKind {
  category: string;
  category_label: string;
  net_invested: Money;
  contributed: Money;
  returned: Money;
  n_movements: number;
  /** Instruments are only knowable where the source discloses them. */
  has_instruments: boolean;
}

export interface InvestmentsResponse {
  holdings: Holding[];
  months: InvestmentMonth[];
  kinds: WealthKind[];
  /** Gross money in; known + unknown add up to exactly this. */
  total_contributed: Money;
  total_returned: Money;
  /** Net of returns. Deliberately distinct from total_contributed. */
  total_invested: Money;
  total_in_known_instruments: Money;
  total_in_unknown: Money;
}

export interface TransferLeg {
  natural_key: string;
  value_date: string;
  account: string;
  amount: Money;
  description: string;
}

/** Everything known about one movement. */
export interface TransactionDetail {
  natural_key: string;
  value_date: string;
  booking_date: string;
  description: string;
  amount: Money;
  currency: string;
  account: string;
  source: string;
  category: string | null;
  category_label: string | null;
  category_source: string | null;
  category_confidence: number | null;
  mcc: string | null;
  native_category: string | null;
  transfer_group: string | null;
  transfer_counterpart: TransferLeg | null;
  file_name: string | null;
  file_uploaded_at: string | null;
  file_sha256: string | null;
  isin: string | null;
  instrument: string | null;
  asset_class: string | null;
  quantity: Money | null;
  unit_price: Money | null;
  side: string | null;
  merchant: string | null;
  purchase_time: string | null;
}

/** The vocabulary the UI builds its selectors from — sources from the adapter
 *  registry, categories from categories.yaml, limits from settings.yaml. */
export interface MetaResponse {
  sources: { id: string; label: string }[];
  categories: { code: string; labels: Record<string, string> }[];
  languages: string[];
  allowed_extensions: string[];
  max_file_bytes: number;
  max_files_per_batch: number;
}
