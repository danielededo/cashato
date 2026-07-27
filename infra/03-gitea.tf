# Layer 3 — Gitea: local self-hosted git remote for the GitOps bridge.
# Argo CD (layer 4) pulls app manifests from here until the repo goes public
# on GitHub.
module "gitea" {
  source        = "./modules/gitea"
  chart_version = local.chart_versions.gitea

  admin_username = var.git_bridge_username
  admin_password = var.git_bridge_password

  depends_on = [module.cilium]
}
