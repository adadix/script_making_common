"""Shared pytest fixtures for VF Curve Manager test suite."""
import sys
import os
import json
import shutil

# Tell hardware_access.py to skip the Intel toolchain imports (and therefore
# skip any OpenIPC connection) for the entire test session.  Must be set
# BEFORE src/ modules are imported.
os.environ.setdefault('VF_MOCK_MODE', '1')

# Run Qt widgets headlessly (no display required) — must be set before
# QApplication is first created, so set it here at collection time.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# Ensure src/ is on sys.path before any test import
_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

_CACHE_FILE = os.path.join(_SRC, 'vf_discovery_cache.json')


@pytest.fixture(scope='session', autouse=True)
def _guard_discovery_cache():
    """Back up vf_discovery_cache.json before the session and restore it
    afterwards so no test (or CLI subprocess) can permanently corrupt the
    fixture data that other tests depend on.
    """
    original: bytes | None = None
    if os.path.exists(_CACHE_FILE):
        with open(_CACHE_FILE, 'rb') as fh:
            original = fh.read()
    yield
    # ── Restore ──────────────────────────────────────────────────────────
    if original is not None:
        with open(_CACHE_FILE, 'wb') as fh:
            fh.write(original)
    elif os.path.exists(_CACHE_FILE):
        os.remove(_CACHE_FILE)


@pytest.fixture(scope='session')
def src_dir():
    return os.path.abspath(_SRC)


@pytest.fixture(scope='session')
def vf_domains_path(src_dir):
    return os.path.join(src_dir, 'vf_domains.json')


@pytest.fixture(scope='session')
def discovery_cache_path(src_dir):
    return os.path.join(src_dir, 'vf_discovery_cache.json')


@pytest.fixture(scope='session')
def config_loader(vf_domains_path):
    from core.config_loader import ConfigLoader
    return ConfigLoader(vf_domains_path)


@pytest.fixture
def mock_hardware():
    """Initialise hardware_access in mock mode; restore MOCK_MODE after the test."""
    import utils.hardware_access as hw
    original = hw.MOCK_MODE
    hw.init_hardware(None, None, mock_mode=True)
    yield hw
    hw.MOCK_MODE = original


@pytest.fixture
def mock_curve_engine(config_loader, mock_hardware):
    from core.curve_engine import CurveEngine
    return CurveEngine(config_loader)
