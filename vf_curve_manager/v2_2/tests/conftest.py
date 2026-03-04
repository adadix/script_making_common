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

_CACHE_FILE   = os.path.join(_SRC, 'vf_discovery_cache.json')
_FIXTURE_CACHE = os.path.join(os.path.dirname(__file__), 'fixtures',
                               'vf_discovery_cache.json')


@pytest.fixture(scope='session', autouse=True)
def _guard_discovery_cache():
    """Back up vf_discovery_cache.json before the session and restore it
    afterwards so no test (or CLI subprocess) can permanently corrupt the
    fixture data that other tests depend on.

    If no cache exists at session start, the known-good fixture from
    tests/fixtures/ is installed so tests that require a pre-populated
    cache (e.g. TestEditRegisterView) always have valid data.
    After the session the original state is restored — meaning the fixture
    is re-written (not deleted) so subsequent runs work without a real
    hardware discovery run.
    """
    original: bytes | None = None
    if os.path.exists(_CACHE_FILE):
        with open(_CACHE_FILE, 'rb') as fh:
            original = fh.read()
    else:
        # Install the shipped fixture so cache-dependent tests pass.
        if os.path.exists(_FIXTURE_CACHE):
            shutil.copy2(_FIXTURE_CACHE, _CACHE_FILE)

    yield

    # ── Restore ──────────────────────────────────────────────────────────
    if original is not None:
        # Restore whatever was there before the session (real or fixture).
        with open(_CACHE_FILE, 'wb') as fh:
            fh.write(original)
    elif os.path.exists(_FIXTURE_CACHE):
        # No pre-existing cache — restore the shipped fixture so the next
        # test session also has valid data without re-running discovery.
        shutil.copy2(_FIXTURE_CACHE, _CACHE_FILE)
    elif os.path.exists(_CACHE_FILE):
        # No fixture available either — clean up anything tests may have created.
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
