# Layer 4 — Argo CD: the GitOps engine (C2). Pulls app manifests from Gitea
# (layer 3) now, from public GitHub later. The root app-of-apps that points it
# at k8s/apps/ is seeded separately once the Gitea repo is bootstrapped.
module "argocd" {
  source              = "./modules/argocd"
  chart_version       = local.chart_versions.argocd
  argocd_apps_version = local.chart_versions.argocd_apps

  # Root app-of-apps watches the CONFIG/deploy repo (cashato-deploy); the child
  # apps it finds there pull manifests from the source repo. One repo-creds Secret
  # (creds_url = the Gitea owner prefix) authenticates BOTH repos (C7c-e).
  repo_url      = "${module.gitea.http_url}/${var.git_bridge_username}/${var.git_deploy_repo}.git"
  creds_url     = "${module.gitea.http_url}/${var.git_bridge_username}"
  repo_username = var.git_bridge_username
  repo_password = var.git_bridge_password
  apps_path     = "k8s/apps"

  depends_on = [module.cilium, module.gitea]
}
