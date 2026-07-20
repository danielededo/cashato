variable "chart_version" {
  description = "cert-manager (jetstack) Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for cert-manager."
  type        = string
  default     = "cert-manager"
}
