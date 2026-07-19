variable "chart_version" {
  description = "Cilium Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for the Cilium release."
  type        = string
  default     = "kube-system"
}
