# Layer 8 — Sealed Secrets controller (C5, platform). Enables committing
# encrypted secrets (SealedSecret CRs) to git; the controller decrypts them into
# real Secrets in-cluster. Used by the app DB-role passwords (C5b).
module "sealed_secrets" {
  source        = "./modules/sealed-secrets"
  chart_version = local.chart_versions.sealed_secrets

  # Pinned sealing key (secret zero) from infra/secrets/ (gitignored). Keeps
  # committed SealedSecrets decryptable across cluster rebuilds.
  tls_crt = file("${path.module}/secrets/sealed-secrets.crt")
  tls_key = file("${path.module}/secrets/sealed-secrets.key")

  depends_on = [module.cilium]
}
