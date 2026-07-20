# scripts/ — tracked operational procedures

Every manual/author-time operation the platform needs is captured here as a
committed script, so nothing is undocumented "tribal knowledge". Scripts read the
**secret zero** (out-of-band material in `infra/secrets/`, gitignored) and produce
reproducible, committable artifacts.

The rule: **git + `infra/secrets/` (backed up) must be enough to rebuild
everything.** No ad-hoc commands.

## Secrets

| Script | What it does | When to run |
|--------|--------------|-------------|
| `secret-zero.sh` | Generate the pinned Sealed Secrets keypair + DB role passwords into `infra/secrets/` (idempotent, never clobbers). The single root of trust. | Once per environment (or restore the backup instead). |
| `seal-secrets.sh` | Regenerate the committed `SealedSecret` YAMLs from `infra/secrets/`, offline against the cert file. | After a password change, or on a fresh checkout with the restored secret-zero. |

**Flow:** `secret-zero.sh` → `tofu apply` (installs the pinned key into the
controller) → `seal-secrets.sh` → commit the SealedSecrets → Argo applies them →
the controller decrypts them in-cluster.

`infra/secrets/` holds: `sealed-secrets.crt` / `.key` (pinned sealing key) and
`role-passwords.env`. It is gitignored — **back it up out-of-band**; losing it
means re-sealing every secret.

## Still to be captured as scripts (tracked TODO)

These manual procedures are documented in the memory/rebuild recipe and will move
here as we formalize them:

- **cluster rebuild** (kind delete → state reset → `tofu apply` → `kind load`
  images → re-seed Gitea repo → `git push`).
- **workstation tools** install (`kubeseal`, `kind` to `~/.local/bin`).
- **image build + `kind load`** (`cashato/svc:dev`, `cashato/migrate:dev`) — until
  Tekton/Harbor (C7) replace it.
