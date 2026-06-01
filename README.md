# User Provision Tool

Give each user their own isolated copy of a service — with one API call.
No template prep required: just point to your existing `docker-compose.yml` and nginx conf.

## What it does

Imagine a shared server that can instantly spin up a private workspace for any new user —
with its own containers, database, and dedicated web address — and tear it all down just as
quickly. No manual setup, no port conflicts, no data leaking between users.

Technically: you drop a plain `docker-compose.yml` (and optionally a plain nginx conf) into
a source project directory. When a user registers, the tool:

1. **Auto-converts** your plain files into per-user Jinja2 templates (once, on first use)
2. **Renders** isolated `docker-compose.user-{user}.{label}.yml` and `*.nginx.conf` for that user
3. **Starts** the containers with `docker compose up --project-name {isolated-name}`
4. **Routes** HTTP traffic by connecting `provision-nginx` to the user's Docker network and reloading nginx live
5. **Tracks** state in `user_registry.yml` — remove a user and everything is torn down cleanly

Per-user container names: `{service}-user_{user}-{label}-{svc}`
Per-user hostnames: `{service}-{user}-{label}.{domain}`

```mermaid
flowchart LR
    operator(["Operator"])
    end_user(["End User"])

    input["Source project"]
    conv["① auto convert\ncompose · nginx → .j2"]
    render["② render per-user\ncompose · nginx · htpasswd"]
    compose["③ compose up\nper user isolated service"]
    route["④ nginx connect\n+ reload"]

    operator -->|"POST /users"| input
    input --> conv --> render --> compose --> route
    end_user -->|"HTTP"| route

    style conv    fill:#dbeafe,stroke:#3b82f6
    style render  fill:#dbeafe,stroke:#3b82f6
    style compose fill:#dbeafe,stroke:#3b82f6
    style route   fill:#dbeafe,stroke:#3b82f6
```

---

## Quick Start (API)

**1. Set up the provision directory and drop in your service**
```bash
export PROVISION_DIR=/srv/provision
mkdir -p $PROVISION_DIR/generated $PROVISION_DIR/source_projects/myapp
# copy your service into source_projects/myapp/  (Dockerfile, docker-compose.yml, nginx.conf, .env, ...)
```

**2. Start the provision service**
```bash
docker compose -f docker-compose.provision.yml up -d --build
```

**3. Register a user — point straight at your plain files**
```bash
curl -X POST http://localhost:8765/users \
  -H 'Content-Type: application/json' \
  -d '{
    "user_name": "alice",
    "service_name": "myapp",
    "compose_file_path": "/srv/provision/source_projects/myapp/docker-compose.yml",
    "nginx_conf_file_path": "/srv/provision/source_projects/myapp/nginx.conf",
    "env_file_path": "/srv/provision/source_projects/myapp/.env",
    "domain": "example.com",
    "passwd": "secret"
  }'
```

> The tool auto-converts `docker-compose.yml` → `docker-compose.yml.j2` and `nginx.conf` → `nginx.conf.j2`
> on first use. Subsequent registrations reuse the generated templates.

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
# Register — point at your plain compose file and nginx conf directly
python cli/register.py \
  -u alice -sn myapp \
  -pr /srv/provision/source_projects/myapp \
  -fc docker-compose.yml \
  -fn nginx.conf \
  -e .env \
  -d example.com

# Already have .j2 templates? Use -tc / -tn instead of -fc / -fn

# Status
python cli/status.py -u alice

# Rebuild
python cli/rebuild.py -u alice -sn myapp -l 0

# Remove
python cli/remove.py -u alice -sn myapp -l 0
```

---

## Architecture

```mermaid
%%{init: {"flowchart": {"defaultRenderer": "elk"}, "elk": {"nodePlacementStrategy": "NETWORK_SIMPLEX", "edgeRouting": "SPLINES"}} }%%
flowchart LR
    operator(["Operator"])
    end_user(["End User\nbrowser / curl"])

    subgraph host["Docker Host"]
        subgraph upt["User Provision Tool"]
            direction TB
            provision_api["provision-api\nFastAPI · :8765"]
            provision_nginx["provision-nginx\nnginx · :80"]
        end

        docker_daemon["Docker Daemon"]
        provision_dir[("PROVISION_DIR\nregistry · templates · confs")]
        user_nets["User Containers\nper-user Docker networks"]
    end

    operator -->|"REST API / CLI"| provision_api
    end_user -->|"HTTP · Host header"| provision_nginx

    provision_api -->|"compose up/down\nnetwork connect"| docker_daemon
    provision_api -->|"write registry & configs"| provision_dir

    provision_nginx -->|"read *.nginx.conf"| provision_dir
    provision_nginx -->|"proxy_pass"| user_nets

    docker_daemon -->|"start · stop · build"| user_nets
    user_nets -->|"bind mounts"| provision_dir

    style upt fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
```

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
