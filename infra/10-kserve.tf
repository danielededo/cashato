# Layer 10 — KServe (platform). Serves the categorization model. Depends on
# cert-manager (webhook certs). RawDeployment mode = no Knative/Istio.
# Boundary: Tofu installs the controller + CRDs only; the InferenceService for our
# custom predictor is an app manifest delivered by Argo CD from k8s/.
module "kserve" {
  source        = "./modules/kserve"
  chart_version = local.chart_versions.kserve

  depends_on = [module.cert_manager]
}
