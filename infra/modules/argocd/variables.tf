variable "chart_version" {
  description = "Argo CD Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for Argo CD."
  type        = string
  default     = "argocd"
}
