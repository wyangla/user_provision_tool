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
mkdir -p $PROVISION_DIR/generated $PROVISION_DIR/source_projects
# copy your service source directories + .j2 templates there
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
    "compose_template_path": "/srv/provision/source_projects/myapp/docker-compose.myapp.yml.j2",
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
# Register (provide project root + template file)
python cli/register.py \
  -u alice -sn myapp \
  -pr /srv/provision/source_projects/myapp \
  -tc docker-compose.myapp.yml.j2 \
  -v app_data=/srv/provision/user-data/alice/app \
  -v db_data=/srv/provision/user-data/alice/db

# Or let the tool generate the template from a plain compose file:
# python cli/register.py ... -fc docker-compose.yml ...

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
  ┌─────────────────────────────────────────────────┐
  │             lib/                                │
  │  provisioner                                    │
  │  validation      registry                       │
  │  template_engine auth                           │
  │  docker_ops      compose_converter              │
  │                  nginx_converter                │
  └────────────┬────────────────────────────────────┘
               │  api.py and cli/ both delegate to provisioner
       ┌───────┴────────┐
       │                │
  ┌────┴─────┐   ┌──────┴─────────────────────────────────────────┐
  │  api.py  │   │  cli/                                          │
  │  FastAPI │   │  register · remove · rebuild · status          │
  │  :8765   │   │  gen_compose_template · gen_nginx_template     │
  └──────────┘   └────────────────────────────────────────────────┘
```

Both the API and CLI delegate shared workflow logic to `lib/provisioner.py`.
Rendered compose files land next to their template in the source project directory;
nginx conf and htpasswd files land in `GENERATED_DIR`.

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
| [template_rendering_workflow.md](docs/template_rendering_workflow.md) | Step-by-step rendering pipeline |

---

## Development

```bash
# Install dependencies (requires uv)
uv sync

# Run unit + e2e tests (112 tests, no Docker needed)
python -m pytest tests/test_unit.py tests/test_e2e.py -v

# Run full integration tests (requires Docker)
sudo bash tests/test_integration.sh
```
