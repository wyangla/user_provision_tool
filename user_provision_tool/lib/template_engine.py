"""Jinja2-based template rendering for compose and nginx files.

Template variables available in both compose and nginx templates:
  {{ user_name }}
  {{ service_name }}
  {{ label }}
  {{ domain_name }}
  {{ container_prefix }}   ->  {service_name}-user_{user_name}-{label}-
  {{ htpasswd_path }}      ->  absolute path to the generated .htpasswd file (nginx only)
  {{ volumes }}            ->  dict of host_path -> container_path mappings

For compose templates, each service name and container_name referencing another service
should use the container_prefix so inter-service communication works by generated name.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined


def _make_env(template_path: str) -> tuple[Environment, str]:
    tpl = Path(template_path).resolve()
    env = Environment(
        loader=FileSystemLoader(str(tpl.parent)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env, tpl.name


def container_prefix(service_name: str, user_name: str, label: str) -> str:
    return f"{service_name}-user_{user_name}-{label}-"


def user_network_name(service_name: str, user_name: str, label: str) -> str:
    """Return the isolated Docker network name for a user service instance."""
    return f"{service_name}-user_{user_name}-{label}"


class _PathPlaceholderUndefined(Undefined):
    """Renders as a valid absolute path placeholder so YAML stays parseable."""
    def __str__(self) -> str:
        return "/tmp/__placeholder__"

    def __getitem__(self, key: object) -> "_PathPlaceholderUndefined":
        return _PathPlaceholderUndefined()

    def __iter__(self):
        return iter([])


def extract_template_volumes(compose_template_path: str) -> list[str]:
    """Return the list of volume keys referenced in a compose template.

    Three sources are scanned and merged (in order, de-duplicated):
    1. ``{{ volumes['key'] }}`` / ``{{ volumes["key"] }}`` Jinja2 expressions
       — covers bind-mount sources substituted at render time.
    2. Top-level named ``volumes:`` block — covers named Docker volumes.
    3. Plain (non-Jinja2) bind-mount source paths in service volumes lists.
    """
    with open(compose_template_path) as f:
        content = f.read()

    # --- 1. Jinja2 dict-access patterns: {{ volumes['key'] }} or {{ volumes["key"] }} ---
    _JINJA_VOL_RE = re.compile(r'\{\{-?\s*volumes\[[\'"]([^\'"]+)[\'"]\]\s*-?\}\}')
    jinja_keys: list[str] = []
    for m in _JINJA_VOL_RE.finditer(content):
        key = m.group(1)
        if key not in jinja_keys:
            jinja_keys.append(key)

    # --- 2 & 3. Render with placeholder values and parse the YAML ---
    try:
        env = Environment(undefined=_PathPlaceholderUndefined)
        rendered = env.from_string(content).render()
    except Exception:
        rendered = content
    try:
        data = yaml.safe_load(rendered)
    except yaml.YAMLError:
        data = {}
    top_volumes = list((data or {}).get("volumes", {}).keys()) if isinstance(
        (data or {}).get("volumes"), dict
    ) else []
    # Also collect bind-mount sources from services
    service_volumes: list[str] = []
    for svc in ((data or {}).get("services", {}) or {}).values():
        for v in (svc or {}).get("volumes", []):
            if isinstance(v, str) and ":" in v:
                src = v.split(":")[0]
                if not src.startswith("/") and src not in top_volumes:
                    service_volumes.append(src)
            elif isinstance(v, dict):
                src = v.get("source", "")
                if src and not src.startswith("/") and src not in top_volumes:
                    service_volumes.append(src)

    # Merge all three sources, preserving order and de-duplicating
    seen: set[str] = set()
    result: list[str] = []
    for key in jinja_keys + top_volumes + service_volumes:
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def render_compose(
    template_path: str,
    output_path: str,
    user_name: str,
    service_name: str,
    label: str,
    volumes: dict[str, str],
    env_file: str | None = None,
) -> str | None:
    """Render a docker-compose template and write the output file.

    If *env_file* is given, it is copied next to the generated compose file
    so ``docker compose --env-file`` can reference it.  Returns the copied
    path (a sibling of *output_path*), or None if no env_file was supplied.

    Two distinct placeholder types are handled by different engines:
      - ``{{ var }}``   — Jinja2; resolved here at render time.
      - ``${ENV_VAR}``  — Docker Compose variable substitution; left as-is
                          in the rendered YAML and resolved by docker at
                          ``compose up`` time via the env_file.
    """
    env, tpl_name = _make_env(template_path)
    prefix = container_prefix(service_name, user_name, label)
    ctx: dict[str, Any] = {
        "user_name": user_name,
        "service_name": service_name,
        "label": label,
        "container_prefix": prefix,
        "network_name": user_network_name(service_name, user_name, label),
        "volumes": volumes,
    }
    rendered = env.get_template(tpl_name).render(**ctx)
    with open(output_path, "w") as f:
        f.write(rendered)

    copied_env: str | None = None
    if env_file and Path(env_file).is_file():
        dest = Path(output_path).parent / Path(env_file).name
        shutil.copy2(env_file, dest)
        copied_env = str(dest)
    return copied_env


def render_nginx_conf(
    template_path: str,
    output_path: str,
    user_name: str,
    service_name: str,
    label: str,
    domain_name: str,
    htpasswd_path: str,
) -> None:
    """Render a nginx conf template and write the output file."""
    env, tpl_name = _make_env(template_path)
    prefix = container_prefix(service_name, user_name, label)
    ctx: dict[str, Any] = {
        "user_name": user_name,
        "service_name": service_name,
        "label": label,
        "domain_name": domain_name,
        "container_prefix": prefix,
        "network_name": user_network_name(service_name, user_name, label),
        "hostname": f"{service_name}-{user_name}-{label}.{domain_name}",
        "htpasswd_path": htpasswd_path,
    }
    rendered = env.get_template(tpl_name).render(**ctx)
    with open(output_path, "w") as f:
        f.write(rendered)
