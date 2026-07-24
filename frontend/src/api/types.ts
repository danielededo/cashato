// TS mirrors of the query-api / ingest-api response models (src/cashato/services/*).
// `category` is the stable language-neutral code; `category_label` is localized.

export interface CategoryTotal {
  category: string;
  category_label: string;
  n_movements: number;
  income: number | null;
  expense: number | null;
  net: number | null;
}
export interface SummaryResponse {
  lang: string;
  categories: CategoryTotal[];
}

export interface MonthRow {
  month: string; // ISO date (first of month)
  income: number | null;
  expense: number | null;
  net: number | null;
  net_excl_investments: number | null;
}
export interface MonthlyResponse {
  months: MonthRow[];
}

export interface CategoryMonthRow {
  month: string;
  category: string;
  category_label: string;
  n_movements: number;
  total: number | null;
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
  amount: number; // signed: negative = outflow
  currency: string;
  account: string;
  source: string;
  category: string | null;
  category_label: string;
  category_source: string | null;
  category_confidence: number | null;
  transfer_group: string | null;
  natural_key: string;
}
export interface TransactionsResponse {
  lang: string;
  total: number;
  limit: number;
  offset: number;
  transactions: TransactionRow[];
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
}
export interface FilesResponse {
  files: RawFile[];
}
export interface FeedbackAccepted {
  status: string;
  natural_key: string;
  category: string;
}

export type Lang = "it" | "en";
export type Sign = "income" | "expense";
export const SOURCES = ["intesa", "revolut", "trade_republic"] as const;

export interface TransactionFilters {
  lang?: Lang;
  account?: string;
  source?: string;
  category?: string;
  sign?: Sign;
  date_from?: string;
  date_to?: string;
  q?: string;
  min_amount?: number;
  max_amount?: number;
  include_transfers?: boolean;
  limit?: number;
  offset?: number;
}
