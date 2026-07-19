# Cilium as the cluster CNI (default CNI is disabled in the kind config),
# with Hubble relay + UI enabled for east-west observability.
resource "helm_release" "cilium" {
  name       = "cilium"
  repository = "https://helm.cilium.io"
  chart      = "cilium"
  version    = var.chart_version
  namespace  = var.namespace

  # helm provider v3: `set` is a list attribute, not repeated blocks.
  # Default IPAM (cluster-pool) works on kind; Hubble relay + UI for east-west
  # observability.
  set = [
    {
      name  = "image.pullPolicy"
      value = "IfNotPresent"
    },
    {
      name  = "hubble.enabled"
      value = "true"
    },
    {
      name  = "hubble.relay.enabled"
      value = "true"
    },
    {
      name  = "hubble.ui.enabled"
      value = "true"
    },
  ]

  # Wait until the CNI is actually up before Tofu considers the release done.
  wait    = true
  timeout = 600
}
