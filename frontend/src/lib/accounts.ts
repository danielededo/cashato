// Account naming, from data rather than a dictionary.
//
// Bank names, products and joint/individual come from the statements themselves
// (`GET /api/v1/accounts`), so adding a bank never means editing the frontend.
// Everything degrades to the raw id when a document did not disclose enough —
// showing `trade_republic` is honest, inventing "Trade Republic" is not.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Account } from "../api/types";
import { useAsync } from "./useAsync";

// Accounts change on ingest, reset and rename, and several pages want them, so
// one in-flight request is shared rather than refetched per mount.
//
// Two things the cache must NOT do. It must not outlive a change — a rename or
// a reset used to leave every page showing the old names while Manage, which
// refetches independently, disagreed. And it must not memoize a REJECTION: one
// transient error at startup used to pin an empty list for the whole session,
// so accounts rendered as raw ids until a full page reload.
let cached: Promise<Account[]> | null = null;
// Bumped on invalidate() so mounted components refetch instead of sitting on a
// stale promise they already resolved.
let generation = 0;

function load(): Promise<Account[]> {
  cached ??= api.accounts().then(
    (r) => r.accounts,
    (err) => {
      cached = null; // do not cache the failure: the next caller retries
      throw err;
    },
  );
  return cached;
}

/** Drop the cached accounts. Call after anything that changes them. */
export function invalidateAccounts(): void {
  cached = null;
  generation += 1;
  for (const fn of listeners) fn();
}

const listeners = new Set<() => void>();

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
  // Re-subscribe on invalidation so every mounted page picks up new names,
  // rather than only the one that triggered the change.
  const [gen, setGen] = useState(generation);
  useEffect(() => {
    const fn = () => setGen(generation);
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);

  // A failed fetch is not fatal here: naming degrades to raw ids.
  const { data } = useAsync(() => load().catch(() => [] as Account[]), [gen]);
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

    // Fallback when nothing described the account. Title-cased so it sits
    // beside real bank names without looking like a different kind of thing:
    // the UI used to show "Trade Republic Bank" in one place and
    // "trade republic" in another, purely by whether a PDF had been ingested.
    // Still visibly derived from the id — never an invented bank name.
    const raw = (id: string | null | undefined) =>
      id
        ? id
            .replace(/_/g, " ")
            .replace(/\b\p{L}/gu, (c) => c.toUpperCase())
        : "—";

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
