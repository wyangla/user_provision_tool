"""cli/remove.py — stop containers and remove a user's service registration.

Usage:
    python cli/remove.py -u USER_NAME -sn SERVICE_NAME -l LABEL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import provisioner, validation


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

    try:
        provisioner.remove_user(
            user_name=args.user_name,
            service_name=args.service_name,
            label=args.label,
        )
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR stopping containers: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[1/2] Stopped containers.")
    print(f"[2/2] Removed user '{args.user_name}' from registry.")
    print(f"\nDone. User '{args.user_name}' service '{args.service_name}' (label {args.label}) removed.")


if __name__ == "__main__":
    main()
