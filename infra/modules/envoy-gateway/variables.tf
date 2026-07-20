variable "chart_version" {
  description = "Envoy Gateway Helm chart version (OCI)."
  type        = string
}

variable "namespace" {
  description = "Namespace for the Envoy Gateway controller."
  type        = string
  default     = "envoy-gateway-system"
}
