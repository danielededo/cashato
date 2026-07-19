# CloudNativePG operator (platform). Installs the CRDs and watches all
# namespaces; the Postgres `Cluster` itself is an app manifest delivered by
# Argo CD into the cashato-data namespace (C3).
resource "helm_release" "cnpg" {
  name             = "cnpg"
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cloudnative-pg"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  wait    = true
  timeout = 600
}
