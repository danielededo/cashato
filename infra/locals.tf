locals {
  # Single source of truth for platform Helm chart versions. Pin every operator
  # here so upgrades are a one-line, reviewable change.
  chart_versions = {
    cilium         = "1.16.5"  # CNI + Hubble
    gitea          = "12.6.0"  # self-hosted git (local GitOps bridge)
    argocd         = "10.1.4"  # GitOps (app v3.4.5)
    argocd_apps    = "2.0.5"   # app-of-apps root Application
    cnpg           = "0.29.0"  # Postgres operator (app v1.30.0)
    nats           = "2.14.2"  # JetStream backbone (app v2.14.2)
    envoy_gateway  = "v1.8.2"  # Gateway API north-south (OCI chart)
    sealed_secrets = "2.19.1"  # Sealed Secrets controller (app v0.38.4)
    cert_manager   = "v1.21.0" # webhook certs (KServe dependency)
    kserve         = "v0.15.0" # model serving (RawDeployment, OCI chart)
    metrics_server = "3.12.2"  # metrics.k8s.io API for HPA + kubectl top (app v0.7.2)
  }
}
