terraform {
  required_version = ">= 1.6"

  backend "local" {
    path = "terraform.tfstate"
  }

  required_providers {
    zitadel = {
      source  = "zitadel/zitadel"
      version = "~> 2.10"
    }
  }
}

# Authenticates using the bootstrap admin PAT (written by Zitadel first-instance init).
# Pass via ZITADEL_TOKEN env var when running tofu, e.g.:
#   ZITADEL_TOKEN=$(cat config/zitadel/bootstrap/admin.pat) tofu apply ...
provider "zitadel" {
  domain   = var.zitadel_domain
  port     = var.zitadel_port
  insecure = true
  access_token = var.zitadel_admin_token
}

module "orgs" {
  source = "../../modules/zitadel/orgs"
}

module "project" {
  source          = "../../modules/zitadel/project"
  platform_org_id = module.orgs.platform_org_id
  alpha_org_id    = module.orgs.alpha_org_id
  beta_org_id     = module.orgs.beta_org_id
  e2e_org_id      = module.orgs.e2e_org_id
  redirect_uris   = var.redirect_uris
  dev_mode        = var.dev_mode
}

module "users" {
  source          = "../../modules/zitadel/users"
  platform_org_id = module.orgs.platform_org_id
  alpha_org_id    = module.orgs.alpha_org_id
  beta_org_id     = module.orgs.beta_org_id
  e2e_org_id      = module.orgs.e2e_org_id
  project_id      = module.project.project_id
  alpha_grant_id  = module.project.alpha_grant_id
  beta_grant_id   = module.project.beta_grant_id
  e2e_grant_id    = module.project.e2e_grant_id
  zitadel_url     = "http://localhost:${var.zitadel_port}"
  admin_pat       = var.zitadel_admin_token
}
