"""FastAPI service for the user provision tool.

Endpoints:
  POST   /users                                           register user + start containers
  DELETE /users/{user_name}/services/{service_name}/{label}   stop + deregister
  POST   /users/{user_name}/services/{service_name}/{label}/rebuild
  GET    /users                                           status of all users
  GET    /users/{user_name}                               status of one user

Environment variables:
  GENERATED_DIR   directory for generated compose/nginx files  (default: ./generated)
  REGISTRY_FILE   path to user_registry.yml                    (default: ./user_registry.yml)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

# Make lib/ importable when the file sits at the project root
sys.path.insert(0, str(Path(__file__).parent))

from lib import docker_ops, provisioner, registry, template_engine, validation
from lib.compose_converter import compose_file_to_template
from lib.nginx_converter import nginx_file_to_template

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GENERATED_DIR = Path(
    os.environ.get("GENERATED_DIR", str(Path(__file__).parent / "generated"))
)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

# User volume data root: auto-created subdirectories are used when no volumes
# are explicitly provided at registration time.
USER_DATA_DIR = Path(
    os.environ.get("USER_DATA_DIR", str(GENERATED_DIR.parent / "user_data"))
)
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Source projects root: operators clone / copy service repos here.
SOURCE_PROJECTS_DIR = Path(
    os.environ.get("SOURCE_PROJECTS_DIR", str(GENERATED_DIR.parent / "source_projects"))
)
SOURCE_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

NGINX_CONTAINER = os.environ.get("NGINX_CONTAINER", "provision-nginx")

# The registry module reads REGISTRY_FILE from its own env var at import time.
# We additionally sync it here so both the API and the lib use the same path.
import lib.registry as _reg_mod
_reg_path = os.environ.get(
    "REGISTRY_FILE",
    str(Path(__file__).parent / "user_registry.yml"),
)
_reg_mod.REGISTRY_FILE = Path(_reg_path)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    user_name: str
    service_name: str
    # Exactly one of these must be provided:
    #   compose_template_path — path to an existing Jinja2 .yml.j2 template
    #   compose_file_path     — path to a plain docker-compose.yml (auto-converted)
    compose_template_path: str | None = None
    compose_file_path: str | None = None
    # Optionally one of:
    nginx_conf_template_path: str | None = None
    nginx_conf_file_path: str | None = None
    env_file_path: str | None = None
    label: str = "0"
    domain: str = "localhost"
    passwd: str = "123456"
    volumes: dict[str, str] = {}

    @field_validator("user_name", "service_name")
    @classmethod
    def _validate_name(cls, v: str, info) -> str:
        try:
            validation.validate_name(v, info.field_name)
        except validation.ValidationError as e:
            raise ValueError(str(e))
        return v

    @field_validator("label")
    @classmethod
    def _validate_label(cls, v: str) -> str:
        try:
            validation.validate_label(v)
        except validation.ValidationError as e:
            raise ValueError(str(e))
        return v

    from pydantic import model_validator

    @model_validator(mode="after")
    def _check_compose_source(self) -> "RegisterRequest":
        has_tpl = bool(self.compose_template_path)
        has_file = bool(self.compose_file_path)
        if not has_tpl and not has_file:
            raise ValueError("one of compose_template_path or compose_file_path is required")
        if has_tpl and has_file:
            raise ValueError("compose_template_path and compose_file_path are mutually exclusive")
        if self.nginx_conf_template_path and self.nginx_conf_file_path:
            raise ValueError("nginx_conf_template_path and nginx_conf_file_path are mutually exclusive")
        return self


class RebuildRequest(BaseModel):
    no_cache: bool = False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="User Provision Tool", version="1.0.0")


# ---------------------------------------------------------------------------
# GET /health  — liveness probe (no docker call)
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /users  — register
# ---------------------------------------------------------------------------

@app.post("/users", status_code=201)
def register_user(req: RegisterRequest) -> dict[str, Any]:
    # --- Resolve compose template (convert plain file if needed) ---
    if req.compose_file_path:
        src = Path(req.compose_file_path)
        if not src.exists():
            raise HTTPException(404, f"compose_file_path not found: {req.compose_file_path}")
        template_out = str(src.parent / f"{src.stem}.yml.j2")
        try:
            compose_file_to_template(str(src), template_out, service_name_hint=req.service_name)
        except Exception as e:
            raise HTTPException(422, f"could not convert compose file: {e}")
        compose_template = template_out
    else:
        if not Path(req.compose_template_path).exists():
            raise HTTPException(404, f"compose_template_path not found: {req.compose_template_path}")
        compose_template = req.compose_template_path

    # --- Resolve nginx template (convert plain file if needed) ---
    nginx_template: str | None = None
    if req.nginx_conf_file_path:
        src = Path(req.nginx_conf_file_path)
        if not src.exists():
            raise HTTPException(404, f"nginx_conf_file_path not found: {req.nginx_conf_file_path}")
        template_out = str(src.parent / f"{src.name}.j2")
        try:
            nginx_file_to_template(str(src), template_out, req.service_name)
        except Exception as e:
            raise HTTPException(422, f"could not convert nginx conf file: {e}")
        nginx_template = template_out
    elif req.nginx_conf_template_path:
        if not Path(req.nginx_conf_template_path).exists():
            raise HTTPException(404, f"nginx_conf_template_path not found: {req.nginx_conf_template_path}")
        nginx_template = req.nginx_conf_template_path

    # --- Delegate to shared provisioner ---
    try:
        result = provisioner.register_user(
            user_name=req.user_name,
            service_name=req.service_name,
            label=req.label,
            compose_template=compose_template,
            output_dir=Path(compose_template).parent,
            nginx_output_dir=GENERATED_DIR,
            volumes=req.volumes or None,
            user_data_dir=USER_DATA_DIR,
            passwd=req.passwd,
            nginx_template=nginx_template,
            domain=req.domain,
            env_file=req.env_file_path,
            nginx_container=NGINX_CONTAINER,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except RuntimeError as e:
        raise HTTPException(500, f"docker compose up failed: {e}")

    return {
        "status": "registered",
        "entry": result["entry"],
        "volume_warnings": result["volume_warnings"],
    }


# ---------------------------------------------------------------------------
# DELETE /users/{user_name}/services/{service_name}/{label}  — remove
# ---------------------------------------------------------------------------

@app.delete("/users/{user_name}/services/{service_name}/{label}")
def remove_user(user_name: str, service_name: str, label: str) -> dict[str, str]:
    try:
        provisioner.remove_user(
            user_name=user_name,
            service_name=service_name,
            label=label,
            nginx_container=NGINX_CONTAINER,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(500, f"docker compose down failed: {e}")

    return {"status": "removed", "user_name": user_name, "service_name": service_name, "label": label}


# ---------------------------------------------------------------------------
# POST /users/{user_name}/services/{service_name}/{label}/rebuild
# ---------------------------------------------------------------------------

@app.post("/users/{user_name}/services/{service_name}/{label}/rebuild")
def rebuild_user(
    user_name: str, service_name: str, label: str,
    req: RebuildRequest = RebuildRequest(),
) -> dict[str, str]:
    try:
        provisioner.rebuild_user(
            user_name=user_name,
            service_name=service_name,
            label=label,
            no_cache=req.no_cache,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(500, f"rebuild failed: {e}")

    return {"status": "rebuilt", "user_name": user_name, "service_name": service_name, "label": label}


# ---------------------------------------------------------------------------
# GET /users  — all users status
# ---------------------------------------------------------------------------

@app.get("/users")
def get_all_users_status() -> dict[str, Any]:
    return _compute_status(None)


# ---------------------------------------------------------------------------
# GET /users/{user_name}  — single user status
# ---------------------------------------------------------------------------

@app.get("/users/{user_name}")
def get_user_status(user_name: str) -> dict[str, Any]:
    entries = registry.get_user(user_name)
    if not entries:
        raise HTTPException(404, f"No registrations found for user '{user_name}'.")
    return _compute_status(user_name)


# ---------------------------------------------------------------------------
# Status computation (shared logic)
# ---------------------------------------------------------------------------

def _compute_status(filter_user: str | None) -> dict[str, Any]:
    running = {c["name"]: c["status"] for c in docker_ops.docker_ps()}
    all_users = registry.get_all_users()

    if filter_user:
        user_names = [u["user_name"] for u in all_users if u.get("user_name") == filter_user]
    else:
        user_names = list({u["user_name"] for u in all_users})

    return {"user_status": [_status_for_user(name, running) for name in sorted(user_names)]}


def _expected_services(compose_file: str) -> list[str]:
    if not Path(compose_file).exists():
        return []
    import yaml
    with open(compose_file) as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("services", {}).keys())


def _status_for_user(user_name: str, running: dict[str, str]) -> dict[str, Any]:
    entries = registry.get_user(user_name)
    healthy_services, unhealthy_services, missing_services = [], [], []

    for entry in entries:
        compose_file = entry.get("compose_file_path", "")
        prefix = template_engine.container_prefix(
            entry["service_name"], entry["user_name"], entry["label"]
        )
        expected_keys = _expected_services(compose_file)

        healthy: dict[str, str] = {}
        unhealthy: dict[str, str] = {}
        missing: dict[str, str] = {}

        for svc_key in expected_keys:
            cname = f"{prefix}{svc_key}"
            if cname in running:
                status = running[cname]
                if "unhealthy" in status.lower():
                    unhealthy[cname] = status
                elif "up" in status.lower() or "healthy" in status.lower():
                    healthy[cname] = status
                else:
                    unhealthy[cname] = status
            else:
                missing[cname] = "not running"

        svc: dict[str, Any] = {
            "service_name": entry["service_name"],
            "label": entry["label"],
            "compose_template_path": entry.get("compose_template_path", ""),
            "compose_file_path": compose_file,
            "healthy_containers": healthy,
            "unhealthy_containers": unhealthy,
            "missing_containers": missing,
        }

        if not Path(compose_file).exists():
            missing_services.append(svc)
        elif len(healthy) == len(expected_keys) and not unhealthy and not missing:
            healthy_services.append(svc)
        elif not healthy and not unhealthy:
            missing_services.append(svc)
        else:
            unhealthy_services.append(svc)

    total = len(entries)
    return {
        "user_name": user_name,
        "summary": {
            "expected_services_#": total,
            "healthy_services_#": len(healthy_services),
            "unhealthy_services_#": len(unhealthy_services) + len(missing_services),
        },
        "healthy_services": healthy_services,
        "unhealthy_services": unhealthy_services,
        "missing_services": missing_services,
    }
