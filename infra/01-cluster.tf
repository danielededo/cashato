# Layer 0 — the kind cluster itself.
# Default CNI is disabled so Cilium (layer 1) takes over. kube-proxy is also
# disabled (kube_proxy_mode = "none") so Cilium runs in full kubeProxyReplacement
# mode — required for L2 announcements / LoadBalancer IPAM. Cilium owns all
# service routing; no kube-proxy iptables to conflict with.
resource "kind_cluster" "default" {
  name           = var.cluster_name
  node_image     = var.node_image
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    # Node-pull wiring: teach every node's containerd to pull images tagged
    # `gitea-http.gitea.svc:3000/...` from Gitea's built-in OCI registry. The nodes
    # cannot resolve that in-cluster DNS name themselves, so the mirror KEY (the
    # name in the image ref) is redirected to the registry's NodePort, reachable
    # from the node netns via Cilium's kubeProxyReplacement (127.0.0.1:<nodePort>).
    # nodePort 30300 must match the gitea-registry Service (modules/gitea/values.yaml).
    #
    # Plain-HTTP endpoint = insecure pull (no TLS); fine for a local single-user
    # bridge. This is the inline `registry.mirrors` form, valid on the node image's
    # containerd 1.7.x (deprecated but honored). NOTE: containerd 2.x REMOVED this
    # CRI field — if node_image is bumped to a containerd-2.x kindest/node, switch to
    # the `config_path = "/etc/containerd/certs.d"` + hosts.toml form (via extra_mounts).
    # NOTE: applying this forces a cluster REPLACE (kind bakes containerd config at
    # node creation). The running cluster carries the equivalent config as a manual
    # hot-patch; this block reproduces it on the next intentional rebuild.
    containerd_config_patches = [
      <<-TOML
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."gitea-http.gitea.svc:3000"]
        endpoint = ["http://127.0.0.1:30300"]
      TOML
    ]

    networking {
      # Cilium replaces the default CNI and kube-proxy.
      disable_default_cni = true
      kube_proxy_mode     = "none"
    }

    # Control-plane also fronts host ports 80/443 so Envoy Gateway can be
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
