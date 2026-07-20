# Argo CD — the GitOps engine. OpenTofu installs it (platform, day-0); from here
# on it pulls application manifests from git (Gitea now, GitHub later).
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = true

  values = [file("${path.module}/values.yaml")]

  wait    = true
  timeout = 900
}

# Repository registration. Argo CD has no Repository CRD: a repo is a Secret
# labeled `argocd.argoproj.io/secret-type: repository`, read by the repo-server.
# This is a BOOTSTRAP secret — Argo needs it to pull the app-of-apps before the
# Sealed Secrets controller could decrypt anything — so it CANNOT be a
# SealedSecret. Tofu creates it directly from the git_bridge_password var, which
# is supplied out-of-band via infra/secret.auto.tfvars (gitignored), so no secret
# lands in git or in committed state.
resource "kubernetes_secret" "gitea_repo" {
  metadata {
    name      = "gitea-repo"
    namespace = var.namespace
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }

  data = {
    type     = "git"
    url      = var.repo_url
    username = var.repo_username
    password = var.repo_password
  }

  depends_on = [helm_release.argocd]
}

# Root app-of-apps. Declared via the argocd-apps chart so there is no plan-time
# dependency on the Application CRD (helm_release does not validate CRDs at plan).
# It watches k8s/apps/ and syncs each child Application found there.
resource "helm_release" "root_app" {
  name       = "argocd-root-app"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_version
  namespace  = var.namespace

  values = [yamlencode({
    applications = {
      root = {
        namespace  = var.namespace
        finalizers = ["resources-finalizer.argocd.argoproj.io"]
        project    = "default"
        source = {
          repoURL        = var.repo_url
          targetRevision = var.target_revision
          path           = var.apps_path
          directory = {
            recurse = true
          }
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = var.namespace
        }
        syncPolicy = {
          automated = {
            prune    = true
            selfHeal = true
          }
          syncOptions = ["CreateNamespace=true"]
        }
      }
    }
  })]

  depends_on = [kubernetes_secret.gitea_repo]
}
