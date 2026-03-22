variable "platform_org_id" {
  type = string
}

variable "alpha_org_id" {
  type = string
}

variable "beta_org_id" {
  type = string
}

variable "e2e_org_id" {
  type = string
}

variable "redirect_uris" {
  type    = list(string)
  default = ["http://localhost:3001/auth/callback"]
}

variable "dev_mode" {
  type    = bool
  default = true
}
