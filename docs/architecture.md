# Architecture: User Containers Provision Tool

## Directory Layout

```
user_provision_tool/
├── api.py                         # FastAPI REST service (primary runtime entry point)
├── docker-compose.provision.yml   # Runs the provision-api container itself
├── Dockerfile                     # Builds the provision-api image
├── pyproject.toml / uv.lock       # Python dependencies (managed via uv)
│
├── cli/                           # CLI entry points (direct/scripted use)
│   ├── __init__.py
│   ├── register.py                # Register user + start containers
│   ├── remove.py                  # Stop + deregister user
│   ├── rebuild.py                 # Rebuild user containers
│   └── status.py                  # Query container health
│
├── lib/                           # Shared library modules
│   ├── __init__.py
│   ├── validation.py              # Name/label regex validation
│   ├── registry.py                # CRUD on user_registry.yml
│   ├── template_engine.py         # Jinja2 compose + nginx rendering
│   ├── auth.py                    # Password hashing (passlib/bcrypt)
│   └── docker_ops.py              # Subprocess wrappers for docker compose
│
├── generated/                     # Runtime output (auto-created)
│   ├── user_registry.yml          # Managed state file
│   ├── docker-compose.{svc}-user_{user}-{label}.yml
│   ├── {svc}.user-{user}-{label}.nginx.conf
│   └── {svc}-user_{user}-{label}.env  # Copied .env (if supplied)
│
└── tests/
    ├── conftest.py                # Shared pytest fixtures
    ├── test_unit.py               # Unit tests (30)
    ├── test_e2e.py                # End-to-end pytest tests (32)
    ├── test_integration.sh        # Full Docker integration test (12)
    └── fixtures/
        ├── docker-compose.template.yml.j2
        └── myapp.template.nginx.conf.j2
```

---

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `validation.py` | Enforce `[a-zA-Z0-9_]` for names, `[0-9]` for label; raise `ValidationError` |
| `registry.py` | Load/save `user_registry.yml`; add/remove/query entries by user+service+label |
| `template_engine.py` | Extract template volumes; render compose and nginx files via Jinja2; copy `.env` alongside output |
| `auth.py` | `getpass` prompt; bcrypt hash via `passlib.hash.bcrypt`; write `.htpasswd` file |
| `docker_ops.py` | `compose_up`, `compose_down`, `compose_build`, `docker_ps` wrappers; optional `--env-file` flag |

---

## Module Dependencies

```
  api.py           →  validation, registry, template_engine, auth, docker_ops
  cli/register.py  →  validation, registry, template_engine, auth, docker_ops
  cli/remove.py    →  validation, registry, docker_ops
  cli/rebuild.py   →  validation, registry, docker_ops
  cli/status.py    →  registry, docker_ops, template_engine
```

---

## Data Flows

### Registration (API or CLI)

```
Input: user_name, service_name, label, volumes, passwd, template paths, env_file?
  │
  ├─ validation.py ── validate names and label format
  │
  ├─ template_engine.py ── extract declared volume keys from template
  │       └─ volumes mismatch? → CLI warns + prompts; API rejects with 400
  │
  ├─ auth.py ── hash password → bcrypt hash
  │
  ├─ registry.py ── append entry to user_registry.yml
  │
  ├─ template_engine.py ── render docker-compose.{svc}-user_{user}-{label}.yml
  │       └─ env_file provided? → copy .env next to compose file
  │
  ├─ template_engine.py ── render {svc}.user-{user}-{label}.nginx.conf  (optional)
  │
  └─ docker_ops.py ── docker compose -f <file> [--env-file <env>] up -d
```

### Removal

```
Input: user_name, service_name, label
  │
  ├─ registry.py ── look up compose_file_path + env_file_path
  │
  ├─ docker_ops.py ── docker compose down
  │
  └─ registry.py ── remove entry from user_registry.yml
```

### Rebuild

```
Input: user_name, service_name, label
  │
  ├─ registry.py ── look up compose_file_path + env_file_path
  │
  ├─ docker_ops.py ── docker compose build
  │
  └─ docker_ops.py ── docker compose up -d
```

---

## Naming Conventions

| Artifact | Pattern |
|---|---|
| Compose file | `docker-compose.{service_name}-user_{user_name}-{label}.yml` |
| Nginx conf | `{service_name}.user-{user_name}-{label}.nginx.conf` |
| Copied env file | `{service_name}-user_{user_name}-{label}.env` |
| Container prefix | `{service_name}-user_{user_name}-{label}-` |
| Nginx hostname | `{service_name}-{user_name}-{label}.{domain_name}` |

---

## Key Design Decisions

1. **`cli/` package** — all four CLI scripts live under `cli/` and share `lib/` with no logic duplication. The `api.py` is the preferred runtime entry point.
2. **`.j2` template extension** — compose and nginx templates use the `.j2` suffix so YAML linters do not flag Jinja2 placeholders as syntax errors.
3. **Two placeholder types in templates** — `{{ var }}` is resolved by Jinja2 at render time; `${ENV_VAR}` is passed through as literal text and resolved by `docker compose` at runtime via `--env-file`.
4. **Docker socket pattern** — the provision-api container mounts `/var/run/docker.sock` and runs `docker compose` without `sudo`. No Docker daemon is installed inside the container; only the CLI binary is present.
5. **Same-path bind mount** — `${PROVISION_DIR}:${PROVISION_DIR}` ensures the absolute paths written into generated compose files are valid on the host where the Docker daemon runs.
6. **`passlib.hash.bcrypt`** — passwords are hashed with `bcrypt.using(rounds=12).hash()`; hashes are stored in `user_registry.yml` and written into `.htpasswd` files for nginx basic auth.
7. **`user_registry.yml` as source of truth** — `cli/status.py` and `GET /users` cross-reference live `docker ps` output against registry entries to compute per-service health.

---

## Status Model

```
Registry entries for user
        │
        ▼
For each entry → expected containers = services declared in compose template
        │
        ├─ docker ps match, status "Up"           → healthy_containers
        ├─ docker ps match, status contains error  → unhealthy_containers
        └─ not found in docker ps output           → missing_containers

Service health = "healthy"  iff  healthy == expected  AND  unhealthy + missing == 0
```

### Status Response Schema

```json
{
  "user_status": [
    {
      "user_name": "alice",
      "summary": {
        "expected_services_#": 2,
        "healthy_services_#": 1,
        "unhealthy_services_#": 0
      },
      "healthy_services": [
        {
          "service_name": "myapp",
          "label": "0",
          "compose_file_path": "/srv/provision/generated/docker-compose.myapp-user_alice-0.yml",
          "healthy_containers": { "myapp-user_alice-0-web": "Up 2 hours" },
          "unhealthy_containers": {},
          "missing_containers": {}
        }
      ],
      "unhealthy_services": [],
      "missing_services": []
    }
  ]
}
```
