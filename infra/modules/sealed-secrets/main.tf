# Sealed Secrets controller (platform, C5). Holds the cluster's private key and
# decrypts SealedSecret CRs into real Secrets in-cluster. This is what lets the
# app's DB-role passwords live in git ENCRYPTED (committed) and never in plaintext.
#
# fullnameOverride=sealed-secrets-controller: the `kubeseal` CLI looks for the
# controller under that exact name in kube-system by default, so keep it.

# PINNED sealing key: pre-create the active key Secret from a fixed keypair kept
# out of git (infra/secrets/, gitignored). On a fresh cluster the controller finds
# this active key on startup and adopts it INSTEAD of generating a random one, so
# every rebuild reuses the same key and the SealedSecrets committed to git remain
# decryptable. Without this, a rebuild -> new random key -> all SealedSecrets break.
resource "kubernetes_secret" "sealing_key" {
  metadata {
    name      = "sealed-secrets-key-pinned"
    namespace = var.namespace
    labels = {
      "sealedsecrets.bitnami.com/sealed-secrets-key" = "active"
    }
  }
  type = "kubernetes.io/tls"
  data = {
    "tls.crt" = var.tls_crt
    "tls.key" = var.tls_key
  }
}

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

  # Key must exist before the controller starts so it adopts it (vs generating one).
  depends_on = [kubernetes_secret.sealing_key]
}
