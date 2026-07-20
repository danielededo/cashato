# Envoy Gateway — the north-south Gateway API controller. Installed from the
# official OCI chart; `crds.enabled` (chart default true) bundles BOTH the Envoy
# Gateway CRDs and the upstream Gateway API CRDs (GatewayClass/Gateway/HTTPRoute),
# so no separate CRD install is needed.
#
# Boundary: Tofu installs the controller only. The GatewayClass + Gateway (and
# later the HTTPRoutes) are app manifests delivered by Argo CD from k8s/.
resource "helm_release" "envoy_gateway" {
  name             = "envoy-gateway"
  repository       = "oci://docker.io/envoyproxy"
  chart            = "gateway-helm"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  wait    = true
  timeout = 600
}
