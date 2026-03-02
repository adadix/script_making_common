"""URL validation utilities."""

import re

_URL_PATTERN = re.compile(
    r"^(https?|ftp)://"
    r"([a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}"
    r"(:\d+)?"
    r"(/[^\s]*)?"
    r"$"
)


def is_valid_url(value):
    """Return True if value is a valid URL with http, https, or ftp scheme."""
    if not isinstance(value, str):
        return False
    return bool(_URL_PATTERN.match(value.strip()))
