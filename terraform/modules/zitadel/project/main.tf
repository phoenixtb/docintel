terraform {
  required_providers {
    zitadel = {
      source = "zitadel/zitadel"
    }
  }
}

resource "zitadel_project" "docintel" {
  name              = "DocintelProject"
  org_id            = var.platform_org_id
  project_role_check = true
  has_project_check  = false
}

resource "zitadel_project_role" "platform_admin" {
  org_id       = var.platform_org_id
  project_id   = zitadel_project.docintel.id
  role_key     = "platform_admin"
  display_name = "Platform Admin"
  group        = "docintel"
}

resource "zitadel_project_role" "tenant_admin" {
  org_id       = var.platform_org_id
  project_id   = zitadel_project.docintel.id
  role_key     = "tenant_admin"
  display_name = "Tenant Admin"
  group        = "docintel"
}

resource "zitadel_project_role" "tenant_user" {
  org_id       = var.platform_org_id
  project_id   = zitadel_project.docintel.id
  role_key     = "tenant_user"
  display_name = "Tenant User"
  group        = "docintel"
}

# SPA — PKCE, no client secret, JWT access tokens
resource "zitadel_application_oidc" "web_ui" {
  org_id     = var.platform_org_id
  project_id = zitadel_project.docintel.id
  name       = "DocIntel Web UI"

  redirect_uris  = var.redirect_uris
  response_types = ["OIDC_RESPONSE_TYPE_CODE"]
  grant_types    = ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE", "OIDC_GRANT_TYPE_REFRESH_TOKEN"]
  post_logout_redirect_uris = ["http://localhost:3001/"]

  app_type         = "OIDC_APP_TYPE_USER_AGENT"
  auth_method_type = "OIDC_AUTH_METHOD_TYPE_NONE"
  version          = "OIDC_VERSION_1_0"
  dev_mode         = var.dev_mode

  access_token_type           = "OIDC_TOKEN_TYPE_JWT"
  id_token_role_assertion     = true
  access_token_role_assertion = true
  id_token_userinfo_assertion = false
}

# Grant project to tenant orgs (cross-org access)
resource "zitadel_project_grant" "alpha" {
  org_id         = var.platform_org_id
  project_id     = zitadel_project.docintel.id
  granted_org_id = var.alpha_org_id
  role_keys      = ["tenant_admin", "tenant_user"]
  depends_on     = [zitadel_project_role.tenant_admin, zitadel_project_role.tenant_user]
}

resource "zitadel_project_grant" "beta" {
  org_id         = var.platform_org_id
  project_id     = zitadel_project.docintel.id
  granted_org_id = var.beta_org_id
  role_keys      = ["tenant_admin", "tenant_user"]
  depends_on     = [zitadel_project_role.tenant_admin, zitadel_project_role.tenant_user]
}

resource "zitadel_project_grant" "e2e" {
  org_id         = var.platform_org_id
  project_id     = zitadel_project.docintel.id
  granted_org_id = var.e2e_org_id
  role_keys      = ["tenant_admin", "tenant_user"]
  depends_on     = [zitadel_project_role.tenant_admin, zitadel_project_role.tenant_user]
}

# Instance-level OIDC token lifetimes (15-min access tokens)
resource "zitadel_default_oidc_settings" "docintel" {
  access_token_lifetime         = "15m0s"
  id_token_lifetime             = "12h0m0s"
  refresh_token_idle_expiration = "24h0m0s"
  refresh_token_expiration      = "720h0m0s"
}

# Instance-level label policy (emerald theme)
resource "zitadel_label_policy" "docintel" {
  org_id                  = var.platform_org_id
  primary_color           = "#10b981"
  warn_color              = "#ef4444"
  background_color        = "#f0faf6"
  font_color              = "#0f172a"
  primary_color_dark      = "#10b981"
  background_color_dark   = "#070d14"
  font_color_dark         = "#e2e8f0"
  warn_color_dark         = "#ef4444"
  disable_watermark       = true
  hide_login_name_suffix  = false
}
