#!/usr/bin/env python3
"""gen_nginx_template.py — CLI wrapper around lib/nginx_converter.py.

Convert a plain nginx conf file into a Jinja2 template (.nginx.conf.j2)
compatible with user_provision_tool.

Usage:
    python gen_nginx_template.py <input.conf> [-o output.conf.j2] [-s SERVICE_NAME] [-c compose.yml]

See lib/nginx_converter.py for full documentation of the transformations applied.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running from the cli/ directory without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.compose_converter import get_compose_service_names
from lib.nginx_converter import nginx_file_to_template


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Convert a plain nginx conf file into a Jinja2 template (.nginx.conf.j2) "
            "for use with user_provision_tool."
        )
    )
    p.add_argument("input", help="Path to the source nginx conf file")
    p.add_argument(
        "-o", "--output",
        help=(
            "Output path for the .j2 template. "
            "Defaults to <input-stem>.nginx.conf.j2 in the same directory."
        ),
    )
    p.add_argument(
        "-s", "--service-name",
        help=(
            "Service name hint used to rewrite proxy_pass container names "
            "and populate the header comment (default: input file stem)."
        ),
    )
    p.add_argument(
        "-c", "--compose-file",
        help=(
            "Path to the companion docker-compose.yml (or .j2 template). "
            "Service names extracted from this file are used to rewrite "
            "proxy_pass targets that reference compose service names."
        ),
    )
    args = p.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = (
        Path(args.output).resolve()
        if args.output
        else input_path.parent / f"{input_path.name}.j2"
    )

    service_name_hint = args.service_name or re.sub(
        r"[^a-zA-Z0-9_]", "_", input_path.stem
    )

    compose_service_names: list[str] | None = None
    if args.compose_file:
        compose_path = Path(args.compose_file)
        if not compose_path.exists():
            print(f"ERROR: compose file not found: {compose_path}", file=sys.stderr)
            sys.exit(1)
        compose_service_names = get_compose_service_names(str(compose_path))

    try:
        nginx_file_to_template(
            str(input_path), str(output_path), service_name_hint,
            compose_service_names=compose_service_names,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Template written to: {output_path}")
    print()
    print("Review the template and adjust any proxy_pass targets that were not")
    print("automatically rewritten, then register with:")
    print(f"  register.py ... -tn {output_path}")


if __name__ == "__main__":
    main()
