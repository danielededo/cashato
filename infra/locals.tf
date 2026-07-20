locals {
  # Single source of truth for platform Helm chart versions. Pin every operator
  # here so upgrades are a one-line, reviewable change. Uncomment as milestones
  # land.
  chart_versions = {
    cilium        = "1.16.5" # C1 — CNI + Hubble
    gitea         = "12.6.0" # C2 — self-hosted git (local GitOps bridge)
    argocd        = "10.1.4" # C2 — GitOps (app v3.4.5)
    argocd_apps   = "2.0.5"  # C2 — app-of-apps root Application
    cnpg          = "0.29.0" # C3 — Postgres operator (app v1.30.0)
    nats          = "2.14.2" # C4 — JetStream backbone (app v2.14.2)
    envoy_gateway = "v1.8.2" # C4 — Gateway API north-south (OCI chart)
    # sealed_secrets = "..."  # C5 — Bitnami controller
  }
}
