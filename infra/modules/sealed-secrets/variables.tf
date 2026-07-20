variable "chart_version" {
  description = "sealed-secrets Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for the sealed-secrets controller. kube-system is the kubeseal default."
  type        = string
  default     = "kube-system"
}
