# metrics-server (platform). Serves the metrics.k8s.io API (pod/node CPU+memory)
# that the HorizontalPodAutoscaler controller and `kubectl top` consume. kind does
# NOT ship it, so any HPA (KServe's predictor, Tekton's webhooks) logs a failing
# reconcile every 15s in kube-controller-manager until this exists.
#
# --kubelet-insecure-tls: kind's kubelet serving certs are self-signed and NOT
# signed by the cluster CA, so metrics-server can't verify them → it would fail to
# scrape. Skipping verification is standard/expected on kind (local, single-host).
# It is appended to the chart's defaultArgs (preferred-address-types, node-status
# -port, metric-resolution), which stay in effect.
resource "helm_release" "metrics_server" {
  name             = "metrics-server"
  repository       = "https://kubernetes-sigs.github.io/metrics-server/"
  chart            = "metrics-server"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = false

  set = [
    {
      name  = "args[0]"
      value = "--kubelet-insecure-tls"
    },
  ]

  wait    = true
  timeout = 600
}
