# Sealed Secrets controller (platform, C5). Holds the cluster's private key and
# decrypts SealedSecret CRs into real Secrets in-cluster. This is what lets the
# app's DB-role passwords live in git ENCRYPTED (committed) and never in plaintext.
#
# fullnameOverride=sealed-secrets-controller: the `kubeseal` CLI looks for the
# controller under that exact name in kube-system by default, so keep it.
resource "helm_release" "sealed_secrets" {
  name       = "sealed-secrets"
  repository = "https://bitnami.github.io/sealed-secrets"
  chart      = "sealed-secrets"
  version    = var.chart_version
  namespace  = var.namespace

  set = [
    {
      name  = "fullnameOverride"
      value = "sealed-secrets-controller"
    },
  ]

  wait    = true
  timeout = 600
}
