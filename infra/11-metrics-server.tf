# Layer 11 — metrics-server (platform). Backs the metrics.k8s.io API so the HPA
# controller and `kubectl top` work. Without it, the KServe predictor HPA and the
# Tekton webhook HPAs spam kube-controller-manager with failing reconciles. Placed
# after the CNI so its pod can be scheduled and reach the kubelets.
module "metrics_server" {
  source        = "./modules/metrics-server"
  chart_version = local.chart_versions.metrics_server

  depends_on = [module.cilium]
}
