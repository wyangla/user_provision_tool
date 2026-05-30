# Testing

The test suite has three layers:

```
Integration (bash)   tests/test_integration.sh     12 tests
  └─ full Docker round-trip: build image → start API → register → rebuild → remove

E2E (pytest)         tests/test_e2e.py              32 tests
  └─ exercise CLI scripts end-to-end against real files, no Docker

Unit (pytest)        tests/test_unit.py             30 tests
  └─ individual lib/ functions in isolation, all I/O mocked
```

---

## Unit Tests

**File:** `tests/test_unit.py`  
**Covers:** `validation`, `registry`, `template_engine`, `auth`, `docker_ops`

Run:
```bash
python -m pytest tests/test_unit.py -v
```

Notable patterns:
- `registry` tests use the `registry_file` fixture (monkeypatched temp file).
- `docker_ops` tests mock `subprocess.run` to avoid real Docker calls.
- `template_engine` tests render against fixture templates in `tests/fixtures/`.

---

## E2E Tests

**File:** `tests/test_e2e.py`  
**Covers:** all four `cli/` scripts invoked via `subprocess` against real temp directories.

Run:
```bash
python -m pytest tests/test_e2e.py -v
```

Notable patterns:
- `docker_ops` is patched so no containers are started.
- Tests verify file generation, registry writes, and stdout/stderr.
- Import paths use `import cli.register as reg_script` etc.

---

## Integration Tests

**File:** `tests/test_integration.sh`  
**Requires:** Docker, `curl`; `jq` optional (falls back to Python).

Runs the full end-to-end cycle against a real Docker daemon:

```
Build provision-api image
  └─ Start provision-api container
       └─ Wait for /health
            ├─ GET  /users                        → empty list
            ├─ POST /users                        → register testuser/myapp/0
            ├─ POST /users (duplicate)            → 409
            ├─ docker ps                          → web + db containers running
            ├─ GET  /users/testuser               → 1 healthy service
            ├─ POST .../rebuild                   → rebuilt
            ├─ docker ps                          → containers still running
            ├─ DELETE .../myapp/0                 → removed
            ├─ docker ps                          → web + db containers gone
            └─ GET  /users                        → empty list
```

Run:
```bash
# From project root, with Docker access
sudo bash tests/test_integration.sh

# Or if your user is in the docker group
bash tests/test_integration.sh
```

Expected output:
```
Results: 12 passed, 0 failed
```

The test creates an isolated `PROVISION_DIR` in a temp directory and tears everything
down (containers + temp dir) on exit, even on failure.

---

## Running All Tests (unit + e2e)

```bash
# Install dev dependencies
uv sync

# Run pytest
python -m pytest tests/test_unit.py tests/test_e2e.py -v
```

Expected: **62 passed** (30 unit + 32 e2e).

---

## Fixtures

| File | Used by |
|---|---|
| `tests/fixtures/docker-compose.template.yml.j2` | unit, e2e, integration |
| `tests/fixtures/myapp.template.nginx.conf.j2` | unit, e2e, integration |

`conftest.py` provides:
- `tmp_generated` — isolated temp directory acting as `generated/`
- `registry_file` — monkeypatched `REGISTRY_FILE` in a temp location
- `mock_input_yes` — patches `input()` to return `"y"` (for volume mismatch prompts)
