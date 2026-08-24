# config/ — runtime configuration (mounted, not baked)

These YAMLs are **runtime configuration**: on the cluster they are mounted as
the `cashato-config` ConfigMap and read via `CASHATO_CONFIG_DIR`; locally the
CLI/tests read them from this directory. Editing one deploys via Argo with
**no image rebuild** — that is the whole point of keeping them out of the
images.

| File | What it holds | Consumed by |
|------|---------------|-------------|
| `settings.yaml` | `categorization`: the embed model, the single `model_threshold` (0.75) and `knn_k`; plus the transfer window and the upload caps (size/batch/extensions) | services, ML, loader |
| `categories.yaml` | five top-level keys: `default`, the category **codes** with their per-language labels, the thin keyword-rule safety net, `asset_categories` and `seeds` (below) | categorizer, query-api labels, frontend via `/meta` |
| `mcc.yaml` | ISO 18245 merchant-category-code → category map (incl. brand ranges) | categorization stage 1 |
| `banks.yaml` | ABI code → bank display name (statements rarely name their own bank, but all carry an IBAN) | account metadata extraction |

Conventions:

- **Categories are language-neutral codes** (`groceries`, not "Spesa").
  Adding a language = adding a key to every entry in `categories.yaml`; no
  code change anywhere.
- **`asset_categories` decides what is wealth rather than spending**, so it is
  load-bearing, not decorative: every member is excluded from the spend figures.
  `Categorizer` validates it at startup (a code not in `categories` raises, so a
  typo fails the service instead of quietly mis-counting) and `/meta` publishes
  it. It has an SQL twin, `silver.asset_categories`, which is what the gold
  views read; `tests/test_categorize.py` parses the seed out of the baseline
  migration and asserts the two sets are identical, so they cannot drift.
- **`seeds`** maps providers' own category strings to ours. Bootstrap-only —
  never consulted at runtime, by design.
- There is **no `sources.yaml`**: the source registry is code — each parser
  module carries its own detection markers (see CONTRIBUTING, "Add a source").
- Infrastructure endpoints and secrets do **not** belong here: those stay in
  env vars / Secrets. This directory is behavior, not wiring.
