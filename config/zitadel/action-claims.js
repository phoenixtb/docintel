/**
 * Zitadel custom claims Action — injected into token and userinfo flows.
 *
 * Sets flat claims consumed by TenantFilter.kt and OPA:
 *   tenant_id  — Zitadel org name (lower-cased) that owns the user account
 *   role       — highest project role granted to the user (platform_admin > tenant_admin > tenant_user)
 *
 * Trigger: COMPLEMENT_TOKEN / PRE_ACCESS_TOKEN_CREATION + PRE_USERINFO_CREATION
 */
function setCustomClaims(ctx, api) {
  // tenant_id: the org the user belongs to (org name == tenant ID in DocIntel)
  var orgName = (ctx.v1.user.resourceOwner && ctx.v1.user.resourceOwner.name) || 'default';
  api.v1.claims.setClaim('tenant_id', orgName.toLowerCase());

  // role: highest project role across all grants, precedence: platform_admin > tenant_admin > tenant_user
  var grants = (ctx.v1.user && ctx.v1.user.grants) || [];
  var role = 'tenant_user';
  for (var i = 0; i < grants.length; i++) {
    var roles = grants[i].roles || [];
    for (var j = 0; j < roles.length; j++) {
      if (roles[j] === 'platform_admin') { role = 'platform_admin'; break; }
      if (roles[j] === 'tenant_admin')   { role = 'tenant_admin'; }
    }
    if (role === 'platform_admin') break;
  }
  api.v1.claims.setClaim('role', role);
}
