output "service_account_pat" {
  description = "PAT for docintel-admin-sa — used by admin-service for Zitadel management API calls"
  value       = zitadel_personal_access_token.admin_sa.token
  sensitive   = true
}

output "admin_sa_user_id" {
  value = zitadel_machine_user.admin_sa.id
}

output "e2e_sa_key_json" {
  description = "JSON key for the E2E test service account (JWT Bearer auth, RFC 7523)"
  value       = zitadel_machine_key.e2e_sa.key_details
  sensitive   = true
}
