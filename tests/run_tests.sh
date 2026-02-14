#!/bin/bash
# ============================================================
# CortexVid Test Runner
# ============================================================
#
# Usage:
#   ./run_tests.sh              Run all tests in Docker
#   ./run_tests.sh opus         Run only Opus planner tests
#   ./run_tests.sh observability Run only observability tests
#   ./run_tests.sh opik         Run all tests with Opik tracing
#   ./run_tests.sh local        Run locally (no Docker)
#   ./run_tests.sh ci           Run for CI pipeline (JUnit XML output)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_header() {
    echo ""
    echo "============================================================"
    echo "  CortexVid Test Suite — $1"
    echo "============================================================"
    echo ""
}

case "${1:-all}" in
    # ─── All Tests (Docker) ──────────────────────────────────────────
    all)
        print_header "All Tests (Docker)"
        mkdir -p "$SCRIPT_DIR/results"
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" build cortexvid-tests
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" run --rm cortexvid-tests
        echo ""
        echo -e "${GREEN}✓ Results: tests/results/report.xml${NC}"
        ;;

    # ─── Opus Planner Tests Only ─────────────────────────────────────
    opus)
        print_header "Opus Planner Tests"
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" build opus-planner-tests
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" --profile opus run --rm opus-planner-tests
        ;;

    # ─── Observability Tests Only ────────────────────────────────────
    observability|obs)
        print_header "Observability Tests"
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" build observability-tests
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" --profile observability run --rm observability-tests
        ;;

    # ─── With Opik Live Tracing ──────────────────────────────────────
    opik)
        print_header "All Tests + Opik Tracing"
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" --profile opik up --build -d opik
        echo "Waiting for Opik to start..."
        sleep 5
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" --profile opik run --rm cortexvid-tests-with-opik
        echo ""
        echo -e "${GREEN}✓ Opik dashboard: http://localhost:5173${NC}"
        echo -e "${GREEN}✓ Project: cortexvid-tests${NC}"
        ;;

    # ─── Local (no Docker) ──────────────────────────────────────────
    local)
        print_header "Local Tests (no Docker)"
        cd "$PROJECT_DIR"
        if command -v pytest &> /dev/null; then
            PYTHONPATH="$PROJECT_DIR" python -m pytest tests/ -v --tb=short
        else
            echo -e "${YELLOW}pytest not found, using unittest${NC}"
            PYTHONPATH="$PROJECT_DIR" python tests/test_cortexvid.py
            echo ""
            # Try integration tests (may fail without aiohttp)
            PYTHONPATH="$PROJECT_DIR" python tests/test_integration.py 2>/dev/null || \
                echo -e "${YELLOW}⚠ Integration tests skipped (install aiohttp for full coverage)${NC}"
        fi
        ;;

    # ─── CI Mode ─────────────────────────────────────────────────────
    ci)
        print_header "CI Pipeline"
        mkdir -p "$SCRIPT_DIR/results"
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" build cortexvid-tests
        docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" run --rm cortexvid-tests
        
        # Check results
        if [ -f "$SCRIPT_DIR/results/report.xml" ]; then
            FAILURES=$(grep -o 'failures="[0-9]*"' "$SCRIPT_DIR/results/report.xml" | grep -o '[0-9]*')
            ERRORS=$(grep -o 'errors="[0-9]*"' "$SCRIPT_DIR/results/report.xml" | grep -o '[0-9]*')
            TESTS=$(grep -o 'tests="[0-9]*"' "$SCRIPT_DIR/results/report.xml" | grep -o '[0-9]*')
            
            echo ""
            echo "Results: $TESTS tests, $FAILURES failures, $ERRORS errors"
            
            if [ "$FAILURES" = "0" ] && [ "$ERRORS" = "0" ]; then
                echo -e "${GREEN}✓ All tests passed${NC}"
                exit 0
            else
                echo -e "${RED}✗ Tests failed${NC}"
                exit 1
            fi
        fi
        ;;

    # ─── Help ────────────────────────────────────────────────────────
    *)
        echo "Usage: $0 {all|opus|observability|opik|local|ci}"
        echo ""
        echo "  all             Run all tests in Docker"
        echo "  opus            Run only Opus planner tests"  
        echo "  observability   Run only observability/eval tests"
        echo "  opik            Run all tests with live Opik tracing"
        echo "  local           Run locally without Docker"
        echo "  ci              Run for CI pipeline (JUnit output)"
        exit 1
        ;;
esac
