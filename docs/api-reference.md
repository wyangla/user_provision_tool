# API Reference

The provision-api exposes a REST API via FastAPI. By default it listens on port `8765`.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `POST` | `/users` | Register a user and start their containers |
| `GET` | `/users` | Status of all registered users |
| `GET` | `/users/{user_name}` | Status of one user |
| `DELETE` | `/users/{user_name}/services/{service_name}/{label}` | Stop and deregister a service |
| `POST` | `/users/{user_name}/services/{service_name}/{label}/rebuild` | Rebuild and restart containers |

---

## `GET /health`

Liveness probe — does not touch Docker.

**Response `200`**
```json
{ "status": "ok" }
```

---

## `POST /users` — Register

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `user_name` | string | ✓ | Alphanumeric + underscore |
| `service_name` | string | ✓ | Alphanumeric + underscore |
| `project_root` | string | — | Base directory for this service. Accepts a **bare name** (`"myapp"`), a relative path, or an absolute path. A bare name (no `/`, doesn't exist as a dir) resolves to `SOURCE_PROJECTS_DIR/myapp` — which is `$PROVISION_DIR/source_projects/myapp` by default. Equivalent to `-pr` in the CLI. Returns `404` if the resolved directory does not exist. |
| `compose_file_path` | string | † | Filename (when `project_root` set) or absolute path inside the container to a **plain** `docker-compose.yml`; auto-converted to a `.j2` template on every registration |
| `compose_template_path` | string | † | Filename (when `project_root` set) or absolute path inside the container to an existing `.j2` compose template |
| `nginx_conf_file_path` | string | — | Filename (when `project_root` set) or absolute path inside the container to a **plain** nginx conf; auto-converted to a `.j2` template |
| `nginx_conf_template_path` | string | — | Filename (when `project_root` set) or absolute path inside the container to an existing `.j2` nginx conf template |
| `env_file_path` | string | — | Filename (when `project_root` set) or absolute path inside the container to a `.env` file for Docker Compose variable substitution |
| `label` | string | — | Digits only; default `"0"` |
| `domain` | string | — | Domain for nginx `server_name`; default `"localhost"` |
| `passwd` | string | — | Plain-text password; default `"123456"`. Hashed with bcrypt before storage. Pass `""` to disable auth entirely (no `.htpasswd` written, `auth_basic` lines stripped from nginx conf) |
| `volumes` | object | — | `{ "template_vol_key": "/host/path", ... }` |
| `build_args` | object | — | `{ "HTTP_PROXY": "http://proxy:8080", ... }` — passed as `--build-arg` to `docker compose build` (run before `compose up` when provided). Stored in registry for future rebuilds. |

> † Exactly one of `compose_file_path` or `compose_template_path` must be provided.

**Example** (simplest: bare service name as `project_root` + filenames)
```json
{
  "user_name": "alice",
  "service_name": "myapp",
  "project_root": "myapp",
  "compose_file_path": "docker-compose.yml",
  "nginx_conf_file_path": "nginx.conf",
  "env_file_path": ".env",
  "domain": "example.com",
  "passwd": "secret"
}
```

**Response `201`**
```json
{
  "status": "registered",
  "entry": {
    "user_name": "alice",
    "service_name": "myapp",
    "label": "0",
    "compose_file_path": "/srv/provision/source_projects/myapp/docker-compose.user-alice.0.yml",
    "nginx_conf_path": null,
    "htpasswd_path": null,
    "env_file_path": "/srv/provision/source_projects/myapp/myapp.env",
    "volumes": { "app_data": "/srv/provision/user-data/alice/app" }
  },
  "volume_warnings": { "missing": [], "extra": [] }
}
```

> **Note on `htpasswd_path`**: this field is `null` in the response when `passwd` is empty (no-auth mode). When a password is provided, it points to the written `.htpasswd` file.

> **Path resolution**: when `project_root` is given, all relative file-path fields are resolved against it. Without `project_root`, every path field must be an **absolute path inside the container** (e.g. `/provision/source_projects/myapp/docker-compose.yml` when `PROVISION_DIR=/provision`).
>
> **Output locations**: the rendered compose file is written into the resolved project root directory, so that `build: .` references resolve correctly. Nginx conf and `.htpasswd` files are always written into `GENERATED_DIR` (`$PROVISION_DIR/generated` by default).

**Error codes**

| Code | Cause |
|---|---|
| `404` | Template/env file not found, or bare `project_root` not found under `SOURCE_PROJECTS_DIR` |
| `409` | The `user_name` + `service_name` + `label` combination is already registered |
| `422` | Validation error on `user_name`, `service_name`, or `label` format |
| `500` | `docker compose up` failed; error message includes stderr output for diagnosis |

---

## `DELETE /users/{user_name}/services/{service_name}/{label}` — Remove

Runs `docker compose down` then removes the registry entry.

**Response `200`**
```json
{ "status": "removed", "user_name": "alice", "service_name": "myapp", "label": "0" }
```

**Error codes**

| Code | Cause |
|---|---|
| `404` | No registration found |
| `500` | `docker compose down` failed |

---

## `POST /users/{user_name}/services/{service_name}/{label}/rebuild`

Runs `docker compose build` then `docker compose up -d`.

**Request body** (optional)

| Field | Type | Default | Description |
|---|---|---|---|
| `no_cache` | bool | `false` | Pass `--no-cache` to `docker compose build` |
| `build_args` | object | — | `{ "HTTP_PROXY": "http://proxy:8080", ... }` — passed as `--build-arg` to `docker compose build`. Overrides registry-stored values when provided. |

**Response `200`**
```json
{ "status": "rebuilt", "user_name": "alice", "service_name": "myapp", "label": "0" }
```

**Error codes**

| Code | Cause |
|---|---|
| `404` | No registration or generated compose file not found |
| `500` | Build or up failed |

---

## `GET /users` — All Users Status

Returns the health of every registered user's services.

**Response `200`** — see [Status Response Schema](#status-response-schema).

---

## `GET /users/{user_name}` — Single User Status

**Error codes**

| Code | Cause |
|---|---|
| `404` | No registrations found for that user |

**Response `200`** — see [Status Response Schema](#status-response-schema).

---

## Status Response Schema

```
GET /users/alice

{
  "user_status": [
    {
      "user_name": "alice",
      "summary": {
        "expected_services_#": 1,
        "healthy_services_#": 1,
        "unhealthy_services_#": 0
      },
      "healthy_services": [
        {
          "service_name": "myapp",
          "label": "0",
          "compose_file_path": "...",
          "healthy_containers":   { "myapp-user_alice-0-web": "Up 3 hours" },
          "unhealthy_containers": {},
          "missing_containers":   {}
        }
      ],
      "unhealthy_services": [],
      "missing_services": []
    }
  ]
}
```

A service is **healthy** when all containers declared in its compose file are running with status `Up`.
A service is **missing** when its compose file does not exist (e.g. was deleted externally).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GENERATED_DIR` | `./generated` | Directory for nginx conf, htpasswd, and `user_registry.yml` |
| `REGISTRY_FILE` | `./user_registry.yml` | Path to the registry state file |
| `DOCKER_OPS_LOG` | _(unset)_ | If set, path to a file where all docker command stdout/stderr is appended for debugging (e.g. `${PROVISION_DIR}/generated/docker_ops.log`) |
| `PROVISION_API_PORT` | `8765` | Host port (set in `docker-compose.provision.yml`) |
