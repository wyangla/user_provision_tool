"""cli/status.py — query health status of user service containers.

Usage:
    python cli/status.py [-u USER_NAME]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import docker_ops, registry


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Query container health status for users.")
    p.add_argument("-u", "--user-name", default=None, help="User name (omit for all users)")
    return p.parse_args()


def _expected_services_from_compose(compose_file: str) -> list[str]:
    """Parse service names from a generated compose file."""
    if not Path(compose_file).exists():
        return []
    with open(compose_file) as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("services", {}).keys())


def _container_name_for_service(entry: dict, svc_key: str) -> str:
    """Derive the expected container name: {service_name}-user_{user_name}-{label}-{svc_key}."""
    prefix = f"{entry['service_name']}-user_{entry['user_name']}-{entry['label']}-"
    return f"{prefix}{svc_key}"


def _build_service_status(entry: dict, running: dict[str, str]) -> dict[str, Any]:
    compose_file = entry.get("compose_file_path", "")
    expected_keys = _expected_services_from_compose(compose_file)

    healthy: dict[str, str] = {}
    unhealthy: dict[str, str] = {}
    missing: dict[str, str] = {}

    for svc_key in expected_keys:
        cname = _container_name_for_service(entry, svc_key)
        if cname in running:
            status = running[cname]
            # Docker status strings containing "healthy" or "Up" (without "unhealthy") are healthy
            status_lower = status.lower()
            if "unhealthy" in status_lower:
                unhealthy[cname] = status
            elif "up" in status_lower or "healthy" in status_lower:
                healthy[cname] = status
            else:
                unhealthy[cname] = status
        else:
            missing[cname] = "not running"

    is_healthy = len(healthy) == len(expected_keys) and not unhealthy and not missing

    return {
        "service_name": entry.get("service_name", ""),
        "label": entry.get("label", ""),
        "compose_template_path": entry.get("compose_template_path", ""),
        "compose_file_path": compose_file,
        "healthy_containers": healthy,
        "unhealthy_containers": unhealthy,
        "missing_containers": missing,
        "_is_healthy": is_healthy,
    }


def _status_for_user(user_name: str, running: dict[str, str]) -> dict[str, Any]:
    entries = registry.get_user(user_name)

    healthy_services: list[dict] = []
    unhealthy_services: list[dict] = []
    missing_services: list[dict] = []

    for entry in entries:
        compose_file = entry.get("compose_file_path", "")
        if not compose_file or not Path(compose_file).exists():
            # Whole service is missing
            svc = {
                "service_name": entry.get("service_name", ""),
                "label": entry.get("label", ""),
                "compose_template_path": entry.get("compose_template_path", ""),
                "compose_file_path": compose_file,
                "healthy_containers": {},
                "unhealthy_containers": {},
                "missing_containers": {},
            }
            missing_services.append(svc)
            continue

        svc = _build_service_status(entry, running)
        is_healthy = svc.pop("_is_healthy")
        if is_healthy:
            healthy_services.append(svc)
        elif svc["missing_containers"] and not svc["healthy_containers"] and not svc["unhealthy_containers"]:
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


def main() -> None:
    args = parse_args()

    # Build a lookup of running containers: name -> status
    running_list = docker_ops.docker_ps()
    running: dict[str, str] = {c["name"]: c["status"] for c in running_list}

    all_users = registry.get_all_users()
    if args.user_name:
        user_names = list({u["user_name"] for u in all_users if u.get("user_name") == args.user_name})
        if not user_names:
            print(f"ERROR: No registrations found for user '{args.user_name}'.", file=sys.stderr)
            sys.exit(1)
    else:
        user_names = list({u["user_name"] for u in all_users})

    result: dict[str, Any] = {
        "user_status": [_status_for_user(name, running) for name in sorted(user_names)]
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
