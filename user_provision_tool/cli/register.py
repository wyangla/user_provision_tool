"""cli/register.py — register a user and start their service containers.

Usage:
    python cli/register.py -u USER_NAME -sn SERVICE_NAME -tc COMPOSE_TEMPLATE
        [-v KEY=VALUE ...] [-e ENV_FILE] [-tn NGINX_TEMPLATE] [-l LABEL] [-d DOMAIN]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure lib/ is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

import os

from lib import auth, docker_ops, registry, template_engine, validation

GENERATED_DIR = Path(
    os.environ.get("GENERATED_DIR", str(Path(__file__).parent.parent / "generated"))
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register a user and start their service containers.")
    p.add_argument("-u", "--user-name", required=True, help="User name")
    p.add_argument("-sn", "--service-name", required=True, help="Service name")
    p.add_argument("-tc", "--compose-template", required=True, help="Path to docker-compose template")
    p.add_argument("-v", "--volume", action="append", default=[],
        metavar="KEY=VALUE",
        help="Volume mapping (can be repeated): template_volume=host_path",
    )
    p.add_argument("-e", "--env-file", default=None, help="Path to .env file for docker compose variable substitution")
    p.add_argument("-tn", "--nginx-template", default=None, help="Path to nginx conf template")
    p.add_argument("-l", "--label", default="0", help="Label (digits only, default: 0)")
    p.add_argument("-d", "--domain", default="localhost", help="Domain name for nginx hostname")
    return p.parse_args()


def parse_volumes(raw: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            print(f"ERROR: volume mapping must be KEY=VALUE, got: {item}", file=sys.stderr)
            sys.exit(1)
        k, v = item.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def check_volumes(
    compose_template: str, provided: dict[str, str]
) -> dict[str, str]:
    """Cross-check template volumes against provided mapping. Prompt for confirmation on mismatch."""
    expected = template_engine.extract_template_volumes(compose_template)
    missing = [k for k in expected if k not in provided]
    extra = [k for k in provided if k not in expected]

    if missing or extra:
        if missing:
            print(f"WARNING: Volume(s) declared in template but not provided: {missing}")
        if extra:
            print(f"WARNING: Volume(s) provided but not found in template: {extra}")
        answer = input("Continue anyway? [y/N]: ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)
    return provided


def main() -> None:
    args = parse_args()

    # --- Validate inputs ---
    try:
        validation.validate_name(args.user_name, "user_name")
        validation.validate_name(args.service_name, "service_name")
        validation.validate_label(args.label)
    except validation.ValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    compose_template = str(Path(args.compose_template).resolve())
    if not Path(compose_template).exists():
        print(f"ERROR: compose template not found: {compose_template}", file=sys.stderr)
        sys.exit(1)

    env_file: str | None = None
    if args.env_file:
        env_file = str(Path(args.env_file).resolve())
        if not Path(env_file).exists():
            print(f"ERROR: env file not found: {env_file}", file=sys.stderr)
            sys.exit(1)

    nginx_template: str | None = None
    if args.nginx_template:
        nginx_template = str(Path(args.nginx_template).resolve())
        if not Path(nginx_template).exists():
            print(f"ERROR: nginx template not found: {nginx_template}", file=sys.stderr)
            sys.exit(1)

    # --- Check for duplicate registration ---
    existing = registry.get_user_service(args.user_name, args.service_name, args.label)
    if existing:
        print(
            f"ERROR: User '{args.user_name}' with service '{args.service_name}' "
            f"and label '{args.label}' is already registered.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Volume validation ---
    volumes = parse_volumes(args.volume)
    volumes = check_volumes(compose_template, volumes)

    # --- Password ---
    try:
        passwd = auth.prompt_password(args.user_name)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    passwd_hash = auth.hash_password(args.user_name, passwd) if passwd else ""

    # --- Prepare output paths ---
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    compose_out = str(
        GENERATED_DIR / f"docker-compose.user-{args.user_name}.{args.label}.yml"
    )
    nginx_out: str | None = None
    htpasswd_out: str | None = None
    if nginx_template:
        nginx_out = str(
            GENERATED_DIR / f"{args.service_name}.user-{args.user_name}.{args.label}.nginx.conf"
        )
        htpasswd_out = str(
            GENERATED_DIR / f"{args.service_name}.user-{args.user_name}.{args.label}.htpasswd"
        )

    # --- Registry entry ---
    entry: dict = {
        "user_name": args.user_name,
        "passwd": passwd_hash,
        "service_name": args.service_name,
        "label": args.label,
        "network_name": template_engine.user_network_name(args.service_name, args.user_name, args.label),
        "compose_template_path": compose_template,
        "nginx_conf_template_path": nginx_template,
        "env_file_path": env_file,
        "compose_file_path": compose_out,
        "nginx_conf_path": nginx_out,
        "htpasswd_path": htpasswd_out,
        "volumes": volumes,
    }
    registry.add_user(entry)
    print(f"[1/4] Registered user '{args.user_name}' in user_registry.yml")

    # --- Render compose file ---
    copied_env = template_engine.render_compose(
        compose_template, compose_out,
        args.user_name, args.service_name, args.label, volumes,
        env_file=env_file,
    )
    print(f"[2/4] Generated compose file: {compose_out}")
    if copied_env:
        print(f"[2/4] Copied env file to: {copied_env}")

    # --- Render nginx conf ---
    if nginx_template and nginx_out and htpasswd_out:
        if passwd_hash:
            auth.write_htpasswd_file(htpasswd_out, args.user_name, passwd_hash)
            print(f"[3/4] Generated htpasswd file: {htpasswd_out}")
        else:
            print("[3/4] No password set; skipping htpasswd file.")
            htpasswd_out_render = ""
        htpasswd_out_render = htpasswd_out if passwd_hash else ""
        template_engine.render_nginx_conf(
            nginx_template, nginx_out,
            args.user_name, args.service_name, args.label,
            args.domain, htpasswd_out_render,
        )
        print(f"[3/4] Generated nginx conf: {nginx_out}")
    else:
        print("[3/4] No nginx template provided; skipping.")

    # --- Start containers ---
    print("[4/4] Starting containers...")
    try:
        docker_ops.compose_up(compose_out, env_file=copied_env)
    except RuntimeError as e:
        print(f"ERROR starting containers: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone. User '{args.user_name}' registered and containers started.")


if __name__ == "__main__":
    main()
