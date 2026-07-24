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
    # kubeProxyReplacement (C4): kube-proxy is disabled in the kind config, so
    # Cilium handles all service routing. Without kube-proxy, agents must dial the
    # API server directly — hence k8sServiceHost/Port.
    {
      name  = "kubeProxyReplacement"
      value = "true"
    },
    {
      name  = "k8sServiceHost"
      value = var.k8s_service_host
    },
    {
      name  = "k8sServicePort"
      value = tostring(var.k8s_service_port)
    },
    # L2 announcements (C4): answer ARP for LoadBalancer IPs so the Envoy Gateway
    # service is reachable on the local (docker) network. Uses leader election →
    # bump the client rate limit above the defaults (10/20).
    {
      name  = "l2announcements.enabled"
      value = "true"
    },
    {
      name  = "k8sClientRateLimit.qps"
      value = "50"
    },
    {
      name  = "k8sClientRateLimit.burst"
      value = "100"
    },
    # Disable the L7 (Envoy) proxy. We only use L3/L4 CiliumNetworkPolicies (no
    # toFQDNs / L7 DNS rules), so the DNS proxy is dead weight — and on the WSL2
    # kernel it can't even install its rules: `xt_TPROXY` is missing, so Cilium
    # loops forever on "iptables ... cilium-dns-egress ... TPROXY revision 0 not
    # supported, missing kernel module". Turning off l7Proxy stops those attempts.
    # Re-enable this if L7 policy or toFQDNs is ever needed (requires a TPROXY-
    # capable kernel).
    {
      name  = "l7Proxy"
      value = "false"
    },
  ]

  # Wait until the CNI is actually up before Tofu considers the release done.
  wait    = true
  timeout = 600
}
