"""lib/provisioner.py — core provisioning workflow shared by CLI and API.

All three operations (register, remove, rebuild) are implemented here so that
both ``cli/`` scripts and ``api.py`` delegate to the same logic without
duplicating it.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import auth, docker_ops, registry, template_engine

# Registry writes must be atomic across threads (relevant when the API handles
# concurrent requests).
_registry_lock = threading.Lock()


def _auto_volumes(
    compose_template: str,
    user_name: str,
    service_name: str,
    label: str,
    user_data_dir: Path,
) -> dict[str, str]:
    """Create per-volume host directories and return the volume → host-path mapping.

    Directories are created at:
        ``{user_data_dir}/{user_name}/{service_name}/{label}/{volume_key}/``

    The returned dict can be passed directly to ``register_user`` as ``volumes``.
    """
    keys = template_engine.extract_template_volumes(compose_template)
    base = user_data_dir / user_name / service_name / label
    result: dict[str, str] = {}
    for key in keys:
        vol_dir = base / key
        vol_dir.mkdir(parents=True, exist_ok=True)
        result[key] = str(vol_dir)
    return result


def register_user(
    *,
    user_name: str,
    service_name: str,
    label: str,
    compose_template: str,
    output_dir: str | Path,
    volumes: dict[str, str] | None = None,
    passwd: str = "",
    nginx_template: str | None = None,
    domain: str = "localhost",
    env_file: str | None = None,
    nginx_container: str = "provision-nginx",
    nginx_output_dir: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Register a user and start their service containers.

    Steps
    -----
    1. Volume cross-check (returned in result, callers decide how to present).
    2. Duplicate-registration check (atomic with step 3).
    3. Add registry entry.
    4. Render compose file (optionally copies env_file).
    5. Render nginx conf + write htpasswd file.
    6. ``docker compose up``.
    7. Connect provision-nginx to the user's isolated network + reload.

    Parameters
    ----------
    compose_template:
        Absolute path to a Jinja2 compose template (.yml.j2).
    output_dir:
        Directory where the rendered compose file is written (should be the
        source project root so ``build: .`` contexts resolve correctly).
    nginx_output_dir:
        Directory where nginx conf and htpasswd files are written.  Defaults
        to ``output_dir`` when not provided.
    user_data_dir:
        When provided and *volumes* is ``None`` or empty, host directories are
        automatically created under ``{user_data_dir}/{user_name}/{service_name}/{label}/{vol_key}/``
        and the resulting mapping is used as the volume dict.  When *volumes*
        is explicitly supplied it takes precedence over this parameter.
    passwd:
        Plain-text password.  Empty string → no htpasswd file written.
    nginx_container:
        Name of the provision-nginx Docker container.

    Returns
    -------
    dict with keys:
        ``entry``           — the registry entry dict
        ``volume_warnings`` — ``{"missing": [...], "extra": [...]}``
        ``copied_env``      — absolute path to the copied .env file, or ``None``

    Raises
    ------
    ValueError
        If user/service/label is already registered.
    RuntimeError
        If ``docker compose up`` fails.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    nginx_dir = Path(nginx_output_dir) if nginx_output_dir else output_dir
    nginx_dir.mkdir(parents=True, exist_ok=True)

    # --- Auto-generate volumes if not supplied ---
    if not volumes:
        if user_data_dir:
            volumes = _auto_volumes(
                compose_template, user_name, service_name, label,
                Path(user_data_dir),
            )
        else:
            volumes = {}

    # --- Volume cross-check (informational; callers decide how to surface) ---
    expected_vols = template_engine.extract_template_volumes(compose_template)
    missing_vols = [k for k in expected_vols if k not in volumes]
    extra_vols = [k for k in volumes if k not in expected_vols]

    # --- Hash password ---
    passwd_hash = auth.hash_password(user_name, passwd) if passwd else ""

    # --- Output paths ---
    compose_out = str(output_dir / f"docker-compose.user-{user_name}.{label}.yml")
    nginx_out: str | None = None
    htpasswd_out: str | None = None
    if nginx_template:
        nginx_out = str(nginx_dir / f"{service_name}.user-{user_name}.{label}.nginx.conf")
        if passwd_hash:
            htpasswd_out = str(nginx_dir / f"{service_name}.user-{user_name}.{label}.htpasswd")

    # --- Registry entry ---
    entry: dict[str, Any] = {
        "user_name": user_name,
        "passwd": passwd_hash,
        "service_name": service_name,
        "label": label,
        "network_name": template_engine.user_network_name(service_name, user_name, label),
        "compose_template_path": compose_template,
        "nginx_conf_template_path": nginx_template,
        "env_file_path": env_file,
        "compose_file_path": compose_out,
        "nginx_conf_path": nginx_out,
        "htpasswd_path": htpasswd_out,
        "volumes": volumes,
    }

    # Duplicate check + add are atomic to prevent concurrent registrations
    with _registry_lock:
        if registry.get_user_service(user_name, service_name, label):
            raise ValueError(
                f"User '{user_name}' with service '{service_name}' "
                f"and label '{label}' is already registered."
            )
        registry.add_user(entry)

    # --- Render compose file ---
    copied_env = template_engine.render_compose(
        compose_template, compose_out,
        user_name, service_name, label, volumes,
        env_file=env_file,
    )

    # --- Render nginx conf + htpasswd ---
    if nginx_template and nginx_out:
        if htpasswd_out:
            auth.write_htpasswd_file(htpasswd_out, user_name, passwd_hash)
        template_engine.render_nginx_conf(
            nginx_template, nginx_out,
            user_name, service_name, label,
            domain, htpasswd_out or "",
        )

    # --- Start containers ---
    try:
        docker_ops.compose_up(compose_out, env_file=copied_env, project_name=entry["network_name"])
    except RuntimeError:
        # Rollback: remove registry entry so the caller can retry
        with _registry_lock:
            registry.remove_user_service(user_name, service_name, label)
        raise

    # --- Connect provision-nginx to user network + reload ---
    net = entry["network_name"]
    docker_ops.network_connect(nginx_container, net)
    docker_ops.nginx_reload(nginx_container)

    return {
        "entry": entry,
        "volume_warnings": {"missing": missing_vols, "extra": extra_vols},
        "copied_env": copied_env,
    }


def remove_user(
    *,
    user_name: str,
    service_name: str,
    label: str,
    nginx_container: str = "provision-nginx",
) -> dict[str, str]:
    """Stop containers and remove a user's service registration.

    Steps
    -----
    1. Disconnect provision-nginx from the user's network.
    2. ``docker compose down``.
    3. Reload provision-nginx.
    4. Remove registry entry.

    Raises
    ------
    KeyError
        If no registration is found.
    RuntimeError
        If ``docker compose down`` fails.
    """
    entry = registry.get_user_service(user_name, service_name, label)
    if not entry:
        raise KeyError(
            f"No registration found for {user_name}/{service_name}/{label}."
        )

    compose_file = entry.get("compose_file_path", "")
    net = entry.get("network_name", "")

    # Disconnect nginx before compose_down so Docker can remove the network
    if net:
        docker_ops.network_disconnect(nginx_container, net)

    if compose_file and Path(compose_file).exists():
        docker_ops.compose_down(compose_file, env_file=entry.get("env_file_path") or None, project_name=entry.get("network_name"))

    docker_ops.nginx_reload(nginx_container)

    with _registry_lock:
        registry.remove_user_service(user_name, service_name, label)

    return {"user_name": user_name, "service_name": service_name, "label": label}


def rebuild_user(
    *,
    user_name: str,
    service_name: str,
    label: str,
    no_cache: bool = False,
) -> dict[str, str]:
    """Rebuild and restart a user's service containers.

    Steps
    -----
    1. ``docker compose build`` (optionally with ``--no-cache``).
    2. ``docker compose up``.

    Raises
    ------
    KeyError
        If no registration is found.
    FileNotFoundError
        If the rendered compose file is missing.
    RuntimeError
        If build or up fails.
    """
    entry = registry.get_user_service(user_name, service_name, label)
    if not entry:
        raise KeyError(
            f"No registration found for {user_name}/{service_name}/{label}."
        )

    compose_file = entry.get("compose_file_path", "")
    if not compose_file or not Path(compose_file).exists():
        raise FileNotFoundError(f"Compose file not found: {compose_file}")

    env_file = entry.get("env_file_path") or None
    project_name = entry.get("network_name")
    docker_ops.compose_build(compose_file, no_cache=no_cache, env_file=env_file, project_name=project_name)
    docker_ops.compose_up(compose_file, env_file=env_file, project_name=project_name)

    return {"user_name": user_name, "service_name": service_name, "label": label}
