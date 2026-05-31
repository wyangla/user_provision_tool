# Deployment

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker Engine | 24+ on the host |
| Docker Compose plugin | `docker compose` (v2) |
| Internet access at build time | To pull `python:3.13-slim`, `docker:cli`, `uv` |

---

## Container Architecture

```
  ┌─────────────────────────────────────────── host ──────────────────────────────────────────────┐
  │                                                                                                │
  │   HTTP client          ┌──────────────────────┐        ┌────────────────────────────────┐    │
  │   (curl / app)         │   provision-api       │        │   PROVISION_DIR                │    │
  │        │               │   container :8765     │◄──────►│   templates/   generated/      │    │
  │        │  REST API      │                       │        └────────────────────────────────┘    │
  │        └──────────────►│   docker compose ...  │                        ▲                     │
  │                        └──────────┬────────────┘                        │ bind mounts         │
  │                                   │ /var/run/docker.sock                │                     │
  │                                   ▼                                     │                     │
  │                        ┌──────────────────────┐       ┌─────────────────┴──────────┐          │
  │                        │   Docker daemon       │──────►│   User containers          │          │
  │                        │   (host)              │       │   e.g. web, db, ...        │          │
  │                        └──────────────────────┘       └────────────────────────────┘          │
  └────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Key points:**
- The provision-api container does **not** run a Docker daemon — it uses the host daemon via the socket.
- The `PROVISION_DIR` bind mount uses the same path on both sides (`${PROVISION_DIR}:${PROVISION_DIR}`) so absolute paths in generated compose files are valid on the host.
- User containers are started as siblings of the provision-api container, not children.

---

## Environment Variables

Set these before running `docker compose up`.

| Variable | Required | Example | Description |
|---|---|---|---|
| `PROVISION_DIR` | ✓ | `/srv/provision` | Base directory; must be the same path inside and outside the container |
| `PROVISION_API_PORT` | — | `8765` | Host port for the API (default `8765`) |

---

## `docker-compose.provision.yml` Walkthrough

```yaml
services:
  provision-api:
    build: .                                 # builds from ./Dockerfile
    ports:
      - "${PROVISION_API_PORT:-8765}:8000"   # host:container
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock   # Docker socket
      - ${PROVISION_DIR}:${PROVISION_DIR}            # same-path bind mount
    environment:
      - GENERATED_DIR=${PROVISION_DIR}/generated        # nginx conf, htpasswd, registry
      - USER_DATA_DIR=${PROVISION_DIR}/user_data         # auto-created per-user volume dirs
      - SOURCE_PROJECTS_DIR=${PROVISION_DIR}/source_projects  # operator repo drop zone
      - REGISTRY_FILE=${PROVISION_DIR}/generated/user_registry.yml
    restart: unless-stopped
```

---

## Start / Stop

```bash
# 1. Set required variables
export PROVISION_DIR=/srv/provision
export PROVISION_API_PORT=8765

# 2. Create the provision directory structure
mkdir -p $PROVISION_DIR/generated $PROVISION_DIR/source_projects $PROVISION_DIR/user_data

# 3. Start (builds image on first run)
docker compose -f docker-compose.provision.yml up -d --build

# 4. Check it is running
curl http://localhost:8765/health
# → {"status": "ok"}

# 5. Stop
docker compose -f docker-compose.provision.yml down
```

---

## Directory Layout at Runtime

```
PROVISION_DIR/
├── source_projects/              ← your service source trees (bind-mounted same-path)
│   └── myapp/
│       ├── Dockerfile
│       ├── docker-compose.myapp.yml.j2    ← compose template (you provide)
│       ├── myapp.nginx.conf.j2            ← nginx template   (you provide)
│       ├── myapp.env                      ← runtime secrets   (you provide)
│       └── docker-compose.user-alice.0.yml ← rendered per-user compose (written by tool)
│
├── user_data/                    ← per-user volume directories (auto-created by tool)
│   └── alice/
│       └── myapp/
│           └── 0/
│               ├── app_data/
│               └── db_data/
│
└── generated/                    ← written by provision-api
    ├── user_registry.yml
    ├── myapp.user-alice.0.nginx.conf
    └── myapp.user-alice.0.htpasswd
```

---

## Upgrading

To update the provision-api image after a code change:

```bash
docker compose -f docker-compose.provision.yml up -d --build --force-recreate
```

User containers are unaffected — they are managed independently by the Docker daemon.

---

## Dockerfile Notes

The Dockerfile uses a multi-stage build to pull the Docker CLI binary from
`docker:cli` (Docker Hub) and `uv` from `ghcr.io/astral-sh/uv`. Neither copies
anything from the host filesystem. The docker binary in `docker:cli` is statically
linked (Alpine/musl) and runs on any Linux.

Dependencies are installed via `uv sync` into `.venv/` during the build step.
At runtime the container starts `uvicorn` directly from `.venv/bin/uvicorn` to avoid
the package-sync delay that `uv run` introduces.

The `docker-buildx` plugin is copied alongside `docker-compose` because Docker
29+ requires it when BuildKit is the active builder. Without it, `docker build`
(and `docker compose build`) would fail inside the container.

```
FROM python:3.13-slim
  ├─ COPY --from=docker:cli       → /usr/local/bin/docker
  │                                  /usr/local/libexec/docker/cli-plugins/docker-compose
  │                                  /usr/local/libexec/docker/cli-plugins/docker-buildx
  ├─ COPY --from=ghcr.io/.../uv  → /usr/local/bin/uv
  ├─ COPY pyproject.toml uv.lock → uv sync (install deps into .venv/)
  ├─ COPY lib/ cli/ api.py
  └─ CMD [".venv/bin/uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```
