# NATS with JetStream — the event backbone. ingest-api publishes ingest.jobs;
# etl-worker consumes them (and category.feedback). Operator-style platform
# component installed by Tofu; the stream/consumers are created by the services
# at runtime (libs/messaging.py) with WorkQueue retention so the PVC stays bounded.
resource "helm_release" "nats" {
  name             = "nats"
  repository       = "https://nats-io.github.io/k8s/helm/charts/"
  chart            = "nats"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  values = [file("${path.module}/values.yaml")]

  set = [
    {
      name  = "config.jetstream.fileStore.pvc.size"
      value = var.jetstream_storage_size
    },
  ]

  wait    = true
  timeout = 600
}
