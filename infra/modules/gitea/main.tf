# Self-hosted Gitea — the local git remote Argo CD pulls from (GitOps bridge).
# Minimal config comes from values.yaml; admin creds + storage size are wired
# via `set` so they can be overridden per environment.
resource "helm_release" "gitea" {
  name             = "gitea"
  repository       = "https://dl.gitea.com/charts/"
  chart            = "gitea"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  values = [file("${path.module}/values.yaml")]

  set = [
    {
      name  = "gitea.admin.username"
      value = var.admin_username
    },
    {
      name  = "gitea.admin.password"
      value = var.admin_password
    },
    {
      name  = "gitea.admin.email"
      value = var.admin_email
    },
    {
      name  = "persistence.size"
      value = var.storage_size
    },
  ]

  wait    = true
  timeout = 600
}
