"""Shared YAML utilities for the provision tool."""

import yaml


class IndentedDumper(yaml.Dumper):
    """YAML dumper that always indents sequence items under their parent key.

    By default PyYAML indents sequences at the same level as the mapping key
    (``key:\\n- item``).  This dumper forces nested indentation
    (``key:\\n  - item``), which is the standard YAML style.
    """
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> int:
        return super().increase_indent(flow, False)
