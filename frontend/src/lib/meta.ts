// The vocabulary, fetched rather than restated.
//
// Sources and category codes live in the adapter registry and in
// `categories.yaml`. A copy here drifts the moment either changes.
// `GET /api/v1/meta` is the same source of truth the pipeline reads.

import { useCallback, useMemo } from "react";
import { api } from "../api/client";
import type { MetaResponse } from "../api/types";
import { useLang } from "./lang";
import { useAsync } from "./useAsync";

// Reference data: changes only on deploy or a config edit, and several pages
// want it. One in-flight request, shared — but a REJECTION is never cached.
let cached: Promise<MetaResponse> | null = null;

function load(): Promise<MetaResponse | null> {
  cached ??= api.meta().catch((err) => {
    cached = null;
    throw err;
  });
  return cached.catch(() => null);
}

export interface Meta {
  /** Source ids the backend can actually parse. Empty until loaded. */
  sources: string[];
  /** Backend-supplied human name for a source id. */
  sourceLabel: (id: string | null | undefined) => string;
  /** Category codes, in the order the backend lists them. */
  categoryCodes: string[];
  /** Localized label for a code; falls back to the code itself, never invents one. */
  catLabel: (code: string | null | undefined) => string;
  /** The backend's fallback code; "other" only until /meta answers. */
  defaultCategory: string;
  /** Wealth-not-consumption codes (spend figures exclude these). */
  assetCategories: string[];
  /** category_source vocabulary, resolver-priority order. */
  categorySources: string[];
  /** Calibrated model cut; null until loaded — don't build queues on a guess. */
  modelThreshold: number | null;
  acceptAttr: string;
  maxFileBytes: number;
  maxFilesPerBatch: number;
  loaded: boolean;
}

export function useMeta(): Meta {
  const { lang } = useLang();
  const { data } = useAsync(load, []);

  const labels = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of data?.categories ?? []) m.set(c.code, c.labels[lang] ?? c.labels.en ?? c.code);
    return m;
  }, [data, lang]);

  const catLabel = useCallback(
    (code: string | null | undefined) => (code ? (labels.get(code) ?? code) : "—"),
    [labels],
  );

  const sourceLabels = new Map((data?.sources ?? []).map((s) => [s.id, s.label]));

  return {
    sources: data?.sources.map((s) => s.id) ?? [],
    // Naming lives in the backend so every client renders it identically; the
    // id is the last resort, not a second naming scheme.
    sourceLabel: (id) => (id ? (sourceLabels.get(id) ?? id.replace(/_/g, " ")) : "—"),
    categoryCodes: data?.categories.map((c) => c.code) ?? [],
    catLabel,
    defaultCategory: data?.default_category ?? "other",
    assetCategories: data?.asset_categories ?? [],
    categorySources: data?.category_sources ?? [],
    modelThreshold: data?.model_threshold ?? null,
    acceptAttr: (data?.allowed_extensions ?? []).join(","),
    maxFileBytes: data?.max_file_bytes ?? 0,
    maxFilesPerBatch: data?.max_files_per_batch ?? 50,
    loaded: data != null,
  };
}
