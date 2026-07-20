output "namespace" {
  description = "Namespace where NATS runs."
  value       = var.namespace
}

output "url" {
  description = "In-cluster NATS client URL for the services."
  value       = "nats://nats.${var.namespace}.svc:4222"
}
