variable "chart_version" {
  description = "Cilium Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for the Cilium release."
  type        = string
  default     = "kube-system"
}

variable "k8s_service_host" {
  description = "API server host Cilium dials directly in kubeProxyReplacement mode (no kube-proxy). On kind this is the control-plane container name, resolvable via the docker network."
  type        = string
}

variable "k8s_service_port" {
  description = "API server port for kubeProxyReplacement mode."
  type        = number
  default     = 6443
}
