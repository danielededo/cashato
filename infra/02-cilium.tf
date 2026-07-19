# Layer 1 — Cilium as the cluster CNI (default CNI disabled in layer 0),
# with Hubble for east-west observability.
module "cilium" {
  source        = "./modules/cilium"
  chart_version = local.chart_versions.cilium

  depends_on = [kind_cluster.default]
}
