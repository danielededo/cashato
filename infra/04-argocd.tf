# Layer 4 — Argo CD: the GitOps engine (C2). Pulls app manifests from Gitea
# (layer 3) now, from public GitHub later. The root app-of-apps that points it
# at k8s/apps/ is seeded separately once the Gitea repo is bootstrapped.
module "argocd" {
  source              = "./modules/argocd"
  chart_version       = local.chart_versions.argocd
  argocd_apps_version = local.chart_versions.argocd_apps

  # Register the Gitea repo (labeled Secret) and seed the root app-of-apps.
  repo_url      = "${module.gitea.http_url}/${var.git_bridge_username}/${var.git_bridge_repo}.git"
  repo_username = var.git_bridge_username
  repo_password = var.git_bridge_password
  apps_path     = "k8s/apps"

  depends_on = [module.cilium, module.gitea]
}
