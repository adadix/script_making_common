"""Common string validation utilities."""


def is_non_empty_string(value):
    """Return True if value is a non-empty, non-whitespace-only string."""
    return isinstance(value, str) and bool(value.strip())


def is_alpha(value):
    """Return True if value is a string containing only alphabetic characters."""
    return isinstance(value, str) and value.isalpha()


def is_alphanumeric(value):
    """Return True if value is a string containing only alphanumeric characters."""
    return isinstance(value, str) and value.isalnum()
