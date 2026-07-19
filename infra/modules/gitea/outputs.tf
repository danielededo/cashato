# In-cluster HTTP endpoint Argo CD uses as the git remote.
output "http_url" {
  description = "In-cluster base URL of the Gitea HTTP service."
  value       = "http://gitea-http.${var.namespace}.svc:3000"
}

output "namespace" {
  description = "Namespace where Gitea runs."
  value       = var.namespace
}
