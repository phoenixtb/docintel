variable "zitadel_domain" {
  description = "External domain of the Zitadel instance"
  default     = "localhost"
}

variable "zitadel_port" {
  description = "External port of the Zitadel instance"
  default     = "9090"
}

variable "zitadel_admin_token" {
  description = "Bootstrap admin PAT from config/zitadel/bootstrap/admin.pat (instance IAM_OWNER)"
  sensitive   = true
}

variable "redirect_uris" {
  description = "OIDC redirect URIs for the web UI SPA"
  type        = list(string)
  default     = ["http://localhost:3001/auth/callback"]
}

variable "dev_mode" {
  description = "Enables OIDC dev mode (allows any redirect URI, no strict validation)"
  type        = bool
  default     = true
}
