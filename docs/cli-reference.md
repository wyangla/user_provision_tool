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
| `--compose-template` | `-tc` | ✓ | Path to the `.j2` compose template |
| `--volume` | `-v` | — | `KEY=VALUE` volume mapping (repeatable) |
| `--env-file` | `-e` | — | Path to a `.env` file for Docker Compose variable substitution |
| `--nginx-template` | `-tn` | — | Path to the `.j2` nginx conf template |
| `--label` | `-l` | — | Digits only; default `0` |
| `--domain` | `-d` | — | Domain for nginx `server_name`; default `localhost` |

### Behaviour

```
parse args
  │
  ├─ validate user_name, service_name, label
  ├─ compare template volumes vs --volume flags → warn + prompt on mismatch
  ├─ prompt for password interactively (skippable)
  ├─ append entry to user_registry.yml
  ├─ render docker-compose.{svc}-user_{user}-{label}.yml
  ├─ copy .env to generated/  (if --env-file given)
  ├─ render nginx conf  (if --nginx-template given)
  └─ docker compose up -d
```

### Example

```bash
python cli/register.py \
  -u alice \
  -sn myapp \
  -tc /srv/provision/templates/myapp.yml.j2 \
  -v app_data=/srv/provision/user-data/alice/app \
  -v db_data=/srv/provision/user-data/alice/db \
  -e /srv/provision/templates/myapp.env \
  -tn /srv/provision/templates/myapp.nginx.conf.j2 \
  -d example.com \
  -l 0
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

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GENERATED_DIR` | `<project-root>/generated` | Where rendered files are written |
| `REGISTRY_FILE` | `<project-root>/user_registry.yml` | Registry state file |
