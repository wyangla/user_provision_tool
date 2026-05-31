#!/usr/bin/env python3
"""gen_compose_template.py — CLI wrapper around lib/compose_converter.py.

Convert a plain docker-compose.yml into a Jinja2 compose template (.yml.j2)
compatible with user_provision_tool.

Usage:
    python gen_compose_template.py <input.yml> [-o output.yml.j2] [-s SERVICE_NAME]

See lib/compose_converter.py for full documentation of the transformations applied.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running from the cli/ directory without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.compose_converter import compose_file_to_template


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Convert a docker-compose.yml into a Jinja2 template (.yml.j2) "
            "for use with user_provision_tool."
        )
    )
    p.add_argument("input", help="Path to the source docker-compose.yml")
    p.add_argument(
        "-o", "--output",
        help=(
            "Output path for the .yml.j2 template. "
            "Defaults to <input-stem>.yml.j2 in the same directory."
        ),
    )
    p.add_argument(
        "-s", "--service-name",
        help="Service name hint for the header comment (default: input file stem).",
    )
    args = p.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = (
        Path(args.output).resolve()
        if args.output
        else input_path.parent / f"{input_path.stem}.yml.j2"
    )

    service_name_hint = args.service_name or re.sub(
        r"[^a-zA-Z0-9_]", "_", input_path.stem
    )

    try:
        src_to_key = compose_file_to_template(
            str(input_path), str(output_path), service_name_hint
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Template written to: {output_path}")

    if src_to_key:
        print()
        print("Volume keys generated (supply these with -v KEY=HOST_PATH at registration):")
        col = max(len(k) for k in src_to_key.values())
        for src, key in src_to_key.items():
            print(f"  {key:<{col}}  ←  {src}")

    print()
    print("Review the template and replace any remaining literal values with")
    print("{{ user_name }}, {{ service_name }}, or ${ENV_VAR} as needed.")


if __name__ == "__main__":
    main()

