"""
Structured logging setup for VF Curve Manager.
===============================================

Replaces scattered print() calls throughout the codebase with Python's
standard logging module, writing to both console and a timestamped log file.

Usage (call once at startup in each launcher):
    from utils.log_setup import setup_logging
    log_file = setup_logging()

Log levels:
    DEBUG   → file only (detailed ITP/register-level diagnostics)
    INFO    → file + console (normal operation)
    WARNING → file + console (non-fatal issues)
    ERROR   → file + console (operation failed)
    CRITICAL→ file + console (hardware instability, cold reset)

Log files are written to  Logs/  (project root) with a timestamp in the filename so
multiple runs do not overwrite each other.
"""

import logging
import os
import sys
from pathlib import Path
from datetime import datetime


def setup_logging(
    log_dir: str = None,
    file_level: int = logging.DEBUG,
    console_level: int = logging.INFO,
) -> str:
    """
    Configure root logger with file + console handlers.

    Args:
        log_dir:       Directory to write log files.  Defaults to
                       Logs/ at the project root.
        file_level:    Minimum level written to the log file (default DEBUG).
        console_level: Minimum level printed to the console (default INFO).

    Returns:
        str: Absolute path of the log file created.
    """
    if log_dir is None:
        # src/utils/log_setup.py -> src/utils/ -> src/ -> project root -> Logs/
        log_dir = Path(__file__).parent.parent.parent / 'Logs'

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'vf_curve_manager_{timestamp}.log'

    root = logging.getLogger()
    # Avoid adding duplicate handlers if setup_logging() is called more than once
    if root.handlers:
        return str(log_file)

    root.setLevel(logging.DEBUG)

    # ── File handler: full detail ────────────────────────────────────────────
    fh = logging.FileHandler(str(log_file), encoding='utf-8')
    fh.setLevel(file_level)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)-8s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))

    # ── Console handler: INFO and above ────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(console_level)
    ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))

    root.addHandler(fh)
    root.addHandler(ch)

    logging.info(f"Logging initialised  ->  {log_file}")
    return str(log_file)
