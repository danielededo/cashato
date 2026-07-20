variable "chart_version" {
  description = "KServe Helm chart version (OCI, applies to both kserve-crd and kserve)."
  type        = string
}

variable "namespace" {
  description = "Namespace for the KServe controller."
  type        = string
  default     = "kserve"
}
