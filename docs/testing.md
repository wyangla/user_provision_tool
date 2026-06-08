# Testing

The test suite has four layers:

```
Integration (bash)   tests/test_integration.sh     23 tests
  └─ full Docker round-trip: build image → start API → register → rebuild → remove
    also covers -fc / -fn plain-file conversion via the API
    also covers passwd='' (no-auth) and default-passwd (auth enabled) paths
    also covers async task pool (GET /tasks, GET /tasks/{id}, DELETE /tasks/{id})
    also covers proxy build_args with MockProxy

E2E (pytest)         tests/test_e2e.py              ~36 tests
  └─ exercise CLI scripts end-to-end against real files, no Docker
    includes proxy --build-arg support tests

Proxy Support        tests/test_proxy_support.py    38 tests
  └─ docker_ops, provisioner, API models, CLI parsing, MockProxy lifecycle

Async Task Pool      tests/test_task_manager.py     10 tests
  └─ submit, complete, fail, cancel, list_all, uniqueness

Unit (pytest)        tests/test_unit.py             ~90 tests
  └─ individual lib/ functions in isolation, all I/O mocked
    includes provisioner proxy support tests
```

---

## Unit Tests

**File:** `tests/test_unit.py`  
**Covers:** `validation`, `registry`, `template_engine`, `auth`, `docker_ops`, `compose_converter`, `nginx_converter`, `provisioner` (proxy support)

Run:
```bash
uv run pytest tests/test_unit.py -v
```

Notable patterns:
- `registry` tests use the `registry_file` fixture (monkeypatched temp file).
- `docker_ops` tests mock `subprocess.Popen` to avoid real Docker calls (was `subprocess.run` prior to real-time-output refactoring).
- `template_engine` tests render against fixture templates in `tests/fixtures/`.
- `TestComposeConverter` covers container_name rewriting, volume extraction, network substitution, profile filtering.
- `TestNginxConverter` covers server_name, auth_basic, proxy_pass, and htpasswd_path substitutions.
- `TestProvisionerProxySupport` covers `build_args` storage in registry and rebuild fallback.

---

## E2E Tests

**File:** `tests/test_e2e.py`  
**Covers:** all `cli/` scripts invoked end-to-end against real temp directories.

Run:
```bash
uv run pytest tests/test_e2e.py -v
```

Notable patterns:
- `docker_ops` is patched so no containers are started (`subprocess.Popen` mocked).
- `TestE2EProxySupport` — register/rebuild with `--build-arg` flag, registry storage, fallback.
- Tests verify file generation, registry writes, and stdout/stderr.
- `TestE2ERegistration` — basic registration, dedup, network isolation.
- `TestE2ERemoval` / `TestE2ERebuild` / `TestE2EStatus` — lifecycle operations.
- `TestE2EConverterIntegration` — `-fc` and `-fn` flags: plain file conversion + registration.
- `TestE2ENoPassword` — registration with `passwd=''`.
- `TestE2EFullLifecycle` — register → status → rebuild → remove round-trip.

---

## Proxy Support Tests

**File:** `tests/test_proxy_support.py` — 38 tests

```bash
uv run pytest tests/test_proxy_support.py -v
```

Covers: `docker_ops.compose_build` --build-arg flags, `provisioner` build_args flow, API Pydantic models, CLI `--build-arg` parsing, `MockProxy` lifecycle (start/stop/relay/context-manager).

---

## Async Task Pool Tests

**File:** `tests/test_task_manager.py` — 10 tests

```bash
uv run pytest tests/test_task_manager.py -v
```

Covers: submit → complete, submit → fail, cancel pending, cancel completed, cancel nonexistent, task ID uniqueness, task dict structure, list_all, empty list.

---

## Integration Tests

**File:** `tests/test_integration.sh`  
**Requires:** Docker, `curl`; `jq` optional (falls back to Python).

Runs the full end-to-end cycle against a real Docker daemon (23 tests):

```
Build provision-api image
  └─ Start provision-api container
       └─ Wait for /health
            ├─ GET  /users                              → empty list
            ├─ POST /users?sync=true (with passwd)      → register testuser/myapp/0
            ├─ POST /users?sync=true (duplicate)        → 409
            ├─ docker ps                                 → web + db containers running
            ├─ GET  /users/testuser                     → 1 healthy service
            ├─ POST .../rebuild?sync=true               → rebuilt
            ├─ docker ps                                 → containers still running
            ├─ DELETE .../myapp/0?sync=true             → removed
            ├─ docker ps                                 → containers gone
            ├─ GET  /users                              → empty list
            ├─ POST /users (re-register)                → network connectivity check
            ├─ docker network inspect                   → provision-nginx connected
            ├─ DELETE /users (teardown)                 → network removed
            ├─ POST /users?sync=true with -fc/-fn       → auto-converted + registered
            ├─ POST /users (no compose source)          → 422
            ├─ POST /users (both compose sources)       → 422
            ├─ POST /users?sync=true (default passwd)   → htpasswd + auth_basic
            ├─ POST /users?sync=true (bare project_root)→ resolves correctly
            ├─ POST /users?sync=true with build_args    → MockProxy + docker_ops.log
            ├─ POST .../rebuild?sync=true override      → override in docker_ops.log
            ├─ POST /users (async)                      → task_id, poll until complete
            ├─ GET  /tasks                              → task pool list with table
            ├─ POST /users (cancel)                     → DELETE /tasks/{id} cancelled
            └─ GET  /tasks/{nonexistent}                → 404
```

Run:
```bash
# From project root, with Docker access
sudo bash tests/test_integration.sh

# Or if your user is in the docker group
bash tests/test_integration.sh
```

### Run all tests

```bash
# All pytest-based tests (181 tests, no Docker needed)
uv run pytest tests/test_unit.py tests/test_e2e.py tests/test_proxy_support.py tests/test_task_manager.py -v

# Full integration (23 tests, requires Docker)
sudo bash tests/test_integration.sh
```
```
Results: 25 passed, 0 failed
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

Expected: **132 passed** (94 unit + 38 e2e).

---

## Fixtures

| File | Used by |
|---|---|
| `tests/fixtures/docker-compose.template.yml.j2` | unit, e2e, integration |
| `tests/fixtures/myapp.template.nginx.conf.j2` | unit, e2e, integration |
| `tests/fixtures/docker-compose.plain.yml` | unit, e2e, integration |
| `tests/fixtures/myapp.plain.nginx.conf` | unit, e2e, integration |

`conftest.py` provides:
- `tmp_generated` — isolated temp directory acting as `generated/`
- `registry_file` — monkeypatched `REGISTRY_FILE` in a temp location
- `mock_input_yes` — patches `input()` to return `"y"` (for volume mismatch prompts)
