# Layer 7 — Envoy Gateway (C4, platform). North-south Gateway API controller.
# The GatewayClass + Gateway are app manifests synced by Argo CD from
# k8s/manifests/gateway; HTTPRoutes follow in C5 once the services exist.
module "envoy_gateway" {
  source        = "./modules/envoy-gateway"
  chart_version = local.chart_versions.envoy_gateway

  depends_on = [module.cilium]
}
