# Inputs meant to be overridden per invocation. Chart version pins live in
# locals.tf (chart_versions), not here.
variable "cluster_name" {
  description = "Name of the local kind cluster."
  type        = string
  default     = "cashato"
}

variable "node_image" {
  description = "kindest/node image (pins the Kubernetes version)."
  type        = string
  default     = "kindest/node:v1.32.0"
}
