---
name: provision-api
description: "Use when: interacting with the locally deployed provision-api (user_provision_tool) via curl — registering users, checking status, rebuilding, or removing per-user Docker Compose stacks. Also use when: a target repo has a Dockerfile but no docker-compose.yml or nginx.conf — read the repo docs to create them following the provision-api template patterns."
---

# Provision-API Skill

This skill covers working with the locally deployed **User Provision Tool** (`provision-api`) —
a FastAPI service at `http://localhost:8765` that stamps out isolated, routed per-user copies
of any Docker Compose stack with a single API call.

Source project: `_users_provision/` (under the workspace root).

---

## Quick Reference (curl commands)

All examples assume the provision-api is running on `http://localhost:8765`.

### Health check

```bash
curl http://localhost:8765/health
# → {"status": "ok"}
```

### Register a user

```bash
curl -X POST http://localhost:8765/users \
  -H 'Content-Type: application/json' \
  -d '{
    "user_name": "alice",
    "service_name": "myapp",
    "project_root": "myapp",
    "compose_file_path": "docker-compose.yml",
    "nginx_conf_file_path": "nginx.conf",
    "env_file_path": ".env",
    "domain": "example.com",
    "passwd": "secret"
  }'
# → 201 with registration entry
```

**Key parameters:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_name` | string | ✓ | `[a-zA-Z0-9_]+` |
| `service_name` | string | ✓ | `[a-zA-Z0-9_]+` |
| `project_root` | string | — | Bare name resolves to `$SOURCE_PROJECTS_DIR/{name}` (= `$PROVISION_DIR/source_projects/{name}`). Relative or absolute path used as-is. Returns 404 if dir not found. |
| `compose_file_path` | string | † | Plain `docker-compose.yml` filename (auto-converted to `.j2` template) |
| `compose_template_path` | string | † | Pre-made `.j2` compose template filename |
| `nginx_conf_file_path` | string | — | Plain nginx conf filename (auto-converted to `.j2` template) |
| `nginx_conf_template_path` | string | — | Pre-made `.j2` nginx conf template filename |
| `env_file_path` | string | — | `.env` filename for Docker Compose `${VAR}` substitution |
| `label` | string | — | Digits only; default `"0"` |
| `domain` | string | — | Domain for `server_name`; default `"localhost"` |
| `passwd` | string | — | Default `"123456"`. Pass `""` to disable HTTP basic auth entirely. |
| `volumes` | object | — | `{ "template_vol_key": "/host/path", ... }` |

> † Exactly one of `compose_file_path` or `compose_template_path` is required.

**Response codes:** 201 (success), 404 (not found), 409 (duplicate), 422 (validation), 500 (docker compose up failed).

### Get all users status

```bash
curl http://localhost:8765/users
```

### Get single user status

```bash
curl http://localhost:8765/users/alice
```

### Rebuild user containers

```bash
curl -X POST http://localhost:8765/users/alice/services/myapp/0/rebuild \
  -H 'Content-Type: application/json' \
  -d '{"no_cache": true}'
```

### Remove (deregister) a user

```bash
curl -X DELETE http://localhost:8765/users/alice/services/myapp/0
```

---

## Deploying provision-api locally

```bash
export PROVISION_DIR=/srv/provision
export PROVISION_API_PORT=8765

# Create directory structure
mkdir -p $PROVISION_DIR/generated $PROVISION_DIR/source_projects $PROVISION_DIR/user_data

# Start
docker compose -f docker-compose.provision.yml up -d --build

# Verify
curl http://localhost:8765/health
```

The compose file at `_users_provision/docker-compose.provision.yml` defines two services:
- **provision-api** (FastAPI on `:8765`) — handles registration, rendering, and Docker orchestration
- **provision-nginx** (nginx:alpine on `:80`) — shared ingress router that proxies to per-user containers

---

## Creating docker-compose.yml when only a Dockerfile exists

When a target repo has a `Dockerfile` but **no** `docker-compose.yml`, read the repo's docs
(README, docs/, etc.) to infer the service architecture, then create a
`docker-compose.yml` following these rules.

### Rules for the compose file

1. **Use `build: .`** (or a subdirectory path) so the Dockerfile is the build context.
   Do NOT hardcode `image:` unless the docs specify a pre-built image.

2. **Do NOT set `container_name:` directly** — the provision tool's auto-converter will
   rewrite `container_name` to use the per-user `{{ container_prefix }}` prefix. Writing
   a descriptive name (e.g., `myapp-web`) helps the converter produce sensible prefixes.

3. **Do NOT hardcode host `ports:` unless essential** — the provision tool strips `ports:`
   on conversion so all traffic routes through `provision-nginx`. If the service needs a
   port exposed internally, use `expose:` instead.

4. **Use named volumes** for persistent data so the converter can extract volume keys
   and map them to per-user host paths via the `volumes` registration parameter.

5. **Use `${ENV_VAR}` syntax** for runtime secrets (API keys, DB passwords). These are
   resolved by `docker compose` at container startup via the `--env-file` flag, keeping
   secrets out of templates and source control.

6. **Define a network** so per-user containers are isolated. The converter replaces
   network names with `{{ network_name }}`.

7. **If the docs provide a compose example, use it verbatim** — only add missing
   directives (volumes, networks, environment) as needed.

### Template for compose file

```yaml
services:
  {service_shortname}:
    build: .
    container_name: {service_shortname}
    expose:
      - "{internal_port}"
    environment:
      - SOME_VAR=${SOME_VAR}
    volumes:
      - {named_vol}:/data
    networks:
      - {service_shortname}-net
    restart: unless-stopped

  # Add db, cache, etc. if the Dockerfile expects them
  db:
    image: postgres:16-alpine
    container_name: {service_shortname}-db
    environment:
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - db_data:/var/lib/postgresql/data
    networks:
      - {service_shortname}-net

volumes:
  {named_vol}:
  db_data:

networks:
  {service_shortname}-net:
```

### Important: write the file into the project root

The compose file must be placed **inside** the project's source directory (i.e., the
directory that will become `project_root`). This is because:
- `build: .` must resolve to the directory containing the Dockerfile.
- The provision tool's auto-converter writes the `.j2` template alongside the source file.
- The rendered per-user compose file is also written into this directory.

---

## Creating nginx.conf when none is provided

When a target repo has no `nginx.conf` that works with the provision tool, create one using
the template below. Place it inside the project root (same directory as the Dockerfile and
`docker-compose.yml`).

### Nginx conf template

Copy this template and fill in the two placeholders for the specific service:

```nginx
server {
    listen 80;
    server_name {service_name}_hostname;

    location / {
        proxy_pass http://{container_name_or_service};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 300s;
        proxy_connect_timeout 60s;
        client_max_body_size 0;
    }
}
```

**Placeholders to fill in:**

| Placeholder | What to put | Converter does |
|---|---|---|
| `{service_name}_hostname` | E.g. `myapp_hostname` | Replaced with `{{ hostname }}` → `{service}-{user}-{label}.{domain}` |
| `{container_name_or_service}` | Container name from compose, e.g. `myapp-web:8080` | Prefix replaced with `{{ container_prefix }}` → `myapp-user_alice-0-web:8080` |

### Converter behavior (what happens automatically)

- **`auth_basic` injection** — if the source conf has no `auth_basic` directives, the
  converter automatically injects `auth_basic "{{ service_name }} - {{ user_name }}";`
  and `auth_basic_user_file {{ htpasswd_path }};` before the first `proxy_pass`.
  When `passwd=""` (no-auth mode), these lines are stripped at render time.

- **Standard proxy headers** — `Host`, `X-Real-IP`, `X-Forwarded-For`, and
  `X-Forwarded-Proto` are already in the template; keep them.

- **WebSocket support** — if the service uses WebSockets, add these to the location block:
  ```nginx
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  ```

### If the target repo's docs provide an nginx example

Use it **verbatim** instead of the template above. The converter will still apply its
standard substitutions (`server_name`, `proxy_pass` prefix, `auth_basic` injection).

### File placement

- The nginx conf lives **inside** the project root (next to the compose file).
- The auto-converter writes the `.j2` template alongside it.
- The rendered per-user nginx conf is written to `GENERATED_DIR` (`$PROVISION_DIR/generated`).

---

## File placement checklist for a new service

When setting up a new service for the provision tool, ensure the project root contains:

```
source_projects/{service_name}/
├── Dockerfile                        ← required (build context)
├── docker-compose.yml                ← required (or docker-compose.{name}.yml)
├── nginx.conf                        ← optional but recommended (or {name}.nginx.conf)
├── .env                              ← optional (runtime secrets via ${ENV_VAR})
└── (application source files)
```

---

## Provision-api template variable reference

When writing `.j2` templates manually, these Jinja2 variables are available:

| Variable | Example | Scope |
|---|---|---|
| `{{ user_name }}` | `alice` | compose + nginx |
| `{{ service_name }}` | `myapp` | compose + nginx |
| `{{ label }}` | `0` | compose + nginx |
| `{{ container_prefix }}` | `myapp-user_alice-0-` | compose + nginx |
| `{{ network_name }}` | `myapp-user_alice-0` | compose only |
| `{{ volumes['key'] }}` | `/srv/provision/user-data/alice/app` | compose only |
| `{{ domain_name }}` | `example.com` | compose + nginx |
| `{{ hostname }}` | `myapp-alice-0.example.com` | nginx only |
| `{{ htpasswd_path }}` | `/srv/provision/generated/myapp.user-alice.0.htpasswd` | nginx only |

- `{{ var }}` placeholders are resolved at **registration time** by Jinja2.
- `${ENV_VAR}` placeholders are resolved at **container startup** by `docker compose` via `--env-file`.
