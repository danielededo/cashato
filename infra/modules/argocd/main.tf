# Argo CD — the GitOps engine. OpenTofu installs it (platform, day-0); from here
# on it pulls application manifests from git (Gitea now, GitHub later).
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  values = [file("${path.module}/values.yaml")]

  wait    = true
  timeout = 900
}
