variable "chart_version" {
  description = "metrics-server Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for metrics-server (a platform add-on in kube-system)."
  type        = string
  default     = "kube-system"
}
