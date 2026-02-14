#!/bin/bash
# CortexOS post-edit hook
# Fires after any file edit in cortex_on/
# Catches relative imports, syntax errors, auto-runs tests

set -e

EDITED_FILE="${1:-}"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTS_DIR="$PROJECT_ROOT/cortex_on/agents"

echo "─── CortexOS post-edit check ───"

# Check 1: Relative imports in agents/
VIOLATIONS=$(grep -rn "from \.\." "$AGENTS_DIR"/*.py 2>/dev/null | grep -v __pycache__ || true)
if [ -n "$VIOLATIONS" ]; then
    echo "⚠️  RELATIVE IMPORTS DETECTED (will break auto-discovery):"
    echo "$VIOLATIONS"
    echo ""
    echo "Fix: Replace 'from ..' with absolute imports:"
    echo "  from ..config  →  from config"
    echo "  from ..models  →  from models"
    exit 1
fi

# Single-dot relative imports
SINGLE_DOT=$(grep -rn "from \." "$AGENTS_DIR"/*.py 2>/dev/null | grep -v __pycache__ | grep -v "from agents\." | grep -v "from config" | grep -v "from models" | grep -v "from vision" || true)
if [ -n "$SINGLE_DOT" ]; then
    echo "⚠️  SINGLE-DOT RELATIVE IMPORTS:"
    echo "$SINGLE_DOT"
    echo "Fix: Replace 'from .module' with 'from agents.module'"
    exit 1
fi

echo "  ✓ No relative imports"

# Check 2: register_routes pattern for new agents
if [ -n "$EDITED_FILE" ] && [[ "$EDITED_FILE" == *"cortex_on/agents/"* ]] && [[ "$EDITED_FILE" == *".py" ]]; then
    BASENAME=$(basename "$EDITED_FILE" .py)
    if [[ "$BASENAME" != "__init__" ]] && \
       [[ "$BASENAME" != "base_agent" ]] && \
       [[ "$BASENAME" != "parallel_client" ]]; then
        if ! grep -q "def register_routes" "$EDITED_FILE" 2>/dev/null; then
            echo "💡 Tip: $BASENAME has no register_routes(app). Add one for auto-discovery."
        fi
    fi
fi

# Check 3: Python syntax check
if [ -n "$EDITED_FILE" ] && [[ "$EDITED_FILE" == *".py" ]]; then
    if ! python3 -c "import py_compile; py_compile.compile('$EDITED_FILE', doraise=True)" 2>/dev/null; then
        echo "⚠️  SYNTAX ERROR in $EDITED_FILE"
        python3 -m py_compile "$EDITED_FILE" 2>&1
        exit 1
    fi
    echo "  ✓ Syntax OK: $(basename "$EDITED_FILE")"
fi

# Check 4: If test file changed, run tests
if [ -n "$EDITED_FILE" ] && [[ "$EDITED_FILE" == *"test_"* ]]; then
    echo "  Running tests..."
    cd "$PROJECT_ROOT"
    python3 -m pytest "$EDITED_FILE" -x -q 2>&1 || echo "⚠️  Tests failed"
fi

echo "─── post-edit: all checks passed ───"
