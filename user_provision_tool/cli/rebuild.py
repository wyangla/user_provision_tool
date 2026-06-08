"""cli/rebuild.py — rebuild and restart a user's service containers.

Usage:
    python cli/rebuild.py -u USER_NAME -sn SERVICE_NAME -l LABEL [--no-cache] [--build-arg KEY=VALUE ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import provisioner, validation


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild and restart a user's service containers.")
    p.add_argument("-u", "--user-name", required=True, help="User name")
    p.add_argument("-sn", "--service-name", required=True, help="Service name")
    p.add_argument("-l", "--label", required=True, help="Label")
    p.add_argument("--no-cache", action="store_true", help="Build without Docker layer cache")
    p.add_argument("--build-arg", action="append", default=[], dest="build_args_raw",
        metavar="KEY=VALUE", help="Build argument (can be repeated, e.g. --build-arg HTTP_PROXY=http://proxy:8080)")
    return p.parse_args()


def _parse_build_args(raw: list[str]) -> dict[str, str] | None:
    if not raw:
        return None
    result: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            print(f"ERROR: --build-arg must be KEY=VALUE, got: {item}", file=sys.stderr)
            sys.exit(1)
        k, v = item.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def main() -> None:
    args = parse_args()

    try:
        validation.validate_name(args.user_name, "user_name")
        validation.validate_name(args.service_name, "service_name")
        validation.validate_label(args.label)
    except validation.ValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    build_args = _parse_build_args(args.build_args_raw)
    cache_flag = " --no-cache" if args.no_cache else ""
    if build_args:
        cache_flag += f" --build-arg {' --build-arg '.join(f'{k}={v}' for k, v in build_args.items())}"
    print(f"[1/2] Building containers{cache_flag}...")
    try:
        provisioner.rebuild_user(
            user_name=args.user_name,
            service_name=args.service_name,
            label=args.label,
            no_cache=args.no_cache,
            build_args=build_args,
        )
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[2/2] Started containers.")
    print(f"\nDone. Containers rebuilt and started for user '{args.user_name}'.")


if __name__ == "__main__":
    main()
