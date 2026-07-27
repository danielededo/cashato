# Layer 9 — cert-manager (platform). Prerequisite for KServe: its admission
# webhooks need TLS certs, which cert-manager issues. Kept as its own layer so it
# is reusable if other components later want certs.
module "cert_manager" {
  source        = "./modules/cert-manager"
  chart_version = local.chart_versions.cert_manager

  depends_on = [module.cilium]
}
