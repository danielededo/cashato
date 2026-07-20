variable "chart_version" {
  description = "sealed-secrets Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for the sealed-secrets controller. kube-system is the kubeseal default."
  type        = string
  default     = "kube-system"
}

variable "tls_crt" {
  description = "PEM public cert of the PINNED sealing key. Pre-provisioning it (labeled active) means the controller reuses the same key across cluster rebuilds, so committed SealedSecrets stay decryptable. This is the 'secret zero', kept out of git."
  type        = string
}

variable "tls_key" {
  description = "PEM private key of the pinned sealing key (secret zero)."
  type        = string
  sensitive   = true
}
