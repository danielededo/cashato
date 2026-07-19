variable "chart_version" {
  description = "Gitea Helm chart version."
  type        = string
}

variable "namespace" {
  description = "Namespace for Gitea."
  type        = string
  default     = "gitea"
}

variable "admin_username" {
  description = "Gitea admin username (bootstrap)."
  type        = string
  default     = "cashato"
}

variable "admin_password" {
  description = "Gitea admin password (bootstrap; local bridge only)."
  type        = string
  default     = "cashato-admin-pw"
  sensitive   = true
}

variable "admin_email" {
  description = "Gitea admin email (bootstrap)."
  type        = string
  default     = "admin@cashato.local"
}

variable "storage_size" {
  description = "PVC size for Gitea data (sqlite + repos)."
  type        = string
  default     = "2Gi"
}
