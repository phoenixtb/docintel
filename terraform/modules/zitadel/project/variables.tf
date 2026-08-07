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

# Native (mobile) app redirects: custom URI scheme for the OS-level callback,
# plus an HTTPS App Link/Universal Link fallback (required by Android App Links /
# iOS Universal Links, and safer than a bare custom scheme against interception).
variable "native_redirect_uris" {
  type    = list(string)
  default = ["com.docintel.app:/callback", "https://app.docintel.dev/callback"]
}

variable "native_post_logout_redirect_uris" {
  type    = list(string)
  default = ["com.docintel.app:/logout"]
}
