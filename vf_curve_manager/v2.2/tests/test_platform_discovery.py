"""Tests for core.platform_discovery — the discovery shim."""
import sys, os, pytest
from unittest.mock import MagicMock, patch

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from core.platform_discovery import discover_and_save


class TestDiscoverAndSave:
    def test_always_returns_empty_dict(self):
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline'):
            assert discover_and_save() == {}

    def test_calls_pipeline_force_true(self):
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline') as mock_run:
            discover_and_save()
        mock_run.assert_called_once_with(force=True)

    def test_returns_empty_dict_on_runtime_error(self):
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline',
                   side_effect=RuntimeError('hw unavailable')):
            assert discover_and_save() == {}

    def test_returns_empty_dict_when_module_missing(self):
        with patch.dict('sys.modules', {'discovery.auto_discover_vf_registers': None}):
            assert discover_and_save() == {}

    def test_accepts_output_path_for_api_compat(self):
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline'):
            assert discover_and_save(output_path='ignored.json') == {}

    def test_module_importable(self):
        from core import platform_discovery
        assert callable(platform_discovery.discover_and_save)
