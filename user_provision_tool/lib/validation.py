"""Input validation for user provision tool."""

import re

_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')
_LABEL_RE = re.compile(r'^\d+$')


class ValidationError(ValueError):
    pass


def validate_name(value: str, field: str = "name") -> str:
    """Validate that a name contains only [a-zA-Z0-9_-]."""
    if not value:
        raise ValidationError(f"{field} must not be empty.")
    if not _NAME_RE.match(value):
        raise ValidationError(
            f"{field} '{value}' is invalid: only English letters, digits, hyphens, and underscores are allowed."
        )
    return value


def validate_label(value: str) -> str:
    """Validate that a label contains only digits."""
    if not _LABEL_RE.match(value):
        raise ValidationError(
            f"label '{value}' is invalid: only digits are allowed."
        )
    return value
