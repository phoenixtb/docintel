#!/bin/bash
# ==============================================================================
# DocIntel Test Runner
# ==============================================================================
# Interactive selector over every module's test suite. Each suite's result is
# recorded and summarised; the run exits non-zero if any suite failed.
#
# Usage:
#   ./scripts/test.sh          # interactive menu
#   ./scripts/test.sh all      # every unit + integration suite, no menu
#   ./scripts/test.sh <action> # any action key listed in ACTIONS below
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ACTIONS=(
    "all"
    "document-all"
    "document-unit"
    "document-integration"
    "document-messaging"
    "admin-all"
    "gateway-all"
    "rag-all"
    "ingestion-all"
    "data-loader-all"
    "analytics-all"
    "common-all"
    "contract"
    "e2e"
    "quit"
)

LABELS=(
    "All                Every unit + integration suite below (not E2E)"
    "Document: All      All document-service tests (needs Docker)"
    "Document: Unit     DocumentServiceTest only (no Docker required)"
    "Document: Integr.  Controller + Repository + Storage (needs Docker)"
    "Document: Msg      StreamConsumerTest (MockK, no Docker required)"
    "Admin: All         All admin-service tests (needs Docker)"
    "Gateway: All       All api-gateway tests"
    "RAG: All           All rag-service pytest tests"
    "Ingestion: All     All ingestion-service pytest tests"
    "Data-Loader: All   All data-loader pytest tests"
    "Analytics: All     All analytics-service-py pytest tests"
    "Common: All        docintel-common tests (object store needs Docker)"
    "Contract           Cross-service contract tests (no services required)"
    "E2E                ⚠ RAG quality tests — full stack + Zitadel + ingested docs required"
    "Quit"
)

# ── Kotlin: Gradle 8.11 cannot start on JDK 25+, the toolchain is 21 ─────────
# Prefer an explicit JAVA_HOME that is 21, then the platform registry, then the
# usual install locations (incl. Gradle-provisioned JDKs).
_is_jdk21() { "$1" -version 2>&1 | grep -qE 'version "21[."]'; }

gradle_java_home() {
    local java_bin cand
    if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ] && _is_jdk21 "$JAVA_HOME/bin/java"; then
        echo "$JAVA_HOME"; return 0
    fi
    if [ -x /usr/libexec/java_home ] && cand=$(/usr/libexec/java_home -v 21 2>/dev/null); then
        echo "$cand"; return 0
    fi
    for java_bin in /usr/lib/jvm/*/bin/java \
                    "$HOME"/.sdkman/candidates/java/*/bin/java \
                    "$HOME"/.gradle/jdks/*/bin/java \
                    "$HOME"/.gradle/jdks/*/Contents/Home/bin/java \
                    "$HOME"/.gradle/jdks/*/*/Contents/Home/bin/java; do
        if [ -x "$java_bin" ] && _is_jdk21 "$java_bin"; then
            dirname "$(dirname "$java_bin")"; return 0
        fi
    done
    return 1
}

run_gradle_tests() {
    local service="$1"; shift
    local jh
    if ! jh=$(gradle_java_home); then
        echo -e "${RED}JDK 21 not found (Gradle 8.11 cannot run on newer JDKs). Install a JDK 21 or set JAVA_HOME.${NC}"
        return 1
    fi
    echo -e "${DIM}JDK: $jh${NC}"
    echo -e "${DIM}Reports: services/$service/build/reports/tests/test/index.html${NC}"
    (cd "$PROJECT_DIR/services/$service" && JAVA_HOME="$jh" ./gradlew test "$@")
}

# ── Python ────────────────────────────────────────────────────────────────────
run_pytest() {
    local dir="$1" tests="${2:-tests/}"
    if ! command -v uv &>/dev/null; then
        echo -e "${RED}uv not found. Install: https://docs.astral.sh/uv/${NC}"
        return 1
    fi
    echo "  Syncing deps (uv)..."
    (cd "$PROJECT_DIR/$dir" && uv sync --python 3.12 --extra dev -q && uv run --no-sync pytest "$tests")
}

# ── Result bookkeeping ────────────────────────────────────────────────────────
RESULTS=()

# run_suite <label> <command...> — runs one suite, records PASS/FAIL, never aborts the run.
run_suite() {
    local label="$1"; shift
    echo ""
    echo -e "${BOLD}━━ ${label} ━━${NC}"
    if "$@"; then
        RESULTS+=("PASS  $label")
    else
        RESULTS+=("FAIL  $label")
    fi
}

print_summary() {
    local line failed=0
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    for line in "${RESULTS[@]}"; do
        case "$line" in
            PASS*) echo -e "  ${GREEN}✓${NC} ${line#PASS  }" ;;
            *)     echo -e "  ${RED}✗${NC} ${line#FAIL  }"; failed=1 ;;
        esac
    done
    echo ""
    return $failed
}

run_e2e() {
    local check_url="http://localhost:8080/actuator/health"
    echo -e "${YELLOW}${BOLD}⚠  E2E: requires full stack running + documents ingested.${NC}"
    if ! curl -sf --max-time 5 "$check_url" > /dev/null 2>&1; then
        echo -e "${RED}✗ API Gateway (port 8080) is not reachable.${NC} Run ./scripts/docintel.sh → Start, then retry."
        return 1
    fi
    echo -e "  ${GREEN}✓${NC} API Gateway reachable."
    # The harness authenticates with the E2E service account key start.sh writes.
    if [ ! -f "$PROJECT_DIR/config/zitadel/e2e-sa-key.json" ]; then
        echo -e "${RED}✗ config/zitadel/e2e-sa-key.json missing — re-run ./scripts/start.sh to generate it.${NC}"
        return 1
    fi
    echo -e "  ${GREEN}✓${NC} E2E service-account key present."
    if ! command -v uv &>/dev/null; then
        echo -e "${RED}uv not found. Install: https://docs.astral.sh/uv/${NC}"
        return 1
    fi
    (cd "$PROJECT_DIR/tests/integration" && uv sync --python 3.12 --extra dev -q && uv run --no-sync python run_tests.py)
}

run_action() {
    case "$1" in
        all)
            run_suite "document-service"   run_gradle_tests document-service
            run_suite "admin-service"      run_gradle_tests admin-service
            run_suite "api-gateway"        run_gradle_tests api-gateway
            run_suite "rag-service"        run_pytest services/rag-service
            run_suite "ingestion-service"  run_pytest services/ingestion-service
            run_suite "data-loader"        run_pytest services/data-loader
            run_suite "analytics-service"  run_pytest services/analytics-service-py
            run_suite "docintel-common"    run_pytest lib/docintel-common
            run_suite "contract"           run_pytest lib/docintel-common "$PROJECT_DIR/tests/contract/"
            ;;
        document-all)         run_suite "document-service"             run_gradle_tests document-service ;;
        document-unit)        run_suite "document-service (unit)"      run_gradle_tests document-service --tests "com.docintel.document.service.DocumentServiceTest" ;;
        document-integration) run_suite "document-service (integr.)"   run_gradle_tests document-service \
                                  --tests "com.docintel.document.controller.*" \
                                  --tests "com.docintel.document.repository.*" \
                                  --tests "com.docintel.document.service.StorageServiceTest" ;;
        document-messaging)   run_suite "document-service (messaging)" run_gradle_tests document-service --tests "com.docintel.document.messaging.*" ;;
        admin-all)            run_suite "admin-service"     run_gradle_tests admin-service ;;
        gateway-all)          run_suite "api-gateway"       run_gradle_tests api-gateway ;;
        rag-all)              run_suite "rag-service"       run_pytest services/rag-service ;;
        ingestion-all)        run_suite "ingestion-service" run_pytest services/ingestion-service ;;
        data-loader-all)      run_suite "data-loader"       run_pytest services/data-loader ;;
        analytics-all)        run_suite "analytics-service" run_pytest services/analytics-service-py ;;
        common-all)           run_suite "docintel-common"   run_pytest lib/docintel-common ;;
        contract)             run_suite "contract"          run_pytest lib/docintel-common "$PROJECT_DIR/tests/contract/" ;;
        e2e)                  run_suite "e2e"               run_e2e ;;
        quit)                 exit 0 ;;
        *)
            echo -e "${RED}Unknown action: $1${NC}. Valid: ${ACTIONS[*]}" >&2
            exit 2
            ;;
    esac
}

if [ -n "${1:-}" ]; then
    action="$1"
else
    # shellcheck source=lib/menu.sh
    source "$SCRIPT_DIR/lib/menu.sh"
    echo ""
    echo -e "  ${BOLD}DocIntel Test Runner${NC}"
    echo ""
    menu_select action "Select which tests to run" ACTIONS LABELS 0 --cancel || exit 0
fi

run_action "$action"
print_summary
