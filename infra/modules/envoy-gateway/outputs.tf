output "namespace" {
  description = "Namespace where the Envoy Gateway controller runs."
  value       = var.namespace
}

output "controller_name" {
  description = "GatewayClass controllerName the GatewayClass must reference."
  value       = "gateway.envoyproxy.io/gatewayclass-controller"
}
