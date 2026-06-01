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
- `provision-nginx` is the shared ingress router. It runs as a sibling container and is dynamically connected to each user's isolated Docker network after registration so it can proxy requests to that user's containers.

---

## Environment Variables

Set these before running `docker compose up`.

| Variable | Required | Example | Description |
|---|---|---|---|
| `PROVISION_DIR` | ✓ | `/srv/provision` | Base directory; must be the same path inside and outside the container |
| `PROVISION_API_PORT` | — | `8765` | Host port for the provision-api REST API (default `8765`) |
| `NGINX_HTTP_PORT` | — | `80` | Host port for provision-nginx (default `80`) |
| `NGINX_CONTAINER` | — | `provision-nginx` | Name of the nginx container to connect/reload on registration (default `provision-nginx`) |

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
      - NGINX_CONTAINER=provision-nginx          # which container to connect/reload
    restart: unless-stopped

  provision-nginx:
    image: nginx:alpine
    container_name: provision-nginx
    ports:
      - "${NGINX_HTTP_PORT:-80}:80"             # host:container
    volumes:
      - ${PROVISION_DIR}:${PROVISION_DIR}:ro    # read-only; htpasswd paths must resolve
      - ./user_provision_tool/nginx.conf.template:/etc/nginx/nginx.conf.template:ro
    environment:
      - GENERATED_DIR=${PROVISION_DIR}/generated
    # envsubst replaces only $GENERATED_DIR; all nginx $variables are left intact
    command: >
      /bin/sh -c "envsubst '$$GENERATED_DIR' < /etc/nginx/nginx.conf.template
                  > /etc/nginx/nginx.conf && nginx -g 'daemon off;'"
    restart: unless-stopped
```

The `nginx.conf.template` includes all per-user virtual-host confs at startup:

```nginx
# nginx.conf.template (simplified)
http {
    include ${GENERATED_DIR}/*.nginx.conf;   # ← envsubst fills GENERATED_DIR
}
```

Each `*.nginx.conf` file is written by provision-api when a user registers. After writing
the file, provision-api calls `docker exec provision-nginx nginx -s reload` so the new
virtual host takes effect immediately without a container restart.

---

## Nginx Routing

`provision-nginx` is an `nginx:alpine` container defined in `docker-compose.provision.yml`.
It is the single ingress point for all HTTP traffic to all user containers.

### How routing works

```
HTTP request
  Host: myapp-alice-0.example.com
        │
        ▼
  provision-nginx  (port 80)
        │
        │  nginx matches server_name in GENERATED_DIR/myapp.user-alice.0.nginx.conf
        │
        ▼
  proxy_pass  http://myapp-user_alice-0-web:8000
              (reachable because nginx is connected to the myapp-user_alice-0 network)
```

Routing is virtual-host based (matched by the `Host:` header / `server_name` directive).
Each registered user gets their own `*.nginx.conf` in `GENERATED_DIR`.

### Config loading

`nginx.conf.template` is mounted read-only into the container. At container startup,
`envsubst` substitutes `$GENERATED_DIR` to produce `/etc/nginx/nginx.conf`:

```nginx
http {
    include ${GENERATED_DIR}/*.nginx.conf;   # expands to e.g. /srv/provision/generated/*.nginx.conf
}
```

Only `$GENERATED_DIR` is substituted; all nginx `$variables` (e.g. `$host`, `$remote_addr`)
are left intact by the `envsubst '$$GENERATED_DIR'` invocation.

### Dynamic updates

When provision-api registers or removes a user:

1. It writes (or deletes) the user's `*.nginx.conf` in `GENERATED_DIR`.
2. It runs `docker exec provision-nginx nginx -s reload` — nginx picks up the new conf
   without a container restart.
3. It calls `docker network connect {network_name} provision-nginx` (register) or
   `docker network disconnect` (remove) so nginx can reach the user's containers.

### Why user containers don't bind ports

The compose converter strips the `ports:` key from all services in user compose files.
All traffic flows through `provision-nginx`. This avoids host port conflicts between users
running the same service type, and keeps user services unreachable except through nginx.

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
