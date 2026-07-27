# Layer 7 — Envoy Gateway (platform). North-south Gateway API controller.
# The GatewayClass + Gateway are app manifests synced by Argo CD from
# k8s/manifests/gateway; the services' HTTPRoutes live in k8s/manifests/services.
module "envoy_gateway" {
  source        = "./modules/envoy-gateway"
  chart_version = local.chart_versions.envoy_gateway

  depends_on = [module.cilium]
}
