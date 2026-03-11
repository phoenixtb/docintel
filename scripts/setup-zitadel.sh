#!/bin/bash
# DocIntel — Zitadel Initial Setup
# =================================
# Idempotent: safe to re-run. Skips if ZITADEL_CLIENT_ID already in .env.
#
# Configures Zitadel for DocIntel:
#   1. Waits for the admin PAT (written by zitadel-api first-instance init)
#   2. Creates DocintelProject with roles (get-or-create)
#   3. Creates the SPA application (PKCE, JWT access tokens, dev mode)
#   4. Creates organizations: platform, alpha, beta (get-or-create)
#   5. Creates users and grants project roles
#   6. Deploys the custom claims Action (tenant_id + role in JWT)
#   7. Sets action triggers (PRE_ACCESS_TOKEN_CREATION + PRE_USERINFO_CREATION)
#   8. Writes ZITADEL_CLIENT_ID, ZITADEL_PROJECT_ID, ZITADEL_SERVICE_ACCOUNT_PAT to .env
#
# Dependencies: curl, jq
# API base: Zitadel v4 — Management v1 + new v2/v2beta APIs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

ZITADEL_URL="http://localhost:9090"
PAT_FILE="$PROJECT_DIR/config/zitadel/bootstrap/admin.pat"
ENV_FILE="$PROJECT_DIR/.env"

# ─── Helpers ──────────────────────────────────────────────────────────────────

log()  { echo "  [zitadel] $*" >&2; }
ok()   { echo "  [zitadel] ✓ $*" >&2; }
fail() { echo "  [zitadel] ✗ $*" >&2; exit 1; }

require_cmd() {
  command -v "$1" &>/dev/null || fail "'$1' is required but not found. Install it and retry."
}

zapi() {
  local method="$1" path="$2"; shift 2
  curl -s -X "$method" \
    -H "Authorization: Bearer $PAT" \
    -H "Content-Type: application/json" \
    "$@" \
    "$ZITADEL_URL$path"
}

upsert_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
      sed -i '' "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
      sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    fi
  else
    echo "${key}=${value}" >> "$ENV_FILE"
  fi
}

# ─── Pre-flight ───────────────────────────────────────────────────────────────

require_cmd curl
require_cmd jq

if grep -q "^ZITADEL_CLIENT_ID=.\+" "$ENV_FILE" 2>/dev/null; then
  STORED_CLIENT_ID=$(grep "^ZITADEL_CLIENT_ID=" "$ENV_FILE" | cut -d= -f2)
  # Validate the stored ID actually exists in the current Zitadel instance
  # Read the PAT first (may not be ready yet — skip check if PAT unavailable)
  if [ -f "$PAT_FILE" ] && [ -s "$PAT_FILE" ]; then
    _STORED_PAT="$(cat "$PAT_FILE")"
    _CHECK=$(curl -s -H "Authorization: Bearer $_STORED_PAT" \
      "$ZITADEL_URL/management/v1/projects/_search" \
      -X POST -H "Content-Type: application/json" -d '{"queries":[{"nameQuery":{"name":"DocintelProject","method":"TEXT_QUERY_METHOD_EQUALS"}}]}' \
      | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('result',[])))" 2>/dev/null || echo "0")
    if [ "$_CHECK" = "1" ]; then
      log "Zitadel already configured and verified (client $STORED_CLIENT_ID exists). Skipping."
      exit 0
    else
      log "Stale ZITADEL_CLIENT_ID in .env (not found in current Zitadel). Re-running setup..."
    fi
  fi
fi

# ─── Wait for admin PAT ───────────────────────────────────────────────────────

echo ""
echo "================================================"
echo "  Configuring Zitadel"
echo "================================================"
echo ""
log "Waiting for Zitadel first-instance init (admin PAT)..."
log "This can take up to 3 minutes on first run..."

MAX_WAIT=180
ELAPSED=0
until [ -f "$PAT_FILE" ] && [ -s "$PAT_FILE" ]; do
  [ $ELAPSED -ge $MAX_WAIT ] && fail "Admin PAT not found after ${MAX_WAIT}s. Check: docker compose logs zitadel-api"
  sleep 5; ELAPSED=$((ELAPSED + 5))
done

PAT="$(cat "$PAT_FILE")"
[ -n "$PAT" ] || fail "Admin PAT file is empty."
ok "Admin PAT ready."

until zapi GET "/debug/healthz" >/dev/null 2>&1; do
  log "Waiting for Zitadel API..."; sleep 3
done
ok "Zitadel API is responding."

# ─── Default org ──────────────────────────────────────────────────────────────

DEFAULT_ORG_ID=$(zapi GET "/management/v1/orgs/me" | jq -r '.org.id')
[ -n "$DEFAULT_ORG_ID" ] && [ "$DEFAULT_ORG_ID" != "null" ] || fail "Could not get default org ID."
ok "Default org ID: $DEFAULT_ORG_ID"

# ─── Get-or-create project ────────────────────────────────────────────────────

log "Ensuring DocintelProject exists..."
CREATE_RESP=$(zapi POST "/management/v1/projects" -d '{
  "name": "DocintelProject",
  "projectRoleCheck": true,
  "hasProjectCheck": false,
  "privateLabelingSetting": "PRIVATE_LABELING_SETTING_UNSPECIFIED"
}')
PROJECT_ID=$(echo "$CREATE_RESP" | jq -r '.id // empty')

if [ -z "$PROJECT_ID" ]; then
  # Already exists — search for it
  SEARCH_RESP=$(zapi POST "/management/v1/projects/_search" -d '{"queries": [{"nameQuery": {"name": "DocintelProject", "method": "TEXT_QUERY_METHOD_EQUALS"}}]}')
  PROJECT_ID=$(echo "$SEARCH_RESP" | jq -r '.result[0].id // empty')
  [ -n "$PROJECT_ID" ] || fail "Failed to create or find project. Create response: $CREATE_RESP"
  ok "Project found (already exists): $PROJECT_ID"
else
  ok "Project created: $PROJECT_ID"
fi

# ─── Create project roles (idempotent — ignore duplicate errors) ──────────────

for role_key in "platform_admin" "tenant_admin" "tenant_user"; do
  display=$(echo "$role_key" | tr '_' ' ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2)); print}')
  zapi POST "/management/v1/projects/$PROJECT_ID/roles" -d "{
    \"roleKey\": \"$role_key\",
    \"displayName\": \"$display\",
    \"group\": \"docintel\"
  }" >/dev/null || true
  ok "Role ensured: $role_key"
done

# ─── Get-or-create SPA application ───────────────────────────────────────────

log "Ensuring DocIntel Web UI OIDC application..."
# Check if an app named "DocIntel Web UI" already exists in the project
EXISTING_APPS=$(zapi POST "/management/v1/projects/$PROJECT_ID/apps/_search" \
  -d '{"queries": [{"nameQuery": {"name": "DocIntel Web UI", "method": "TEXT_QUERY_METHOD_EQUALS"}}]}' 2>/dev/null || echo '{}')
CLIENT_ID=$(echo "$EXISTING_APPS" | jq -r '.result[0].oidcConfig.clientId // empty' 2>/dev/null)

if [ -z "$CLIENT_ID" ]; then
  APP_RESP=$(zapi POST "/management/v1/projects/$PROJECT_ID/apps/oidc" -d '{
    "name": "DocIntel Web UI",
    "redirectUris": ["http://localhost:3001/auth/callback"],
    "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
    "grantTypes": ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE", "OIDC_GRANT_TYPE_REFRESH_TOKEN"],
    "appType": "OIDC_APP_TYPE_USER_AGENT",
    "authMethodType": "OIDC_AUTH_METHOD_TYPE_NONE",
    "postLogoutRedirectUris": ["http://localhost:3001/"],
    "version": "OIDC_VERSION_1_0",
    "devMode": true,
    "accessTokenType": "OIDC_TOKEN_TYPE_JWT",
    "idTokenUserinfoAssertion": false,
    "clockSkew": "0s",
    "additionalOrigins": []
  }')
  CLIENT_ID=$(echo "$APP_RESP" | jq -r '.clientId // empty')
  [ -n "$CLIENT_ID" ] || fail "Failed to create OIDC app. Response: $APP_RESP"
  ok "OIDC app created. clientId: $CLIENT_ID"
else
  ok "OIDC app found (already exists). clientId: $CLIENT_ID"
fi

# ─── Get-or-create organizations ─────────────────────────────────────────────

create_or_get_org() {
  local name="$1"
  local resp org_id
  resp=$(curl -s -X POST \
    -H "Authorization: Bearer $PAT" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$name\"}" \
    "$ZITADEL_URL/v2/organizations")
  org_id=$(echo "$resp" | jq -r '.organizationId // empty')

  if [ -z "$org_id" ]; then
    # Already exists — search for it
    SEARCH=$(curl -s -X POST \
      -H "Authorization: Bearer $PAT" \
      -H "Content-Type: application/json" \
      -d "{\"queries\": [{\"nameQuery\": {\"name\": \"$name\", \"method\": \"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
      "$ZITADEL_URL/v2/organizations/_search")
    org_id=$(echo "$SEARCH" | jq -r '.result[0].id // empty')
    [ -n "$org_id" ] || fail "Failed to create or find org '$name'. Create response: $resp"
    log "Org '$name' found (already exists): $org_id"
  fi
  echo "$org_id"
}

log "Ensuring organizations..."
PLATFORM_ORG_ID=$(create_or_get_org "platform")
ok "Org 'platform': $PLATFORM_ORG_ID"

ALPHA_ORG_ID=$(create_or_get_org "alpha")
ok "Org 'alpha': $ALPHA_ORG_ID"

BETA_ORG_ID=$(create_or_get_org "beta")
ok "Org 'beta': $BETA_ORG_ID"

# ─── Grant project to orgs (idempotent) ───────────────────────────────────────

grant_project_to_org() {
  local org_id="$1" roles="$2"
  zapi POST "/management/v1/projects/$PROJECT_ID/grants" \
    -H "x-zitadel-orgid: $DEFAULT_ORG_ID" \
    -d "{\"grantedOrgId\": \"$org_id\", \"roleKeys\": $roles}" >/dev/null || true
}

grant_project_to_org "$PLATFORM_ORG_ID" '["platform_admin","tenant_admin","tenant_user"]'
ok "Project granted to 'platform' org."
grant_project_to_org "$ALPHA_ORG_ID" '["tenant_admin","tenant_user"]'
ok "Project granted to 'alpha' org."
grant_project_to_org "$BETA_ORG_ID" '["tenant_admin","tenant_user"]'
ok "Project granted to 'beta' org."

# ─── Create users ─────────────────────────────────────────────────────────────

create_user() {
  # Uses v2 API — creates users in USER_STATE_ACTIVE (management v1 creates INITIAL state)
  # Returns userId, or empty on error
  local username="$1" first="$2" last="$3" email="$4" password="$5" org_id="$6"
  curl -s -X POST \
    -H "Authorization: Bearer $PAT" \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"$username\",
      \"organization\": {\"orgId\": \"$org_id\"},
      \"profile\": {\"givenName\": \"$first\", \"familyName\": \"$last\", \"displayName\": \"$first $last\"},
      \"email\": {\"email\": \"$email\", \"isVerified\": true},
      \"password\": {\"password\": \"$password\", \"changeRequired\": false}
    }" \
    "$ZITADEL_URL/v2/users/human" | jq -r '.userId // empty'
}

get_user_id() {
  local username="$1" org_id="$2"
  curl -s -X POST \
    -H "Authorization: Bearer $PAT" \
    -H "Content-Type: application/json" \
    -H "x-zitadel-orgid: $org_id" \
    -d "{\"queries\": [{\"userNameQuery\": {\"userName\": \"$username\", \"method\": \"TEXT_QUERY_METHOD_EQUALS\"}}]}" \
    "$ZITADEL_URL/management/v1/users/_search" | jq -r '.result[0].id // empty'
}

create_or_get_user() {
  local username="$1" first="$2" last="$3" email="$4" password="$5" org_id="$6"
  local uid
  uid=$(create_user "$username" "$first" "$last" "$email" "$password" "$org_id")
  if [ -z "$uid" ]; then
    uid=$(get_user_id "$username" "$org_id")
    [ -n "$uid" ] || fail "Failed to create or find user '$username'."
    log "User '$username' found (already exists): $uid"
  fi
  echo "$uid"
}

grant_role() {
  local user_id="$1" project_id="$2" role_key="$3" org_id="$4"
  curl -s -X POST \
    -H "Authorization: Bearer $PAT" \
    -H "Content-Type: application/json" \
    -H "x-zitadel-orgid: $org_id" \
    -d "{\"projectId\": \"$project_id\", \"roleKeys\": [\"$role_key\"]}" \
    "$ZITADEL_URL/management/v1/users/$user_id/grants" >/dev/null || true
}

log "Ensuring users..."

DIADMIN_ID=$(create_or_get_user "diadmin" "Di" "Admin" "diadmin@platform.local" "Diadmin@123" "$PLATFORM_ORG_ID")
grant_role "$DIADMIN_ID" "$PROJECT_ID" "platform_admin" "$PLATFORM_ORG_ID"
ok "User 'diadmin' (platform_admin, org: platform)"

ALPHAADMIN_ID=$(create_or_get_user "alphaadmin" "Alpha" "Admin" "alphaadmin@alpha.local" "Alphaadmin@123" "$ALPHA_ORG_ID")
grant_role "$ALPHAADMIN_ID" "$PROJECT_ID" "tenant_admin" "$ALPHA_ORG_ID"
ok "User 'alphaadmin' (tenant_admin, org: alpha)"

ALPHAUSER_ID=$(create_or_get_user "alphauser" "Alpha" "User" "alphauser@alpha.local" "Alphauser@123" "$ALPHA_ORG_ID")
grant_role "$ALPHAUSER_ID" "$PROJECT_ID" "tenant_user" "$ALPHA_ORG_ID"
ok "User 'alphauser' (tenant_user, org: alpha)"

BETAADMIN_ID=$(create_or_get_user "betaadmin" "Beta" "Admin" "betaadmin@beta.local" "Betaadmin@123" "$BETA_ORG_ID")
grant_role "$BETAADMIN_ID" "$PROJECT_ID" "tenant_admin" "$BETA_ORG_ID"
ok "User 'betaadmin' (tenant_admin, org: beta)"

BETAUSER_ID=$(create_or_get_user "betauser" "Beta" "User" "betauser@beta.local" "Betauser@123" "$BETA_ORG_ID")
grant_role "$BETAUSER_ID" "$PROJECT_ID" "tenant_user" "$BETA_ORG_ID"
ok "User 'betauser' (tenant_user, org: beta)"

# ─── Create admin-service service account + PAT ───────────────────────────────

log "Ensuring admin-service service account..."
SA_RESP=$(zapi POST "/management/v1/users/machine" -d '{
  "userName": "docintel-admin-sa",
  "name": "DocIntel Admin Service Account",
  "description": "Service account for admin-service tenant management",
  "accessTokenType": "ACCESS_TOKEN_TYPE_JWT"
}')
SA_USER_ID=$(echo "$SA_RESP" | jq -r '.userId // empty')

if [ -z "$SA_USER_ID" ]; then
  SA_USER_ID=$(get_user_id "docintel-admin-sa" "$DEFAULT_ORG_ID")
  [ -n "$SA_USER_ID" ] || fail "Failed to create or find service account."
  log "Service account found (already exists): $SA_USER_ID"
else
  ok "Service account created: $SA_USER_ID"
fi

grant_role "$SA_USER_ID" "$PROJECT_ID" "platform_admin" "$DEFAULT_ORG_ID"

SA_PAT_RESP=$(zapi POST "/management/v1/users/$SA_USER_ID/pats" -d '{"expirationDate": "2099-01-01T00:00:00Z"}')
SA_PAT=$(echo "$SA_PAT_RESP" | jq -r '.token // empty')
[ -n "$SA_PAT" ] || fail "Failed to generate service account PAT. Response: $SA_PAT_RESP"
ok "Service account PAT generated."

# ─── Deploy custom claims Action ──────────────────────────────────────────────

log "Deploying custom claims action..."
ACTION_SCRIPT=$(cat "$PROJECT_DIR/config/zitadel/action-claims.js" | jq -Rs .)

ACTION_RESP=$(zapi POST "/management/v1/actions" -d "{
  \"name\": \"setCustomClaims\",
  \"script\": $ACTION_SCRIPT,
  \"timeout\": \"10s\",
  \"allowedToFail\": true
}")
ACTION_ID=$(echo "$ACTION_RESP" | jq -r '.id // empty')

if [ -z "$ACTION_ID" ]; then
  # Already exists — search for it
  ACTIONS_RESP=$(zapi POST "/management/v1/actions/_search" -d '{}' 2>/dev/null || echo '{}')
  ACTION_ID=$(echo "$ACTIONS_RESP" | jq -r '.result[] | select(.name == "setCustomClaims") | .id' | head -1)
  [ -n "$ACTION_ID" ] || fail "Failed to create or find action. Create response: $ACTION_RESP"
  log "Action found (already exists): $ACTION_ID"
else
  ok "Action created: $ACTION_ID"
fi

# Set triggers: COMPLEMENT_TOKEN (2) / PRE_ACCESS_TOKEN_CREATION (5) + PRE_USERINFO_CREATION (4)
zapi POST "/management/v1/flows/2/trigger/5" -d "{\"actionIds\": [\"$ACTION_ID\"]}" >/dev/null || true
ok "Trigger set: PRE_ACCESS_TOKEN_CREATION"
zapi POST "/management/v1/flows/2/trigger/4" -d "{\"actionIds\": [\"$ACTION_ID\"]}" >/dev/null || true
ok "Trigger set: PRE_USERINFO_CREATION"

# ─── Branding ─────────────────────────────────────────────────────────────────
# Colors wired via API; background image must be uploaded manually (no upload API in v4).

log "Applying DocIntel branding..."

zapi PUT "/admin/v1/policies/label" -d '{
  "primaryColor":      "#10b981",
  "warnColor":         "#ef4444",
  "backgroundColor":   "#f0faf6",
  "fontColor":         "#0f172a",
  "primaryColorDark":  "#10b981",
  "backgroundColorDark": "#070d14",
  "fontColorDark":     "#e2e8f0",
  "warnColorDark":     "#ef4444",
  "disableWatermark":  true,
  "themeMode":         "THEME_MODE_AUTO"
}' >/dev/null
zapi POST "/admin/v1/policies/label/_activate" -d '{}' >/dev/null
ok "Branding colors applied (emerald primary, dark #070d14)."

# Generate starfield background PNG (DocIntel dark-mode background) for manual upload
BRANDING_DIR="$PROJECT_DIR/config/zitadel/branding"
BG_PNG="$BRANDING_DIR/background-dark.png"
mkdir -p "$BRANDING_DIR"
if [ ! -f "$BG_PNG" ]; then
  python3 - "$BG_PNG" << 'PYEOF'
import struct, zlib, random, math, sys
def make_png(w, h, px):
    def crc(d): return struct.pack('>I', zlib.crc32(d) & 0xffffffff)
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + crc(t + d)
    rows = b''.join(b'\x00' + bytes(px[y*w*3:(y+1)*w*3]) for y in range(h))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9))
            + chunk(b'IEND', b''))
out = sys.argv[1]
W, H = 1920, 1080
px = [7, 13, 20] * (W * H)
for y in range(H):
    for x in range(W):
        d1 = math.sqrt((x/W)**2 + (y/H)**2)
        d2 = math.sqrt(((W-x)/W)**2 + ((H-y)/H)**2)
        glow = max(0, 0.06 - d1*0.09) + max(0, 0.05 - d2*0.07)
        if glow > 0:
            i = (y*W+x)*3; px[i+1]=int(min(255,px[i+1]+glow*111)); px[i+2]=int(min(255,px[i+2]+glow*39))
random.seed(42)
for _ in range(800):
    x,y=random.randint(0,W-1),random.randint(0,H-1)
    b=random.randint(100,255); sz=random.choices([1,2,3],[80,15,5])[0]
    r,g,bl=b,min(255,b+random.randint(0,10)),min(255,b+random.randint(0,20))
    for dy in range(-sz+1,sz):
        for dx in range(-sz+1,sz):
            nx,ny=x+dx,y+dy
            if 0<=nx<W and 0<=ny<H:
                f=max(0,1-math.sqrt(dx*dx+dy*dy)/sz); i=(ny*W+nx)*3
                px[i]=min(255,px[i]+int(r*f)); px[i+1]=min(255,px[i+1]+int(g*f)); px[i+2]=min(255,px[i+2]+int(bl*f))
with open(out, 'wb') as f: f.write(make_png(W, H, px))
PYEOF
  ok "Starfield PNG generated: config/zitadel/branding/background-dark.png"
else
  ok "Starfield PNG already exists."
fi

# ─── Write config to .env ─────────────────────────────────────────────────────

log "Writing configuration to .env..."
[ -f "$ENV_FILE" ] || cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
upsert_env "ZITADEL_CLIENT_ID" "$CLIENT_ID"
upsert_env "ZITADEL_PROJECT_ID" "$PROJECT_ID"
upsert_env "ZITADEL_SERVICE_ACCOUNT_PAT" "$SA_PAT"
ok ".env updated."

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "================================================"
echo "  Zitadel Setup Complete"
echo "================================================"
echo ""
echo "  Zitadel UI:    http://localhost:9090  (login: zitadel-admin@docintel.localhost)"
echo ""
echo "  Project ID:    $PROJECT_ID"
echo "  Client ID:     $CLIENT_ID"
echo ""
echo "  Users:"
echo "    diadmin     / Diadmin@123     — Platform Admin    (org: platform)"
echo "    alphaadmin  / Alphaadmin@123  — Alpha Tenant Admin (org: alpha)"
echo "    alphauser   / Alphauser@123   — Alpha Tenant User  (org: alpha)"
echo "    betaadmin   / Betaadmin@123   — Beta Tenant Admin  (org: beta)"
echo "    betauser    / Betauser@123    — Beta Tenant User   (org: beta)"
echo ""
echo "  Branding:"
echo "    Colors applied (emerald #10b981, dark bg #070d14)."
echo "    Background image — no upload API in Zitadel v4. To apply:"
echo "      1. http://localhost:9090 → Instance Settings → Branding"
echo "      2. Dark mode → Background → upload config/zitadel/branding/background-dark.png"
echo "      3. Click 'Apply settings'"
echo ""
echo "  Restarting web-ui to pick up CLIENT_ID..."
docker compose restart web-ui 2>/dev/null || true
echo ""
