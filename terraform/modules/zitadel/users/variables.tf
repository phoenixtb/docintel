variable "platform_org_id" {
  type = string
}

variable "alpha_org_id" {
  type = string
}

variable "beta_org_id" {
  type = string
}

variable "project_id" {
  type = string
}

variable "alpha_grant_id" {
  description = "Zitadel project grant ID for alpha org (from zitadel_project_grant.alpha.grant_id)"
  type        = string
}

variable "beta_grant_id" {
  description = "Zitadel project grant ID for beta org (from zitadel_project_grant.beta.grant_id)"
  type        = string
}

variable "e2e_org_id" {
  description = "Zitadel org ID for the e2e test org"
  type        = string
}

variable "e2e_grant_id" {
  description = "Zitadel project grant ID for e2e org (from zitadel_project_grant.e2e.grant_id)"
  type        = string
}

variable "zitadel_url" {
  description = "Zitadel base URL for Management API calls"
  default     = "http://localhost:9090"
}

variable "admin_pat" {
  description = "Bootstrap admin PAT for setting passwords via API"
  sensitive   = true
}
