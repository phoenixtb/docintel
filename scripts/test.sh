#!/bin/bash
# ==============================================================================
# DocIntel Test Runner
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

ACTIONS=(
    "all"
    "document-all"
    "document-unit"
    "document-integration"
    "document-messaging"
    "ingestion-all"
    "data-loader-all"
    "common-all"
    "contract"
    "e2e"
    "quit"
)

LABELS=(
    "All                Run all unit + integration tests (all services)"
    "Document: All      All document-service tests"
    "Document: Unit     DocumentServiceTest only (no Docker required)"
    "Document: Integr.  Controller + Repository + Storage (needs Docker)"
    "Document: Msg      StreamConsumerTest (MockK, no Docker required)"
    "Ingestion: All     All ingestion-service pytest tests"
    "Data-Loader: All   All data-loader pytest tests"
    "Common: All        docintel-common library tests"
    "Contract           API contract tests (no services required)"
    "E2E                ⚠ RAG quality tests — full stack + Zitadel + ingested docs required"
    "Quit"
)

cursor_to()  { printf "\033[%s;0H" "$1"; }
clear_line() { printf "\033[2K"; }

SAVED_TTY=$(stty -g 2>/dev/null)
cleanup() {
    stty "$SAVED_TTY" 2>/dev/null
    printf "\033[?25h"
    echo ""
}
trap cleanup EXIT INT TERM

cursor=0

draw_menu() {
    local start_row=$1
    for i in "${!ACTIONS[@]}"; do
        cursor_to $((start_row + i))
        clear_line
        if [[ $i -eq $cursor ]]; then
            printf "  ${CYAN}▸ ${BOLD}%s${NC}\n" "${LABELS[$i]}"
        else
            if [[ "${ACTIONS[$i]}" == "quit" ]]; then
                printf "    ${DIM}%s${NC}\n" "${LABELS[$i]}"
            else
                printf "    %s\n" "${LABELS[$i]}"
            fi
        fi
    done
    cursor_to $((start_row + ${#ACTIONS[@]} + 1))
    clear_line
    printf "  ${DIM}↑↓ navigate • enter select • q quit${NC}"
}

clear
echo ""
echo -e "  ${BOLD}DocIntel Test Runner${NC}"
echo -e "  ${DIM}Select which tests to run${NC}"
echo ""

start_row=5
printf "\033[?25l"
stty -echo -icanon min 1 time 0 2>/dev/null

draw_menu $start_row

while IFS= read -r -n1 -s key; do
    if [[ "$key" == $'\x1b' ]]; then
        IFS= read -r -n2 -s -t 1 seq
        case "$seq" in
            '[A') ((cursor > 0)) && ((cursor--)) ;;
            '[B') ((cursor < ${#ACTIONS[@]} - 1)) && ((cursor++)) ;;
        esac
    elif [[ "$key" == '' ]]; then
        break
    elif [[ "$key" == 'q' || "$key" == 'Q' ]]; then
        cursor_to $((start_row + ${#ACTIONS[@]} + 3))
        exit 0
    fi
    draw_menu $start_row
done

cursor_to $((start_row + ${#ACTIONS[@]} + 3))
action="${ACTIONS[$cursor]}"

stty "$SAVED_TTY" 2>/dev/null
printf "\033[?25h"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

run_document_tests() {
    local filter="$1"
    echo -e "${BOLD}Running document-service tests...${NC}"
    echo -e "${DIM}Reports: services/document-service/build/reports/tests/test/index.html${NC}"
    echo ""
    if [[ -n "$filter" ]]; then
        (cd "$PROJECT_DIR/services/document-service" && ./gradlew test --tests "$filter")
    else
        (cd "$PROJECT_DIR/services/document-service" && ./gradlew test)
    fi
}

_run_python_tests() {
    local label="$1"
    local svc="$2"
    local test_dir="$3"
    local python_bin="${4:-python3}"

    echo -e "${BOLD}Running $label tests...${NC}"
    echo ""
    local venv="$svc/.venv"

    if [[ ! -d "$venv" ]]; then
        echo "  Creating venv..."
        "$python_bin" -m venv "$venv"
    fi
    if [[ ! -f "$venv/.deps-installed" ]] || \
       [[ "$svc/pyproject.toml" -nt "$venv/.deps-installed" ]]; then
        echo "  Installing dependencies..."
        "$venv/bin/pip" install -q -e "$svc[dev]"
        touch "$venv/.deps-installed"
    fi

    "$venv/bin/pytest" "$test_dir" -v
}

run_ingestion_tests() {
    local svc="$PROJECT_DIR/services/ingestion-service"
    # docling-haystack 0.1.1 requires Python <3.13 — use python3.12 explicitly
    _run_python_tests "ingestion-service" "$svc" "$svc/tests/" "python3.12"
}

run_data_loader_tests() {
    local svc="$PROJECT_DIR/services/data-loader"
    _run_python_tests "data-loader" "$svc" "$svc/tests/"
}

run_common_tests() {
    local svc="$PROJECT_DIR/lib/docintel-common"
    _run_python_tests "docintel-common" "$svc" "$svc/tests/"
}

run_contract_tests() {
    echo -e "${BOLD}Running contract tests...${NC}"
    echo ""
    python3 -m pytest "$PROJECT_DIR/tests/contract/" -v
}

print_result() {
    local code=$?
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [[ $code -eq 0 ]]; then
        echo -e "  ${GREEN}${BOLD}✓ All tests passed${NC}"
    else
        echo -e "  ${RED}${BOLD}✗ Some tests failed${NC}"
    fi
    echo ""
}

case "$action" in
    all)
        run_document_tests ""
        run_ingestion_tests
        run_data_loader_tests
        run_common_tests
        run_contract_tests
        print_result
        ;;
    document-all)
        run_document_tests ""
        print_result
        ;;
    document-unit)
        run_document_tests "com.docintel.document.service.DocumentServiceTest"
        print_result
        ;;
    document-integration)
        echo -e "${BOLD}Running document-service integration tests...${NC}"
        echo -e "${DIM}Reports: services/document-service/build/reports/tests/test/index.html${NC}"
        echo ""
        (cd "$PROJECT_DIR/services/document-service" && ./gradlew test \
            --tests "com.docintel.document.controller.*" \
            --tests "com.docintel.document.repository.*" \
            --tests "com.docintel.document.service.StorageServiceTest")
        print_result
        ;;
    document-messaging)
        run_document_tests "com.docintel.document.messaging.*"
        print_result
        ;;
    ingestion-all)
        run_ingestion_tests
        print_result
        ;;
    data-loader-all)
        run_data_loader_tests
        print_result
        ;;
    common-all)
        run_common_tests
        print_result
        ;;
    contract)
        run_contract_tests
        print_result
        ;;
    e2e)
        CHECK_URL="http://localhost:8080/actuator/health"
        CHECK_LABEL="API Gateway (port 8080)"

        echo -e "${YELLOW}${BOLD}⚠  E2E: requires full stack running + documents ingested.${NC}"
        echo ""

        # Check if the gateway is reachable
        if ! curl -sf --max-time 5 "$CHECK_URL" > /dev/null 2>&1; then
            echo -e "${RED}${BOLD}✗ $CHECK_LABEL is not reachable.${NC}"
            echo ""
            echo -e "  Would you like to start the stack now? ${DIM}(this may take several minutes)${NC}"
            printf "  [y/N] "
            read -r answer
            if [[ "$answer" =~ ^[Yy]$ ]]; then
                echo ""
                "$SCRIPT_DIR/start.sh"
                echo ""
                if ! curl -sf --max-time 10 "$CHECK_URL" > /dev/null 2>&1; then
                    echo -e "${RED}Stack still not reachable after start. Aborting E2E.${NC}"
                    exit 1
                fi
            else
                echo ""
                echo -e "  Run ${BOLD}./scripts/docintel.sh → Start${NC} then retry."
                exit 1
            fi
        else
            echo -e "  ${GREEN}✓${NC} $CHECK_LABEL is reachable."
        fi

        echo ""

        GENERATED_ENV="$PROJECT_DIR/config/zitadel/generated.env"
        if [[ -f "$GENERATED_ENV" ]]; then
            set -a; source "$GENERATED_ENV"; set +a
            echo -e "  ${GREEN}✓${NC} Loaded ZITADEL_CLIENT_ID + ZITADEL_SERVICE_ACCOUNT_PAT from generated.env"
        else
            echo -e "  ${YELLOW}⚠  config/zitadel/generated.env not found — ensure ZITADEL_CLIENT_ID and ZITADEL_SERVICE_ACCOUNT_PAT are set.${NC}"
        fi
        echo ""

        cd "$PROJECT_DIR/tests/integration"

        # Ensure venv exists and deps are installed
        if [[ ! -d ".venv" ]]; then
            echo "  Creating venv for integration tests..."
            python3 -m venv .venv
        fi
        if [[ ! -f ".venv/.deps-installed" ]] || \
           [[ requirements.txt -nt ".venv/.deps-installed" ]]; then
            echo "  Installing dependencies..."
            .venv/bin/pip install -q -r requirements.txt
            touch .venv/.deps-installed
        fi

        .venv/bin/python run_tests.py
        print_result
        ;;
    quit)
        exit 0
        ;;
esac
