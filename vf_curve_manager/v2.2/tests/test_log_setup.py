"""Tests for utils.log_setup.setup_logging()."""
import sys, os, logging, pytest
from contextlib import contextmanager
from unittest.mock import patch

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.log_setup import setup_logging


@contextmanager
def _isolated_root():
    """Temporarily replace root.handlers with a fresh empty list so the
    setup_logging() guard (`if root.handlers: return early`) never trips
    regardless of what pytest's log-capture plugin has already installed.
    Handlers added during the block are closed on exit; the original list
    is restored so pytest's own capture is unaffected."""
    root = logging.getLogger()
    original = root.handlers
    isolated: list = []
    root.handlers = isolated
    try:
        yield root
    finally:
        for h in isolated:
            try:
                h.close()
            except Exception:
                pass
        root.handlers = original


class TestSetupLogging:
    def test_returns_string_path(self, tmp_path):
        with _isolated_root():
            result = setup_logging(log_dir=str(tmp_path))
        assert isinstance(result, str)

    def test_creates_log_file(self, tmp_path):
        with _isolated_root():
            path = setup_logging(log_dir=str(tmp_path))
        assert os.path.exists(path)

    def test_log_file_in_specified_dir(self, tmp_path):
        with _isolated_root():
            path = setup_logging(log_dir=str(tmp_path))
        assert str(tmp_path) in path

    def test_log_file_has_vf_prefix(self, tmp_path):
        with _isolated_root():
            path = setup_logging(log_dir=str(tmp_path))
        assert os.path.basename(path).startswith('vf_curve_manager_')

    def test_log_file_is_writable(self, tmp_path):
        with _isolated_root():
            path = setup_logging(log_dir=str(tmp_path))
            logging.info('test message from test suite')
        assert os.path.getsize(path) > 0

    def test_adds_handlers_to_root_logger(self, tmp_path):
        with _isolated_root() as root:
            before = len(root.handlers)  # 0 — list is empty
            setup_logging(log_dir=str(tmp_path))
            assert len(root.handlers) > before

    def test_calling_twice_no_duplicate_handlers(self, tmp_path):
        with _isolated_root() as root:
            setup_logging(log_dir=str(tmp_path))
            count_after_first = len(root.handlers)
            setup_logging(log_dir=str(tmp_path))  # guard fires → no new handlers
            assert len(root.handlers) == count_after_first

    def test_creates_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / 'nested' / 'logs'
        with _isolated_root():
            result = setup_logging(log_dir=str(new_dir))
        assert new_dir.exists()
        assert os.path.exists(result)
