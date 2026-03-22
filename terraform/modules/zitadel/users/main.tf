terraform {
  required_providers {
    zitadel = {
      source = "zitadel/zitadel"
    }
  }
}

locals {
  # Map of user resource key → {user_id, password} for password provisioning.
  # Populated after user creation via terraform_data local-exec.
  seed_users = {
    diadmin    = { id = zitadel_human_user.diadmin.id,    password = "Diadmin@123" }
    alphaadmin = { id = zitadel_human_user.alphaadmin.id, password = "Alphaadmin@123" }
    alphauser  = { id = zitadel_human_user.alphauser.id,  password = "Alphauser@123" }
    betaadmin  = { id = zitadel_human_user.betaadmin.id,  password = "Betaadmin@123" }
    betauser   = { id = zitadel_human_user.betauser.id,   password = "Betauser@123" }
  }
}

# ── Human users ───────────────────────────────────────────────────────────────
# No initial_password — passwords are set below via API with change_required=false.

resource "zitadel_human_user" "diadmin" {
  org_id            = var.platform_org_id
  user_name         = "diadmin"
  first_name        = "Di"
  last_name         = "Admin"
  display_name      = "Di Admin"
  email             = "diadmin@platform.local"
  is_email_verified = true
}

resource "zitadel_human_user" "alphaadmin" {
  org_id            = var.alpha_org_id
  user_name         = "alphaadmin"
  first_name        = "Alpha"
  last_name         = "Admin"
  display_name      = "Alpha Admin"
  email             = "alphaadmin@alpha.local"
  is_email_verified = true
}

resource "zitadel_human_user" "alphauser" {
  org_id            = var.alpha_org_id
  user_name         = "alphauser"
  first_name        = "Alpha"
  last_name         = "User"
  display_name      = "Alpha User"
  email             = "alphauser@alpha.local"
  is_email_verified = true
}

resource "zitadel_human_user" "betaadmin" {
  org_id            = var.beta_org_id
  user_name         = "betaadmin"
  first_name        = "Beta"
  last_name         = "Admin"
  display_name      = "Beta Admin"
  email             = "betaadmin@beta.local"
  is_email_verified = true
}

resource "zitadel_human_user" "betauser" {
  org_id            = var.beta_org_id
  user_name         = "betauser"
  first_name        = "Beta"
  last_name         = "User"
  display_name      = "Beta User"
  email             = "betauser@beta.local"
  is_email_verified = true
}

# ── Set passwords without change_required ─────────────────────────────────────
# Zitadel's initial_password always sets change_required=true.
# The v2 API SetPassword allows change_required=false for seed/dev users.

resource "terraform_data" "seed_passwords" {
  for_each = local.seed_users

  input = {
    url      = var.zitadel_url
    pat      = var.admin_pat
    user_id  = each.value.id
    password = each.value.password
  }

  provisioner "local-exec" {
    command = <<-EOT
      curl -sf -X POST "${self.input.url}/v2/users/${self.input.user_id}/password" \
        -H "Authorization: Bearer ${self.input.pat}" \
        -H "Content-Type: application/json" \
        -d "{\"newPassword\": {\"password\": \"${self.input.password}\", \"changeRequired\": false}}"
    EOT
  }

  depends_on = [
    zitadel_human_user.diadmin,
    zitadel_human_user.alphaadmin,
    zitadel_human_user.alphauser,
    zitadel_human_user.betaadmin,
    zitadel_human_user.betauser,
  ]
}

# ── E2E test service account ─────────────────────────────────────────────────
# Authenticates via JWT Bearer (RFC 7523) — no ROPC, no browser, no user creds.
# Has tenant_user role in alpha org (minimum scope needed for RAG queries).

resource "zitadel_machine_user" "e2e_sa" {
  org_id            = var.e2e_org_id
  user_name         = "docintel-e2e-sa"
  name              = "DocIntel E2E Test Service Account"
  description       = "Service account for automated E2E tests (run_tests.py). Has tenant_admin in the e2e org to manage test data lifecycle."
  access_token_type = "ACCESS_TOKEN_TYPE_JWT"
}

resource "zitadel_machine_key" "e2e_sa" {
  org_id   = var.e2e_org_id
  user_id  = zitadel_machine_user.e2e_sa.id
  key_type = "KEY_TYPE_JSON"
}

resource "zitadel_user_grant" "e2e_sa" {
  org_id           = var.e2e_org_id
  user_id          = zitadel_machine_user.e2e_sa.id
  project_id       = var.project_id
  project_grant_id = var.e2e_grant_id
  role_keys        = ["tenant_admin"]
}

# ── Machine service account (admin-service) ───────────────────────────────────

resource "zitadel_machine_user" "admin_sa" {
  org_id            = var.platform_org_id
  user_name         = "docintel-admin-sa"
  name              = "DocIntel Admin Service Account"
  description       = "Service account for admin-service tenant management"
  access_token_type = "ACCESS_TOKEN_TYPE_JWT"
}

resource "zitadel_personal_access_token" "admin_sa" {
  org_id          = var.platform_org_id
  user_id         = zitadel_machine_user.admin_sa.id
  expiration_date = "2099-01-01T00:00:00Z"
}

# ── Project role grants ───────────────────────────────────────────────────────
# Platform org owns the project — no project_grant_id needed for platform users.
# Tenant orgs received a project_grant — project_grant_id required.

resource "zitadel_user_grant" "diadmin" {
  org_id     = var.platform_org_id
  user_id    = zitadel_human_user.diadmin.id
  project_id = var.project_id
  role_keys  = ["platform_admin"]
}

resource "zitadel_user_grant" "admin_sa" {
  org_id     = var.platform_org_id
  user_id    = zitadel_machine_user.admin_sa.id
  project_id = var.project_id
  role_keys  = ["platform_admin"]
}

# Instance-level viewer role so the SA can read user grants across all orgs
# (needed by docintel-actions to expand coarse roles to fine-grained claims)
resource "zitadel_instance_member" "admin_sa_iam_viewer" {
  user_id = zitadel_machine_user.admin_sa.id
  roles   = ["IAM_OWNER_VIEWER"]
}

resource "zitadel_user_grant" "alphaadmin" {
  org_id           = var.alpha_org_id
  user_id          = zitadel_human_user.alphaadmin.id
  project_id       = var.project_id
  project_grant_id = var.alpha_grant_id
  role_keys        = ["tenant_admin"]
}

resource "zitadel_user_grant" "alphauser" {
  org_id           = var.alpha_org_id
  user_id          = zitadel_human_user.alphauser.id
  project_id       = var.project_id
  project_grant_id = var.alpha_grant_id
  role_keys        = ["tenant_user"]
}

resource "zitadel_user_grant" "betaadmin" {
  org_id           = var.beta_org_id
  user_id          = zitadel_human_user.betaadmin.id
  project_id       = var.project_id
  project_grant_id = var.beta_grant_id
  role_keys        = ["tenant_admin"]
}

resource "zitadel_user_grant" "betauser" {
  org_id           = var.beta_org_id
  user_id          = zitadel_human_user.betauser.id
  project_id       = var.project_id
  project_grant_id = var.beta_grant_id
  role_keys        = ["tenant_user"]
}
