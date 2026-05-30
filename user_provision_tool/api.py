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
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

# Make lib/ importable when the file sits at the project root
sys.path.insert(0, str(Path(__file__).parent))

from lib import auth, docker_ops, registry, template_engine, validation

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GENERATED_DIR = Path(
    os.environ.get("GENERATED_DIR", str(Path(__file__).parent / "generated"))
)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

# The registry module reads REGISTRY_FILE from its own env var at import time.
# We additionally sync it here so both the API and the lib use the same path.
import lib.registry as _reg_mod
_reg_path = os.environ.get(
    "REGISTRY_FILE",
    str(Path(__file__).parent / "user_registry.yml"),
)
_reg_mod.REGISTRY_FILE = Path(_reg_path)

# Registry writes are not atomic; a lock prevents concurrent corruption.
_registry_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    user_name: str
    service_name: str
    compose_template_path: str
    nginx_conf_template_path: str | None = None
    env_file_path: str | None = None
    label: str = "0"
    domain: str = "localhost"
    passwd: str = ""
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
    # Template files must be reachable inside the container
    if not Path(req.compose_template_path).exists():
        raise HTTPException(404, f"compose_template_path not found: {req.compose_template_path}")
    if req.nginx_conf_template_path and not Path(req.nginx_conf_template_path).exists():
        raise HTTPException(404, f"nginx_conf_template_path not found: {req.nginx_conf_template_path}")

    with _registry_lock:
        if registry.get_user_service(req.user_name, req.service_name, req.label):
            raise HTTPException(
                409,
                f"User '{req.user_name}' with service '{req.service_name}' "
                f"and label '{req.label}' is already registered.",
            )

        # Volume cross-check (warn in response, never block)
        expected_vols = template_engine.extract_template_volumes(req.compose_template_path)
        missing_vols = [k for k in expected_vols if k not in req.volumes]
        extra_vols = [k for k in req.volumes if k not in expected_vols]

        # Hash password
        passwd_hash = auth.hash_password(req.user_name, req.passwd) if req.passwd else ""

        # Output paths
        compose_out = str(
            GENERATED_DIR / f"docker-compose.user-{req.user_name}.{req.label}.yml"
        )
        nginx_out: str | None = None
        htpasswd_out: str | None = None
        if req.nginx_conf_template_path:
            nginx_out = str(
                GENERATED_DIR / f"{req.service_name}.user-{req.user_name}.{req.label}.nginx.conf"
            )
            htpasswd_out = str(
                GENERATED_DIR / f"{req.service_name}.user-{req.user_name}.{req.label}.htpasswd"
            )

        # Registry entry
        entry: dict[str, Any] = {
            "user_name": req.user_name,
            "passwd": passwd_hash,
            "service_name": req.service_name,
            "label": req.label,
            "compose_template_path": req.compose_template_path,
            "nginx_conf_template_path": req.nginx_conf_template_path,
            "env_file_path": req.env_file_path,
            "compose_file_path": compose_out,
            "nginx_conf_path": nginx_out,
            "htpasswd_path": htpasswd_out,
            "volumes": req.volumes,
        }
        registry.add_user(entry)

    # Render compose file — also copies env_file to generated/ if provided
    copied_env = template_engine.render_compose(
        req.compose_template_path, compose_out,
        req.user_name, req.service_name, req.label, req.volumes,
        env_file=req.env_file_path,
    )

    # Render nginx conf
    if req.nginx_conf_template_path and nginx_out and htpasswd_out:
        if passwd_hash:
            auth.write_htpasswd_file(htpasswd_out, req.user_name, passwd_hash)
        htpasswd_render = htpasswd_out if passwd_hash else ""
        template_engine.render_nginx_conf(
            req.nginx_conf_template_path, nginx_out,
            req.user_name, req.service_name, req.label,
            req.domain, htpasswd_render,
        )

    # Start containers
    try:
        docker_ops.compose_up(compose_out, env_file=copied_env)
    except RuntimeError as e:
        # Rollback registry on docker failure
        with _registry_lock:
            registry.remove_user_service(req.user_name, req.service_name, req.label)
        raise HTTPException(500, f"docker compose up failed: {e}")

    return {
        "status": "registered",
        "entry": entry,
        "volume_warnings": {
            "missing": missing_vols,
            "extra": extra_vols,
        },
    }


# ---------------------------------------------------------------------------
# DELETE /users/{user_name}/services/{service_name}/{label}  — remove
# ---------------------------------------------------------------------------

@app.delete("/users/{user_name}/services/{service_name}/{label}")
def remove_user(user_name: str, service_name: str, label: str) -> dict[str, str]:
    entry = registry.get_user_service(user_name, service_name, label)
    if not entry:
        raise HTTPException(404, f"No registration found for {user_name}/{service_name}/{label}.")

    compose_file = entry.get("compose_file_path", "")
    if compose_file and Path(compose_file).exists():
        try:
            docker_ops.compose_down(compose_file, env_file=entry.get("env_file_path") or None)
        except RuntimeError as e:
            raise HTTPException(500, f"docker compose down failed: {e}")

    with _registry_lock:
        registry.remove_user_service(user_name, service_name, label)

    return {"status": "removed", "user_name": user_name, "service_name": service_name, "label": label}


# ---------------------------------------------------------------------------
# POST /users/{user_name}/services/{service_name}/{label}/rebuild
# ---------------------------------------------------------------------------

@app.post("/users/{user_name}/services/{service_name}/{label}/rebuild")
def rebuild_user(
    user_name: str, service_name: str, label: str,
    req: RebuildRequest = RebuildRequest(),
) -> dict[str, str]:
    entry = registry.get_user_service(user_name, service_name, label)
    if not entry:
        raise HTTPException(404, f"No registration found for {user_name}/{service_name}/{label}.")

    compose_file = entry.get("compose_file_path", "")
    if not compose_file or not Path(compose_file).exists():
        raise HTTPException(404, f"Compose file not found: {compose_file}")

    env_file = entry.get("env_file_path") or None
    try:
        docker_ops.compose_build(compose_file, no_cache=req.no_cache, env_file=env_file)
        docker_ops.compose_up(compose_file, env_file=env_file)
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
