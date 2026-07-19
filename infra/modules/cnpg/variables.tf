variable "chart_version" {
  description = "CloudNativePG operator Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for the CNPG operator."
  type        = string
  default     = "cnpg-system"
}
