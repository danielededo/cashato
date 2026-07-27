# Platform IaC — provider requirements (pinned).
# OpenTofu manages day-0: the kind cluster + all operators/Helm charts + Argo CD.
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    # Creates the kind cluster via the kind Go library (no kind CLI needed).
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.9"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
  }
}
