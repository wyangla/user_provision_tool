"""cli/rebuild.py — rebuild and restart a user's service containers.

Usage:
    python cli/rebuild.py -u USER_NAME -sn SERVICE_NAME -l LABEL [--no-cache]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import docker_ops, registry, validation


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild and restart a user's service containers.")
    p.add_argument("-u", "--user-name", required=True, help="User name")
    p.add_argument("-sn", "--service-name", required=True, help="Service name")
    p.add_argument("-l", "--label", required=True, help="Label")
    p.add_argument("--no-cache", action="store_true", help="Build without Docker layer cache")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    try:
        validation.validate_name(args.user_name, "user_name")
        validation.validate_name(args.service_name, "service_name")
        validation.validate_label(args.label)
    except validation.ValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    entry = registry.get_user_service(args.user_name, args.service_name, args.label)
    if not entry:
        print(
            f"ERROR: No registration found for user '{args.user_name}', "
            f"service '{args.service_name}', label '{args.label}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    compose_file = entry.get("compose_file_path", "")
    if not compose_file or not Path(compose_file).exists():
        print(f"ERROR: Compose file not found: '{compose_file}'.", file=sys.stderr)
        sys.exit(1)

    cache_flag = " --no-cache" if args.no_cache else ""
    print(f"[1/2] Building containers{cache_flag}: {compose_file}")
    env_file = entry.get("env_file_path") or None
    try:
        docker_ops.compose_build(compose_file, no_cache=args.no_cache, env_file=env_file)
    except RuntimeError as e:
        print(f"ERROR during build: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[2/2] Starting containers: {compose_file}")
    try:
        docker_ops.compose_up(compose_file, env_file=env_file)
    except RuntimeError as e:
        print(f"ERROR during up: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone. Containers rebuilt and started for user '{args.user_name}'.")


if __name__ == "__main__":
    main()
