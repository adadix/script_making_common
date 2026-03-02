"""Email address validation utilities."""

import re

_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9_%+\-]+(\.[a-zA-Z0-9_%+\-]+)*@[a-zA-Z0-9\-]+(\.[a-zA-Z0-9\-]+)*\.[a-zA-Z]{2,}$"
)


def is_valid_email(value):
    """Return True if value is a valid email address format."""
    if not isinstance(value, str):
        return False
    return bool(_EMAIL_PATTERN.match(value.strip()))
