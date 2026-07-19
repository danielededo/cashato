output "cluster_name" {
  description = "kind cluster name."
  value       = kind_cluster.default.name
}

output "kubeconfig_path" {
  description = "Path to the kubeconfig written for the cluster."
  value       = kind_cluster.default.kubeconfig_path
}

output "endpoint" {
  description = "Kubernetes API server endpoint."
  value       = kind_cluster.default.endpoint
}
