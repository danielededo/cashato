variable "chart_version" {
  description = "NATS Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for the NATS server (platform-owned by Tofu)."
  type        = string
  default     = "nats"
}

variable "jetstream_storage_size" {
  description = "Size of the JetStream fileStore PVC. Payloads are small file references, not bytes, so this stays tiny; WorkQueue retention keeps it bounded."
  type        = string
  default     = "1Gi"
}
