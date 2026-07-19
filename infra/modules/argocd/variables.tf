variable "chart_version" {
  description = "Argo CD Helm chart version."
  type        = string
}

variable "argocd_apps_version" {
  description = "argocd-apps Helm chart version (declares the root Application)."
  type        = string
}

variable "namespace" {
  description = "Namespace for Argo CD."
  type        = string
  default     = "argocd"
}

variable "repo_url" {
  description = "Git repo URL Argo CD pulls app manifests from (Gitea now)."
  type        = string
}

variable "repo_username" {
  description = "Username for the repo credential Secret."
  type        = string
}

variable "repo_password" {
  description = "Password/token for the repo credential Secret."
  type        = string
  sensitive   = true
}

variable "apps_path" {
  description = "Path in the repo the root app-of-apps watches."
  type        = string
  default     = "k8s/apps"
}

variable "target_revision" {
  description = "Git revision (branch/tag) the root Application tracks."
  type        = string
  default     = "main"
}
