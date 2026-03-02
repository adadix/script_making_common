"""Numeric value validation utilities."""


def is_positive_number(value):
    """Return True if value is a positive number (int or float, greater than zero)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def is_integer(value):
    """Return True if value is an integer (not bool)."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_in_range(value, min_val, max_val):
    """Return True if value is a number within [min_val, max_val] inclusive."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and min_val <= value <= max_val
    )
