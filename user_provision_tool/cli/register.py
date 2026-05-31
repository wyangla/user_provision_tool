"""cli/register.py — register a user and start their service containers.

Usage:
    python cli/register.py -u USER_NAME -sn SERVICE_NAME
        -pr PROJECT_ROOT
        { -tc COMPOSE_TEMPLATE | -fc COMPOSE_FILE }
        [-v KEY=VALUE ...] [-e ENV_FILE]
        [-tn NGINX_TEMPLATE | -fn NGINX_FILE]
        [-l LABEL] [-d DOMAIN]

-pr  Project root directory (e.g. source_project/service_1).
       • All generated files (rendered compose, nginx conf, htpasswd) are
         written here so they live alongside the Dockerfile / source tree
       • 'build: .' contexts in compose files resolve correctly
-tc  Filename of an existing Jinja2 compose template (.yml.j2) inside project root.
-fc  Filename of a plain docker-compose.yml inside project root.
     Converted to a .j2 template before registration.  Mutually exclusive
     with -tc.
-tn  Filename of an existing Jinja2 nginx conf template (.nginx.conf.j2) inside project root.
-fn  Filename of a plain nginx conf file inside project root.
     Converted to a .j2 template before registration.  Mutually exclusive
     with -tn.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure lib/ is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

import os

from lib import auth, provisioner, template_engine, validation
from lib.compose_converter import compose_file_to_template
from lib.nginx_converter import nginx_file_to_template

GENERATED_DIR = Path(
    os.environ.get("GENERATED_DIR", str(Path(__file__).parent.parent / "generated"))
)

USER_DATA_DIR = Path(
    os.environ.get("USER_DATA_DIR", str(Path(__file__).parent.parent / "user_data"))
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register a user and start their service containers.")
    p.add_argument("-u", "--user-name", required=True, help="User name")
    p.add_argument("-sn", "--service-name", required=True, help="Service name")
    p.add_argument("-pr", "--project-root", required=True,
        help=(
            "Project root directory (e.g. source_project/service_1). "
            "Generated files are written here; all source filenames (-tc/-fc/-tn/-fn) "
            "are resolved relative to this directory."
        ),
    )

    src_group = p.add_mutually_exclusive_group(required=True)
    src_group.add_argument(
        "-tc", "--compose-template",
        help="Filename of an existing Jinja2 compose template (.yml.j2) inside project root.",
    )
    src_group.add_argument(
        "-fc", "--compose-file",
        help="Filename of a plain docker-compose.yml inside project root. Converted to .yml.j2 before registration.",
    )

    p.add_argument("-v", "--volume", action="append", default=[],
        metavar="KEY=VALUE",
        help=(
            "Volume mapping (can be repeated): template_volume=host_path. "
            "When omitted, paths are auto-generated under USER_DATA_DIR."
        ),
    )
    p.add_argument("-e", "--env-file", default=None, help="Path to .env file for docker compose variable substitution")

    nginx_group = p.add_mutually_exclusive_group()
    nginx_group.add_argument(
        "-tn", "--nginx-template",
        help="Filename of an existing Jinja2 nginx conf template (.nginx.conf.j2) inside project root.",
    )
    nginx_group.add_argument(
        "-fn", "--nginx-file",
        help="Filename of a plain nginx conf file inside project root. Converted to .j2 before registration.",
    )

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

    # --- Resolve project root ---
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        print(f"ERROR: project root not found: {project_root}", file=sys.stderr)
        sys.exit(1)

    # --- Resolve compose template (-tc used directly; -fc triggers conversion) ---
    if args.compose_template:
        compose_src = project_root / args.compose_template
        if not compose_src.exists():
            print(f"ERROR: compose template not found: {compose_src}", file=sys.stderr)
            sys.exit(1)
        compose_template = str(compose_src)
    else:
        compose_src = project_root / args.compose_file
        if not compose_src.exists():
            print(f"ERROR: compose file not found: {compose_src}", file=sys.stderr)
            sys.exit(1)
        src = compose_src
        template_out = str(src.parent / f"{src.stem}.yml.j2")
        try:
            src_to_key = compose_file_to_template(
                str(compose_src), template_out, service_name_hint=args.service_name
            )
        except ValueError as exc:
            print(f"ERROR: could not convert compose file: {exc}", file=sys.stderr)
            sys.exit(1)
        compose_template = template_out
        print(f"[0/4] Converted compose file to template: {compose_template}")
        if src_to_key:
            col = max(len(k) for k in src_to_key.values())
            for s, k in src_to_key.items():
                print(f"       volume key '{k}'  ←  {s}")
            print("       Pass these with -v KEY=HOST_PATH (or omit to be warned).")

    env_file: str | None = None
    if args.env_file:
        env_file = str(Path(args.env_file).resolve())
        if not Path(env_file).exists():
            print(f"ERROR: env file not found: {env_file}", file=sys.stderr)
            sys.exit(1)

    # --- Resolve nginx template (-tn used directly; -fn triggers conversion) ---
    nginx_template: str | None = None
    if args.nginx_template:
        nginx_src = project_root / args.nginx_template
        if not nginx_src.exists():
            print(f"ERROR: nginx template not found: {nginx_src}", file=sys.stderr)
            sys.exit(1)
        nginx_template = str(nginx_src)
    elif args.nginx_file:
        nginx_src = project_root / args.nginx_file
        if not nginx_src.exists():
            print(f"ERROR: nginx file not found: {nginx_src}", file=sys.stderr)
            sys.exit(1)
        template_out = str(nginx_src.parent / f"{nginx_src.name}.j2")
        try:
            nginx_file_to_template(str(nginx_src), template_out, args.service_name)
        except Exception as exc:
            print(f"ERROR: could not convert nginx file: {exc}", file=sys.stderr)
            sys.exit(1)
        nginx_template = template_out
        print(f"[0/4] Converted nginx file to template: {nginx_template}")

    # --- Volume validation / auto-generation ---
    volumes = parse_volumes(args.volume)
    if volumes:
        # Manual mode: validate provided mapping against template
        volumes = check_volumes(compose_template, volumes)
    # else: provisioner will auto-generate under USER_DATA_DIR

    # --- Password prompt ---
    try:
        passwd = auth.prompt_password(args.user_name)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Core registration workflow ---
    try:
        result = provisioner.register_user(
            user_name=args.user_name,
            service_name=args.service_name,
            label=args.label,
            compose_template=compose_template,
            output_dir=project_root,
            nginx_output_dir=GENERATED_DIR,
            volumes=volumes or None,
            user_data_dir=USER_DATA_DIR,
            passwd=passwd,
            nginx_template=nginx_template,
            domain=args.domain,
            env_file=env_file,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR starting containers: {e}", file=sys.stderr)
        sys.exit(1)

    entry = result["entry"]
    print(f"[1/4] Registered user '{args.user_name}' in user_registry.yml")
    print(f"[2/4] Generated compose file: {entry['compose_file_path']}")
    if result["copied_env"]:
        print(f"[2/4] Copied env file to: {result['copied_env']}")
    if nginx_template:
        if entry["passwd"]:
            print(f"[3/4] Generated htpasswd file: {entry['htpasswd_path']}")
        else:
            print("[3/4] No password set; skipping htpasswd file.")
        print(f"[3/4] Generated nginx conf: {entry['nginx_conf_path']}")
    else:
        print("[3/4] No nginx template provided; skipping.")
    print(f"\nDone. User '{args.user_name}' registered and containers started.")


if __name__ == "__main__":
    main()
