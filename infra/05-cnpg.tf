# Layer 5 — CloudNativePG operator (platform). The Postgres Cluster + roles
# + migration Job are app manifests synced by Argo CD from k8s/manifests/data.
module "cnpg" {
  source        = "./modules/cnpg"
  chart_version = local.chart_versions.cnpg

  depends_on = [module.cilium]
}
