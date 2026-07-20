# cert-manager (platform, C6b). Provides the webhook-serving certificates that
# KServe's admission webhooks require; installed as a dependency of KServe (not
# used by the app services). crds.enabled bundles the cert-manager CRDs with the
# chart (v1.21 key; the old installCRDs flag is deprecated).
resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  set = [
    {
      name  = "crds.enabled"
      value = "true"
    },
  ]

  wait    = true
  timeout = 600
}
