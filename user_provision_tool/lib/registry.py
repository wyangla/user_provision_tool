"""CRUD operations on user_registry.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import os
import yaml

REGISTRY_FILE = Path(
    os.environ.get("REGISTRY_FILE", str(Path(__file__).parent.parent / "user_registry.yml"))
)


def _load() -> list[dict[str, Any]]:
    if not REGISTRY_FILE.exists():
        return []
    with REGISTRY_FILE.open("r") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, list) else []


def _save(users: list[dict[str, Any]]) -> None:
    with REGISTRY_FILE.open("w") as f:
        yaml.dump(users, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_all_users() -> list[dict[str, Any]]:
    return _load()


def get_user(user_name: str) -> list[dict[str, Any]]:
    """Return all registry entries for the given user_name."""
    return [u for u in _load() if u.get("user_name") == user_name]


def get_user_service(user_name: str, service_name: str, label: str) -> dict[str, Any] | None:
    """Return the registry entry for a specific user+service+label combination."""
    for u in _load():
        if (
            u.get("user_name") == user_name
            and u.get("service_name") == service_name
            and str(u.get("label", "")) == str(label)
        ):
            return u
    return None


def add_user(entry: dict[str, Any]) -> None:
    users = _load()
    users.append(entry)
    _save(users)


def remove_user_service(user_name: str, service_name: str, label: str) -> bool:
    """Remove the entry matching user_name+service_name+label. Returns True if removed."""
    users = _load()
    before = len(users)
    users = [
        u for u in users
        if not (
            u.get("user_name") == user_name
            and u.get("service_name") == service_name
            and str(u.get("label", "")) == str(label)
        )
    ]
    if len(users) == before:
        return False
    _save(users)
    return True
