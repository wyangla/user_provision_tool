# User Provision Tool

A self-hosted REST service that provisions per-user Docker Compose stacks from Jinja2 templates.

## What it does

```
You provide                      Tool does
─────────────────                ─────────────────────────────────────────
Jinja2 compose template   ──►   Render a concrete docker-compose.yml
Volume paths per user     ──►   Substitute names, labels, paths
Optional .env file        ──►   Pass runtime secrets to docker compose
Optional nginx template   ──►   Render nginx conf + htpasswd file
                          ──►   docker compose up / down / build
                          ──►   Track state in user_registry.yml
```

Per-user container names are deterministic: `{service}-user_{user}-{label}-{svc}`.

---

## Quick Start (API)

**1. Set up the provision directory**
```bash
export PROVISION_DIR=/srv/provision
mkdir -p $PROVISION_DIR/templates $PROVISION_DIR/generated
# copy your .j2 templates there
```

**2. Start the service**
```bash
docker compose -f docker-compose.provision.yml up -d --build
```

**3. Register a user**
```bash
curl -X POST http://localhost:8765/users \
  -H 'Content-Type: application/json' \
  -d '{
    "user_name": "alice",
    "service_name": "myapp",
    "compose_template_path": "/srv/provision/templates/myapp.yml.j2",
    "volumes": {
      "app_data": "/srv/provision/user-data/alice/app",
      "db_data":  "/srv/provision/user-data/alice/db"
    },
    "passwd": "secret"
  }'
```

**4. Check status**
```bash
curl http://localhost:8765/users/alice
```

**5. Remove**
```bash
curl -X DELETE http://localhost:8765/users/alice/services/myapp/0
```

---

## Quick Start (CLI)

```bash
# Register
python cli/register.py \
  -u alice -sn myapp \
  -tc /srv/provision/templates/myapp.yml.j2 \
  -v app_data=/srv/provision/user-data/alice/app \
  -v db_data=/srv/provision/user-data/alice/db

# Status
python cli/status.py -u alice

# Rebuild
python cli/rebuild.py -u alice -sn myapp -l 0

# Remove
python cli/remove.py -u alice -sn myapp -l 0
```

---

## Architecture

```
  ┌─────────────────────────────────────┐
  │             lib/                    │
  │  validation      registry           │
  │  template_engine auth               │
  │  docker_ops                         │
  └────────────┬────────────────────────┘
               │  shared by both entry points
       ┌───────┴────────┐
       │                │
  ┌────┴─────┐   ┌──────┴──────────────────────────┐
  │  api.py  │   │  cli/                           │
  │  FastAPI │   │  register · remove · rebuild    │
  │  :8765   │   │  status                         │
  └──────────┘   └─────────────────────────────────┘
```

The API and CLI share the same `lib/` modules. The API is the preferred runtime entry point;
the CLI is useful for scripting or running without the container.

---

## Documentation

| Document | Topic |
|---|---|
| [architecture.md](docs/architecture.md) | Module layout, data flows, naming conventions |
| [api-reference.md](docs/api-reference.md) | All REST endpoints and request/response schemas |
| [cli-reference.md](docs/cli-reference.md) | CLI script arguments and examples |
| [templates.md](docs/templates.md) | Writing compose and nginx templates |
| [deployment.md](docs/deployment.md) | Running in production, environment variables |
| [testing.md](docs/testing.md) | Running unit, e2e, and integration tests |

---

## Development

```bash
# Install dependencies (requires uv)
uv sync

# Run unit + e2e tests (62 tests, no Docker needed)
python -m pytest tests/test_unit.py tests/test_e2e.py -v

# Run full integration tests (requires Docker)
sudo bash tests/test_integration.sh
```
