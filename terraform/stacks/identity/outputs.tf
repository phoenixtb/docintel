output "client_id" {
  description = "OIDC client ID for the DocIntel Web UI SPA"
  value       = module.project.client_id
  sensitive   = true
}

output "project_id" {
  description = "Zitadel project ID for DocintelProject"
  value       = module.project.project_id
}

output "service_account_pat" {
  description = "Personal Access Token for the docintel-admin-sa machine user"
  value       = module.users.service_account_pat
  sensitive   = true
}

output "e2e_sa_key_json" {
  description = "JSON key for E2E test SA — written to config/zitadel/e2e-sa-key.json by start.sh"
  value       = module.users.e2e_sa_key_json
  sensitive   = true
}

output "platform_org_id" {
  description = "Zitadel org ID for the platform org"
  value       = module.orgs.platform_org_id
}

output "alpha_org_id" {
  value = module.orgs.alpha_org_id
}

output "beta_org_id" {
  value = module.orgs.beta_org_id
}
