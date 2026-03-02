# script_making_common

A collection of reusable validation-specific tools and utilities.

## Overview

This repository contains common validation scripts and helper modules that can be used across projects. It provides ready-to-use validators for strings, emails, URLs, numeric values, and more.

## Structure

```
script_making_common/
├── validators/
│   ├── __init__.py       # Package exports
│   ├── string_validators.py   # String/text validation
│   ├── email_validator.py     # Email address validation
│   ├── url_validator.py       # URL validation
│   └── numeric_validators.py  # Numeric value validation
├── tests/
│   └── test_validators.py    # Unit tests
└── requirements.txt
```

## Usage

```python
from validators import is_valid_email, is_valid_url, is_non_empty_string

is_valid_email("user@example.com")   # True
is_valid_url("https://example.com")  # True
is_non_empty_string("hello")         # True
is_non_empty_string("  ")            # False
```

## Running Tests

```bash
python -m pytest tests/
```
