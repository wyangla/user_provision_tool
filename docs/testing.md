# Testing

The test suite has three layers:

```
Integration (bash)   tests/test_integration.sh     17 tests
  └─ full Docker round-trip: build image → start API → register → rebuild → remove
    also covers -fc / -fn plain-file conversion via the API

E2E (pytest)         tests/test_e2e.py              32 tests
  └─ exercise CLI scripts end-to-end against real files, no Docker

Unit (pytest)        tests/test_unit.py             88 tests
  └─ individual lib/ functions in isolation, all I/O mocked
```

---

## Unit Tests

**File:** `tests/test_unit.py`  
**Covers:** `validation`, `registry`, `template_engine`, `auth`, `docker_ops`, `compose_converter`, `nginx_converter`

Run:
```bash
python -m pytest tests/test_unit.py -v
```

Notable patterns:
- `registry` tests use the `registry_file` fixture (monkeypatched temp file).
- `docker_ops` tests mock `subprocess.run` to avoid real Docker calls.
- `template_engine` tests render against fixture templates in `tests/fixtures/`.
- `TestComposeConverter` covers container_name rewriting, volume extraction, network substitution, profile filtering (strips `profiles:`, excludes named-profile services).
- `TestNginxConverter` covers server_name, auth_basic, proxy_pass, and htpasswd_path substitutions.

---

## E2E Tests

**File:** `tests/test_e2e.py`  
**Covers:** all `cli/` scripts invoked end-to-end against real temp directories.

Run:
```bash
python -m pytest tests/test_e2e.py -v
```

Notable patterns:
- `docker_ops` is patched so no containers are started.
- Tests verify file generation, registry writes, and stdout/stderr.
- `TestE2ERegistration` — basic registration, dedup, network isolation.
- `TestE2ERemoval` / `TestE2ERebuild` / `TestE2EStatus` — lifecycle operations.
- `TestE2EConverterIntegration` — `-fc` and `-fn` flags: plain file conversion + registration.
- `TestE2EFullLifecycle` — register → status → rebuild → remove round-trip.

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
            ├─ GET  /users                        → empty list
            ├─ POST /users with plain compose (-fc) → auto-converted + registered
            ├─ validate .j2 template written into source dir
            ├─ DELETE file-registered user
            ├─ POST /users with invalid compose   → 400
            └─ nginx reload called after each registration
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
Results: 17 passed, 0 failed
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

Expected: **120 passed** (88 unit + 32 e2e).

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
