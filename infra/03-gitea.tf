# Layer 3 — Gitea: local self-hosted git remote for the GitOps bridge (C2).
# Argo CD (layer 4) pulls app manifests from here until the repo goes public
# on GitHub.
module "gitea" {
  source        = "./modules/gitea"
  chart_version = local.chart_versions.gitea

  depends_on = [module.cilium]
}
