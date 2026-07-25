// Account naming, from data rather than a dictionary.
//
// Bank names, products and joint/individual come from the statements themselves
// (`GET /api/v1/accounts`), so adding a bank never means editing the frontend.
// Everything degrades to the raw id when a document did not disclose enough —
// showing `trade_republic` is honest, inventing "Trade Republic" is not.

import { useMemo } from "react";
import { api } from "../api/client";
import type { Account } from "../api/types";
import { useAsync } from "./useAsync";

// Accounts change only on ingest, and several pages want them. One in-flight
// request is shared rather than refetched per mount.
let cached: Promise<Account[]> | null = null;

function load(): Promise<Account[]> {
  cached ??= api.accounts().then(
    (r) => r.accounts,
    // Decorative: a failure just means we fall back to raw ids everywhere.
    () => [],
  );
  return cached;
}

export interface AccountNaming {
  accounts: Account[];
  /** Full label for one account id, e.g. "Revolut Bank UAB · Joint Account (Joint)". */
  accountLabel: (id: string | null | undefined) => string;
  /** Compact label for dense places (tables): bank name, plus a Joint marker. */
  accountShort: (id: string | null | undefined) => string;
  /** Bank behind a source id, when its accounts agree on one. */
  sourceLabel: (id: string | null | undefined) => string;
  isJoint: (id: string | null | undefined) => boolean;
}

export function useAccounts(): AccountNaming {
  const { data } = useAsync(load, []);
  const accounts = data ?? [];

  return useMemo(() => {
    const byId = new Map(accounts.map((a) => [a.account_id, a]));

    // A source maps to one bank only if every account under it says the same;
    // otherwise there is no honest single label and the raw id stands.
    const banksBySource = new Map<string, Set<string>>();
    for (const a of accounts) {
      if (!a.bank_name) continue;
      const s = banksBySource.get(a.source) ?? new Set<string>();
      s.add(a.bank_name);
      banksBySource.set(a.source, s);
    }

    const raw = (id: string | null | undefined) => (id ? id.replace(/_/g, " ") : "—");

    return {
      accounts,
      accountLabel: (id) => byId.get(id ?? "")?.display_name || raw(id),
      accountShort: (id) => {
        const a = byId.get(id ?? "");
        if (!a?.bank_name) return raw(id);
        return a.is_joint ? `${a.bank_name} (Joint)` : a.bank_name;
      },
      sourceLabel: (id) => {
        const banks = banksBySource.get(id ?? "");
        return banks?.size === 1 ? [...banks][0] : raw(id);
      },
      isJoint: (id) => byId.get(id ?? "")?.is_joint === true,
    };
  }, [accounts]);
}
