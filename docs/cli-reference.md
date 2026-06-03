# CLI Reference

The `cli/` package provides four command-line scripts that mirror the REST API.
They are useful for scripted automation or for running on the host directly (without the API container).

All scripts must be run from the project root or with `PYTHONPATH` set to the project root:
```bash
python cli/register.py ...
# or
cd /path/to/user_provision_tool && python cli/register.py ...
```

---

## `cli/register.py` — Register a user

Start a user's containers from a Jinja2 compose template.

### Arguments

| Flag | Short | Required | Description |
|---|---|---|---|
| `--user-name` | `-u` | ✓ | User name (`[a-zA-Z0-9_]+`) |
| `--service-name` | `-sn` | ✓ | Service name (`[a-zA-Z0-9_]+`) |
| `--project-root` | `-pr` | ✓ | Project root directory; all filenames are resolved relative to this path. Accepts a **bare name** (`myapp`), a relative path, or an absolute path. A bare name (no `/`, does not exist as a dir) resolves to `$SOURCE_PROJECTS_DIR/myapp` — which is `$PROVISION_DIR/source_projects/myapp` by default; override via the `SOURCE_PROJECTS_DIR` env var. Returns an error if the resolved directory does not exist. |
| `--compose-template` | `-tc` | ✓¹ | Filename of an existing `.j2` compose template inside project root |
| `--compose-file` | `-fc` | ✓¹ | Filename of a plain `docker-compose.yml` inside project root; auto-converted to `.j2` |
| `--nginx-template` | `-tn` | — | Filename of an existing `.j2` nginx conf template inside project root |
| `--nginx-file` | `-fn` | — | Filename of a plain nginx conf inside project root; auto-converted to `.j2` |
| `--volume` | `-v` | — | `KEY=VALUE` volume mapping (repeatable) |
| `--env-file` | `-e` | — | Path to a `.env` file for Docker Compose variable substitution |
| `--label` | `-l` | — | Digits only; default `0` |
| `--domain` | `-d` | — | Domain for nginx `server_name`; default `localhost` |

¹ `-tc` and `-fc` are mutually exclusive; exactly one is required. `-tn` and `-fn` are mutually exclusive and both optional.

### Behaviour

```
parse args
  │
  ├─ (-fc only) compose_converter → write docker-compose.plain.yml.j2 into project root
  ├─ (-fn only) nginx_converter    → write nginx.plain.conf.j2        into project root
  │
  ├─ validate user_name, service_name, label
  ├─ compare template volumes vs --volume flags → warn + prompt on mismatch
  ├─ prompt for password interactively (Enter to use default `123456`; type empty = no auth)
  │
  ├─ provisioner.register_user()
  │       ├─ append entry to user_registry.yml
  │       ├─ render docker-compose.user-{user}.{label}.yml  → project root
  │       ├─ copy .env next to compose file  (if --env-file given)
  │       ├─ render nginx conf + write .htpasswd  → GENERATED_DIR  (if -tn/-fn given)
  │       ├─ docker compose up -d
  │       └─ docker network connect + nginx reload
  └─ print summary
```

### Example

```bash
# Simplest: bare name as project root (resolves to SOURCE_PROJECTS_DIR/myapp = $PROVISION_DIR/source_projects/myapp)
python cli/register.py \
  -u alice \
  -sn myapp \
  -pr myapp \
  -fc docker-compose.yml \
  -fn nginx.conf \
  -e .env \
  -d example.com

# Using a pre-made Jinja2 template with explicit full path
python cli/register.py \
  -u alice \
  -sn myapp \
  -pr /srv/provision/source_projects/myapp \
  -tc docker-compose.myapp.yml.j2 \
  -v app_data=/srv/provision/user-data/alice/app \
  -v db_data=/srv/provision/user-data/alice/db \
  -e /srv/provision/source_projects/myapp/myapp.env \
  -tn myapp.nginx.conf.j2 \
  -d example.com \
  -l 0

# Using a plain compose file (auto-converted to .j2 on first use) with full path
python cli/register.py \
  -u alice \
  -sn myapp \
  -pr /srv/provision/source_projects/myapp \
  -fc docker-compose.yml \
  -fn nginx.conf \
  -v app_data=/srv/provision/user-data/alice/app \
  -d example.com
```

---

## `cli/remove.py` — Remove a user

Stop containers and deregister a user's service.

### Arguments

| Flag | Short | Required | Description |
|---|---|---|---|
| `--user-name` | `-u` | ✓ | User name |
| `--service-name` | `-sn` | ✓ | Service name |
| `--label` | `-l` | ✓ | Label |

### Example

```bash
python cli/remove.py -u alice -sn myapp -l 0
```

---

## `cli/rebuild.py` — Rebuild a user's containers

Run `docker compose build` followed by `docker compose up -d`. Useful after updating a service image.

### Arguments

| Flag | Short | Required | Description |
|---|---|---|---|
| `--user-name` | `-u` | ✓ | User name |
| `--service-name` | `-sn` | ✓ | Service name |
| `--label` | `-l` | ✓ | Label |
| `--no-cache` | — | — | Build without Docker layer cache |

### Example

```bash
python cli/rebuild.py -u alice -sn myapp -l 0 --no-cache
```

---

## `cli/status.py` — Query health status

Print a JSON health report for one user or all users.

### Arguments

| Flag | Short | Required | Description |
|---|---|---|---|
| `--user-name` | `-u` | — | User name; omit to show all users |

### Example

```bash
# All users
python cli/status.py

# One user
python cli/status.py -u alice
```

### Output

```json
{
  "user_status": [
    {
      "user_name": "alice",
      "summary": {
        "expected_services_#": 1,
        "healthy_services_#": 1,
        "unhealthy_services_#": 0
      },
      "healthy_services": [ ... ],
      "unhealthy_services": [],
      "missing_services": []
    }
  ]
}
```

---

## `cli/gen_compose_template.py` — Convert compose file to template

Standalone tool to convert a plain `docker-compose.yml` into a reusable Jinja2 template.
Equivalent to using `-fc` in `register.py` but without registering a user.

### Arguments

| Argument | Required | Description |
|---|---|---|
| `input` | ✓ | Path to the source `docker-compose.yml` |
| `-o` / `--output` | — | Output path; defaults to `<input-stem>.yml.j2` in the same directory |
| `-s` / `--service-name` | — | Service name hint for the header comment (default: input file stem) |

### Example

```bash
python cli/gen_compose_template.py \
  source_projects/myapp/docker-compose.yml \
  -s myapp
# writes source_projects/myapp/docker-compose.yml.j2
```

---

## `cli/gen_nginx_template.py` — Convert nginx conf to template

Standalone tool to convert a plain nginx conf file into a reusable Jinja2 template.
Equivalent to using `-fn` in `register.py` but without registering a user.

### Arguments

| Argument | Required | Description |
|---|---|---|
| `input` | ✓ | Path to the source nginx conf file |
| `-o` / `--output` | — | Output path; defaults to `<input-stem>.nginx.conf.j2` in the same directory |
| `-s` / `--service-name` | — | Service name hint for rewriting `proxy_pass` container names (default: input file stem) |

### Example

```bash
python cli/gen_nginx_template.py \
  source_projects/myapp/nginx.conf \
  -s myapp
# writes source_projects/myapp/nginx.conf.j2
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GENERATED_DIR` | `<project-root>/generated` | Where nginx conf, htpasswd, and `user_registry.yml` are written |
| `REGISTRY_FILE` | `<project-root>/user_registry.yml` | Registry state file |
