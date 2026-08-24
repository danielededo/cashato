# Security policy

cashato processes **bank statements** on the machine that runs it. Anything that
could make that data leave the machine is the most serious class of bug this
project can have, and is treated as such.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
**[Security → Report a vulnerability](https://github.com/danielededo/cashato/security/advisories/new)**.
Please do **not** open a public issue for something exploitable.

Include what you'd expect: affected component, how to reproduce, and what an
attacker gets. If a statement file is needed to reproduce, **anonymize it
first** — replace names, IBANs and amounts; the parsers care about layout, not
about your real numbers.

This is a personal project maintained by one person: expect a first reply
within a week, not within an hour. Only `main` is supported — there are no
maintained release branches.

## Threat model

The platform is **single-user and local by design**. It assumes:

- the operator is the data owner and has legitimate access to the machine;
- the cluster is not reachable from the internet (kind, no public ingress);
- statement files come from the operator's own banks.

**In scope** — please report:

- any path that sends transaction data, statements or derived aggregates
  off-machine (an unintended outbound call, a log line carrying amounts or
  IBANs, a trace attribute, an object-storage misconfiguration);
- remote or browser-driven access to the APIs: DNS rebinding, CSRF, a Host or
  `Origin` check that can be bypassed, a CORS relaxation;
- SQL injection, or anything that escapes the least-privilege DB roles
  (`query-api` reads gold only; `etl_writer` has no grant on
  `silver.asset_categories`);
- parser-level exploitation: a crafted PDF/CSV/XLSX that achieves more than a
  parse failure (pdfplumber/pandas/openpyxl run on untrusted-by-construction
  input, since a statement is whatever the bank emitted);
- the model-loading path: `joblib` artifacts are pickle-based, so a route that
  loads an attacker-supplied artifact is a code-execution bug.

**Out of scope** — known and deliberate:

- **No authentication on the APIs.** There is one user. Writes are guarded by
  an `Origin` check (`_no_cross_origin_writes`: absent or loopback passes,
  anything else 403s) and by the gateway listener pinned to
  `hostname: localhost`, which 404s a rebound Host at the edge. Multi-user
  (Postgres RLS + OIDC) is future work, not a missing patch.
- **Default bootstrap credentials** in `infra/` for cluster-local services
  (Gitea, MinIO, Postgres in `compose.yaml`). They are documented placeholders
  for a local bring-up; override them in `infra/*.auto.tfvars` (git-ignored) if
  your machine is not trusted-single-user.
- **Committed SealedSecret ciphertexts** under `k8s/`. They are encrypted to
  *one specific cluster's* sealing key, which lives only in `infra/secrets/`
  (git-ignored, never published) — a fork cannot decrypt them and must re-seal
  with `scripts/secret-zero.sh` + `scripts/seal-secrets.sh`.
- Anything that already requires code execution or a shell on the operator's
  machine.
- The Ollama labeling step: it is offline, host-side, and never runs in the
  cluster. It sees transaction descriptions by design — locally.

## What the project promises about your data

- No bank data in the repository: `data/`, `output/`, `models/` are
  git-ignored, and the test fixtures use `MARIO ROSSI` with fake IBANs.
- Money is `Decimal` end to end — a rounding bug is a correctness bug, and we
  want to hear about those too (open a normal issue).
