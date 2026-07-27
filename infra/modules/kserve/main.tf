# KServe (platform) — model serving. Installed in **RawDeployment mode**: no
# Knative, no Istio — InferenceServices become plain Kubernetes Deployments +
# Services, much lighter on kind. Our custom EmbeddingKNN predictor is
# served as an InferenceService (an app manifest via Argo), reached in-cluster by
# the categorizer; ingress creation is disabled since we don't front it with
# a K8s Ingress (access is cluster-internal via the predictor Service).
#
# Two OCI charts: kserve-crd (the CRDs) must land before kserve (the controller).

resource "helm_release" "kserve_crd" {
  name             = "kserve-crd"
  repository       = "oci://ghcr.io/kserve/charts"
  chart            = "kserve-crd"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  wait    = true
  timeout = 600
}

resource "helm_release" "kserve" {
  name       = "kserve"
  repository = "oci://ghcr.io/kserve/charts"
  chart      = "kserve"
  version    = var.chart_version
  namespace  = var.namespace

  set = [
    {
      name  = "kserve.controller.deploymentMode"
      value = "RawDeployment"
    },
    {
      name  = "kserve.controller.gateway.disableIngressCreation"
      value = "true"
    },
  ]

  wait    = true
  timeout = 600

  depends_on = [helm_release.kserve_crd]
}
