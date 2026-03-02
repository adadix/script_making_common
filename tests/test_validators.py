"""Tests for the validators package."""

import pytest
from validators import (
    is_non_empty_string,
    is_alpha,
    is_alphanumeric,
    is_valid_email,
    is_valid_url,
    is_positive_number,
    is_integer,
    is_in_range,
)


# --- String validators ---

class TestIsNonEmptyString:
    def test_valid_string(self):
        assert is_non_empty_string("hello") is True

    def test_whitespace_only(self):
        assert is_non_empty_string("   ") is False

    def test_empty_string(self):
        assert is_non_empty_string("") is False

    def test_non_string(self):
        assert is_non_empty_string(123) is False

    def test_none(self):
        assert is_non_empty_string(None) is False


class TestIsAlpha:
    def test_alpha_string(self):
        assert is_alpha("Hello") is True

    def test_string_with_digits(self):
        assert is_alpha("Hello1") is False

    def test_empty_string(self):
        assert is_alpha("") is False

    def test_non_string(self):
        assert is_alpha(123) is False


class TestIsAlphanumeric:
    def test_alphanumeric(self):
        assert is_alphanumeric("abc123") is True

    def test_with_space(self):
        assert is_alphanumeric("abc 123") is False

    def test_empty_string(self):
        assert is_alphanumeric("") is False

    def test_non_string(self):
        assert is_alphanumeric(None) is False


# --- Email validator ---

class TestIsValidEmail:
    def test_valid_email(self):
        assert is_valid_email("user@example.com") is True

    def test_valid_email_with_plus(self):
        assert is_valid_email("user+tag@example.co.uk") is True

    def test_missing_at_sign(self):
        assert is_valid_email("userexample.com") is False

    def test_missing_domain(self):
        assert is_valid_email("user@") is False

    def test_empty_string(self):
        assert is_valid_email("") is False

    def test_non_string(self):
        assert is_valid_email(42) is False


# --- URL validator ---

class TestIsValidUrl:
    def test_https_url(self):
        assert is_valid_url("https://example.com") is True

    def test_http_url(self):
        assert is_valid_url("http://example.com/path") is True

    def test_ftp_url(self):
        assert is_valid_url("ftp://files.example.com") is True

    def test_url_with_port(self):
        assert is_valid_url("https://example.com:8080/path") is True

    def test_missing_scheme(self):
        assert is_valid_url("example.com") is False

    def test_empty_string(self):
        assert is_valid_url("") is False

    def test_non_string(self):
        assert is_valid_url(None) is False


# --- Numeric validators ---

class TestIsPositiveNumber:
    def test_positive_int(self):
        assert is_positive_number(5) is True

    def test_positive_float(self):
        assert is_positive_number(0.1) is True

    def test_zero(self):
        assert is_positive_number(0) is False

    def test_negative(self):
        assert is_positive_number(-1) is False

    def test_bool_true(self):
        assert is_positive_number(True) is False

    def test_non_number(self):
        assert is_positive_number("5") is False


class TestIsInteger:
    def test_integer(self):
        assert is_integer(10) is True

    def test_negative_integer(self):
        assert is_integer(-3) is True

    def test_float(self):
        assert is_integer(1.0) is False

    def test_bool(self):
        assert is_integer(True) is False

    def test_string(self):
        assert is_integer("5") is False


class TestIsInRange:
    def test_within_range(self):
        assert is_in_range(5, 1, 10) is True

    def test_at_min_boundary(self):
        assert is_in_range(1, 1, 10) is True

    def test_at_max_boundary(self):
        assert is_in_range(10, 1, 10) is True

    def test_below_range(self):
        assert is_in_range(0, 1, 10) is False

    def test_above_range(self):
        assert is_in_range(11, 1, 10) is False

    def test_bool_excluded(self):
        assert is_in_range(True, 0, 2) is False

    def test_non_number(self):
        assert is_in_range("5", 1, 10) is False
