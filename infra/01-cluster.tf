# Layer 0 — the kind cluster itself.
# Default CNI is disabled so Cilium (layer 1) takes over. kube-proxy is kept
# (default) for a simple, reliable bootstrap; kubeProxyReplacement can come later.
resource "kind_cluster" "default" {
  name           = var.cluster_name
  node_image     = var.node_image
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    networking {
      # Cilium replaces the default CNI.
      disable_default_cni = true
    }

    # Control-plane also fronts host ports 80/443 so Envoy Gateway (C3) can be
    # reached from the host once its Service is wired up.
    node {
      role = "control-plane"

      extra_port_mappings {
        container_port = 80
        host_port      = 80
        protocol       = "TCP"
      }
      extra_port_mappings {
        container_port = 443
        host_port      = 443
        protocol       = "TCP"
      }
    }

    node {
      role = "worker"
    }
  }
}
