```bash
# ─── Build ───────────────────────────────────────────────────────────
docker compose -f tests/docker-compose.test.yml build

# ─── Run all tests ──────────────────────────────────────────────────
docker compose -f tests/docker-compose.test.yml run --rm cortexvid-tests

# ─── Run only Opus planner tests ────────────────────────────────────
docker compose -f tests/docker-compose.test.yml --profile opus run --rm opus-planner-tests

# ─── Run only observability tests ───────────────────────────────────
docker compose -f tests/docker-compose.test.yml --profile observability run --rm observability-tests

# ─── Run with Opik dashboard ────────────────────────────────────────
docker compose -f tests/docker-compose.test.yml --profile opik up --build
# Then open http://localhost:5173 → project "cortexvid-tests"

# ─── Cleanup ────────────────────────────────────────────────────────
docker compose -f tests/docker-compose.test.yml down --rmi local
```

