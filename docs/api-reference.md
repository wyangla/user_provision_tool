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
| `compose_template_path` | string | ✓ | Absolute path to a `.j2` compose template inside the container |
| `nginx_conf_template_path` | string | — | Absolute path to a `.j2` nginx conf template (optional) |
| `env_file_path` | string | — | Absolute path to a `.env` file for Docker Compose variable substitution |
| `label` | string | — | Digits only; default `"0"` |
| `domain` | string | — | Domain for nginx `server_name`; default `"localhost"` |
| `passwd` | string | — | Plain-text password; hashed with bcrypt before storage |
| `volumes` | object | — | `{ "template_vol_key": "/host/path", ... }` |

**Example**
```json
{
  "user_name": "alice",
  "service_name": "myapp",
  "compose_template_path": "/srv/provision/templates/myapp.yml.j2",
  "env_file_path": "/srv/provision/templates/myapp.env",
  "label": "0",
  "domain": "example.com",
  "passwd": "secret",
  "volumes": {
    "app_data": "/srv/provision/user-data/alice/app",
    "db_data":  "/srv/provision/user-data/alice/db"
  }
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

> **Output locations**: the rendered compose file is written into the same directory as the
> `compose_template_path` (the source project root), so that `build: .` references resolve
> correctly. Nginx conf and `.htpasswd` files are written into `GENERATED_DIR`.

**Error codes**

| Code | Cause |
|---|---|
| `404` | Template file not found inside the container |
| `409` | The `user_name` + `service_name` + `label` combination is already registered |
| `422` | Validation error on `user_name`, `service_name`, or `label` format |
| `500` | `docker compose up` failed |

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
| `PROVISION_API_PORT` | `8765` | Host port (set in `docker-compose.provision.yml`) |
