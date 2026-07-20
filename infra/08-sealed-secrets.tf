# Layer 8 — Sealed Secrets controller (C5, platform). Enables committing
# encrypted secrets (SealedSecret CRs) to git; the controller decrypts them into
# real Secrets in-cluster. Used by the app DB-role passwords (C5b).
module "sealed_secrets" {
  source        = "./modules/sealed-secrets"
  chart_version = local.chart_versions.sealed_secrets

  depends_on = [module.cilium]
}
