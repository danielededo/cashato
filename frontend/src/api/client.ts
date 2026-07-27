// Thin typed fetch client. Always RELATIVE `/api/v1` → same-origin via the gateway
// (in-cluster) or the Vite dev proxy (local). Never hardcode a host.

import type {
  AccountsResponse,
  CategoriesMonthlyResponse,
  FeedbackAccepted,
  FilesResponse,
  InvestmentsResponse,
  Lang,
  MetaResponse,
  MonthlyResponse,
  Profile,
  SummaryResponse,
  TransactionDetail,
  TransactionFilters,
  TransactionsResponse,
  TransfersResponse,
  UploadAccepted,
} from "./types";

const BASE = "/api/v1";

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = qs.toString() ? `${BASE}${path}?${qs}` : `${BASE}${path}`;
  const res = await fetch(url);
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`);
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return (body as { detail?: string }).detail ?? JSON.stringify(body);
  } catch {
    return res.statusText;
  }
}

async function postJson<T>(path: string, body: unknown, method = "POST"): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await safeText(res));
  return res.json() as Promise<T>;
}

export interface AdminResult {
  status: string;
  detail?: string;
  [k: string]: unknown;
}

export const api = {
  summary: (lang: Lang) => get<SummaryResponse>("/summary", { lang }),
  monthly: () => get<MonthlyResponse>("/monthly"),
  categoriesMonthly: (lang: Lang) => get<CategoriesMonthlyResponse>("/categories/monthly", { lang }),
  transactions: (f: TransactionFilters) => get<TransactionsResponse>("/transactions", { ...f }),
  transfers: () => get<TransfersResponse>("/transfers"),
  meta: () => get<MetaResponse>("/meta"),
  accounts: () => get<AccountsResponse>("/accounts"),
  investments: (lang: Lang) => get<InvestmentsResponse>("/investments", { lang }),
  transaction: (key: string, lang: Lang) =>
    get<TransactionDetail>(`/transactions/${encodeURIComponent(key)}`, { lang }),
  files: () => get<FilesResponse>("/files"),
  profile: () => get<Profile>("/profile"),

  // admin (destructive / operational)
  reprocessAll: () => postJson<AdminResult>("/admin/reprocess", {}),
  resetData: (scope: "data" | "all", keepFiles = false, confirm = "") =>
    postJson<AdminResult>("/admin/reset", { scope, keep_files: keepFiles, confirm }),
  renameAccount: (id: string, display_name: string | null) =>
    postJson<AdminResult>(`/admin/accounts/${encodeURIComponent(id)}`, { display_name }, "PATCH"),

  async upload(file: File): Promise<UploadAccepted> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/uploads`, { method: "POST", body: form });
    if (!res.ok) throw new ApiError(res.status, await safeText(res));
    return res.json() as Promise<UploadAccepted>;
  },

  async feedback(natural_key: string, category: string, corrected_by?: string): Promise<FeedbackAccepted> {
    const res = await fetch(`${BASE}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ natural_key, category, corrected_by }),
    });
    if (!res.ok) throw new ApiError(res.status, await safeText(res));
    return res.json() as Promise<FeedbackAccepted>;
  },
};
