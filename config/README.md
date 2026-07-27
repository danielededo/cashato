# config/ — runtime configuration (mounted, not baked)

These YAMLs are **runtime configuration**: on the cluster they are mounted as
the `cashato-config` ConfigMap and read via `CASHATO_CONFIG_DIR`; locally the
CLI/tests read them from this directory. Editing one deploys via Argo with
**no image rebuild** — that is the whole point of keeping them out of the
images.

| File | What it holds | Consumed by |
|------|---------------|-------------|
| `settings.yaml` | categorization thresholds + embed model, transfer window, upload caps (size/batch/extensions) | services, ML, loader |
| `categories.yaml` | the category **codes**, their per-language labels, and the thin keyword-rule safety net | categorizer, query-api labels, frontend via `/meta` |
| `mcc.yaml` | ISO 18245 merchant-category-code → category map (incl. brand ranges) | categorization stage 1 |
| `banks.yaml` | ABI code → bank display name (statements rarely name their own bank, but all carry an IBAN) | account metadata extraction |

Conventions:

- **Categories are language-neutral codes** (`groceries`, not "Spesa").
  Adding a language = adding a key to every entry in `categories.yaml`; no
  code change anywhere.
- There is **no `sources.yaml`**: the source registry is code — each parser
  module carries its own detection markers (see CONTRIBUTING, "Add a source").
- Infrastructure endpoints and secrets do **not** belong here: those stay in
  env vars / Secrets. This directory is behavior, not wiring.
