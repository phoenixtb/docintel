output "project_id" {
  value = zitadel_project.docintel.id
}

output "client_id" {
  value = zitadel_application_oidc.web_ui.client_id
}

output "alpha_grant_id" {
  value = zitadel_project_grant.alpha.id
}

output "beta_grant_id" {
  value = zitadel_project_grant.beta.id
}

output "e2e_grant_id" {
  value = zitadel_project_grant.e2e.id
}
