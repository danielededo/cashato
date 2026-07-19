# Layer 4 — Argo CD: the GitOps engine (C2). Pulls app manifests from Gitea
# (layer 3) now, from public GitHub later. The root app-of-apps that points it
# at k8s/apps/ is seeded separately once the Gitea repo is bootstrapped.
module "argocd" {
  source        = "./modules/argocd"
  chart_version = local.chart_versions.argocd

  depends_on = [module.cilium]
}
