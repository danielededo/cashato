output "namespace" {
  description = "Namespace where the sealed-secrets controller runs."
  value       = var.namespace
}

output "controller_name" {
  description = "Controller name kubeseal targets (--controller-name)."
  value       = "sealed-secrets-controller"
}
