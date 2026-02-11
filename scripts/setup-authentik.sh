#!/bin/bash
# ==============================================================================
# Authentik Setup Verification for DocIntel
# ==============================================================================
# The actual provisioning is done by the blueprint YAML at:
#   config/authentik/blueprints/docintel-setup.yaml
#
# This script:
#   1. Waits for Authentik to be fully ready
#   2. Waits for the blueprint to be applied successfully
#   3. Verifies the OAuth2 configuration is working
#   4. Prints credentials
#
# Usage: ./scripts/setup-authentik.sh
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

AUTHENTIK_URL="${AUTHENTIK_URL:-http://localhost:9090}"
TOKEN="${AUTHENTIK_BOOTSTRAP_TOKEN:-docintel-api-token-change-in-prod}"
DEFAULT_PASSWORD="DocIntel@123"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[>>]${NC} $1"; }

api() { curl -s -H "Authorization: Bearer $TOKEN" "${AUTHENTIK_URL}$1"; }

echo ""
echo "================================================"
echo "  DocIntel - Authentik Setup"
echo "================================================"
echo ""

# ==========================================================================
# 1. Wait for Authentik API to be ready
# ==========================================================================
log_step "Waiting for Authentik API..."
for i in $(seq 1 90); do
    if api "/api/v3/root/config/" > /dev/null 2>&1; then
        log_ok "Authentik API is ready"
        break
    fi
    [ "$i" -eq 90 ] && { log_err "Authentik did not start in time"; exit 1; }
    echo -n "."
    sleep 2
done

# ==========================================================================
# 2. Wait for blueprint to be applied
# ==========================================================================
log_step "Waiting for DocIntel blueprint to apply..."
BLUEPRINT_OK=false
for i in $(seq 1 30); do
    STATUS=$(api "/api/v3/managed/blueprints/" | \
        python3 -c "
import sys,json
d=json.load(sys.stdin)
for b in d.get('results',[]):
    if b['name'] == 'DocIntel Setup':
        print(b['status'])
        break
else:
    print('not_found')
" 2>/dev/null || echo "error")

    case "$STATUS" in
        successful)
            log_ok "Blueprint applied successfully"
            BLUEPRINT_OK=true
            break
            ;;
        not_found)
            echo -n "."
            ;;
        error)
            log_warn "Blueprint status: error (attempt $i/30, retrying...)"
            # Trigger re-apply
            BLUEPRINT_PK=$(api "/api/v3/managed/blueprints/" | \
                python3 -c "import sys,json; d=json.load(sys.stdin); print([b['pk'] for b in d['results'] if b['name']=='DocIntel Setup'][0])" 2>/dev/null || echo "")
            if [ -n "$BLUEPRINT_PK" ]; then
                curl -s -X POST -H "Authorization: Bearer $TOKEN" \
                    "${AUTHENTIK_URL}/api/v3/managed/blueprints/${BLUEPRINT_PK}/apply/" > /dev/null 2>&1
            fi
            ;;
        *)
            echo -n "."
            ;;
    esac
    sleep 3
done

if [ "$BLUEPRINT_OK" != "true" ]; then
    log_err "Blueprint did not apply successfully after 90 seconds."
    log_err "Check: docker logs docintel-authentik-worker-1"
    exit 1
fi

# ==========================================================================
# 3. Verify OAuth2 endpoint is working
# ==========================================================================
log_step "Verifying OAuth2 configuration..."
ISSUER=$(curl -s "${AUTHENTIK_URL}/application/o/docintel/.well-known/openid-configuration" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('issuer',''))" 2>/dev/null || echo "")

if [ -n "$ISSUER" ]; then
    log_ok "OIDC discovery endpoint verified: $ISSUER"
else
    log_err "OIDC endpoint not responding. OAuth app may not have been created."
    exit 1
fi

# ==========================================================================
# 4. Done
# ==========================================================================
echo ""
echo "================================================"
echo "  Setup Complete"
echo "================================================"
echo ""
echo "Authentik Admin:"
echo "  URL:       ${AUTHENTIK_URL}/if/admin/"
echo "  User:      akadmin"
echo "  Password:  ${AUTHENTIK_ADMIN_PASSWORD:-DocIntel@123}"
echo ""
echo "Demo Users (password: ${DEFAULT_PASSWORD}):"
echo "  demo-admin   (tenant: default)"
echo "  demo-user    (tenant: default)"
echo "  tenant-user  (tenant: demo)"
echo ""
echo "OAuth2:"
echo "  Client ID:  docintel"
echo "  Issuer:     ${AUTHENTIK_URL}/application/o/docintel/"
echo ""
