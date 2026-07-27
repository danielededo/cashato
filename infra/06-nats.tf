# Layer 6 — NATS + JetStream (C4, platform). The messaging backbone for the
# services. The stream (CASHATO) and its WorkQueue retention are created by the
# app at runtime (src/cashato/messaging.py), not here.
module "nats" {
  source        = "./modules/nats"
  chart_version = local.chart_versions.nats

  depends_on = [module.cilium]
}
