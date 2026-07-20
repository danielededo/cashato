# Layer 1 — Cilium as the cluster CNI (default CNI disabled in layer 0),
# with Hubble for east-west observability.
module "cilium" {
  source        = "./modules/cilium"
  chart_version = local.chart_versions.cilium

  # kind names the control-plane container "<cluster>-control-plane"; it is
  # resolvable via the docker network from the Cilium agents (host netns).
  k8s_service_host = "${kind_cluster.default.name}-control-plane"
  k8s_service_port = 6443

  depends_on = [kind_cluster.default]
}
