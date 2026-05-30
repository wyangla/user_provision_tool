"""cli/remove.py — stop containers and remove a user's service registration.

Usage:
    python cli/remove.py -u USER_NAME -sn SERVICE_NAME -l LABEL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import docker_ops, registry, validation


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Remove a user's service and deregister them.")
    p.add_argument("-u", "--user-name", required=True, help="User name")
    p.add_argument("-sn", "--service-name", required=True, help="Service name")
    p.add_argument("-l", "--label", required=True, help="Label")
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
        print(f"WARNING: Compose file not found: '{compose_file}'. Skipping docker down.")
    else:
        print(f"[1/2] Stopping containers using {compose_file} ...")
        try:
            docker_ops.compose_down(compose_file, env_file=entry.get("env_file_path") or None)
        except RuntimeError as e:
            print(f"ERROR stopping containers: {e}", file=sys.stderr)
            sys.exit(1)

    removed = registry.remove_user_service(args.user_name, args.service_name, args.label)
    if removed:
        print(f"[2/2] Removed user '{args.user_name}' from registry.")
    else:
        print(f"WARNING: Registry entry was not found during removal.")

    print(f"\nDone. User '{args.user_name}' service '{args.service_name}' (label {args.label}) removed.")


if __name__ == "__main__":
    main()
