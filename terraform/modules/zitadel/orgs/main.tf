terraform {
  required_providers {
    zitadel = {
      source = "zitadel/zitadel"
    }
  }
}

resource "zitadel_org" "platform" {
  name = "platform"
}

resource "zitadel_org" "alpha" {
  name = "alpha"
}

resource "zitadel_org" "beta" {
  name = "beta"
}

resource "zitadel_org" "e2e" {
  name = "e2e"
}
