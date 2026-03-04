"""Tests for discovery.startup_discovery — platform check + pipeline bridge."""
import sys, os, json, pytest
from unittest.mock import patch, MagicMock

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from discovery import startup_discovery as sd


class TestGetCachedPlatform:
    def test_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_cached_platform() == ''

    def test_returns_platform_from_valid_cache(self, tmp_path, monkeypatch):
        cache = tmp_path / 'vf_discovery_cache.json'
        cache.write_text(json.dumps({'platform': 'TestPlatform'}), encoding='utf-8')
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_cached_platform() == 'testplatform'

    def test_returns_empty_on_bad_json(self, tmp_path, monkeypatch):
        cache = tmp_path / 'vf_discovery_cache.json'
        cache.write_text('NOT JSON', encoding='utf-8')
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_cached_platform() == ''

    def test_returns_empty_when_no_platform_key(self, tmp_path, monkeypatch):
        cache = tmp_path / 'vf_discovery_cache.json'
        cache.write_text(json.dumps({'other': 'data'}), encoding='utf-8')
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_cached_platform() == ''


class TestGetDomainsPlatform:
    def test_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_domains_platform() == ''

    def test_returns_platform_from_valid_domains(self, tmp_path, monkeypatch):
        domains = tmp_path / 'vf_domains.json'
        domains.write_text(json.dumps({'_platform': 'TestPlatform', 'domain': {}}), encoding='utf-8')
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_domains_platform() == 'testplatform'

    def test_returns_empty_on_bad_json(self, tmp_path, monkeypatch):
        domains = tmp_path / 'vf_domains.json'
        domains.write_text('BAD', encoding='utf-8')
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        assert sd._get_domains_platform() == ''


class TestMaybeRunDiscovery:
    def test_force_true_runs_pipeline(self):
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline',
                   return_value=True) as mock_run:
            result = sd.maybe_run_discovery(force=True)
        assert result is True
        mock_run.assert_called_once_with(force=True)

    def test_force_true_bypasses_platform_check(self):
        """When force=True, detect_platform_name must NOT be called."""
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline',
                   return_value=True):
            with patch('discovery.auto_discover_vf_registers.detect_platform_name') as mock_detect:
                sd.maybe_run_discovery(force=True)
            mock_detect.assert_not_called()

    def test_import_error_returns_false(self):
        with patch.dict('sys.modules',
                        {'discovery.auto_discover_vf_registers': None}):
            result = sd.maybe_run_discovery(force=True)
        assert result is False

    def test_pipeline_exception_returns_false(self):
        with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline',
                   side_effect=RuntimeError('pipeline crash')):
            result = sd.maybe_run_discovery(force=True)
        assert result is False

    def test_force_false_matching_platform_skips(self, tmp_path, monkeypatch):
        """Same platform detected and stored → skip discovery."""
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        domains = tmp_path / 'vf_domains.json'
        domains.write_text(
            json.dumps({'_platform': 'SamePlatform', 'domain': {}}),
            encoding='utf-8'
        )
        with patch('discovery.auto_discover_vf_registers.detect_platform_name',
                   return_value='SamePlatform'):
            with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline',
                       return_value=True) as mock_run:
                sd.maybe_run_discovery(force=False)
        mock_run.assert_called_once_with(force=False)

    def test_force_false_platform_mismatch_triggers_rediscovery(self, tmp_path, monkeypatch):
        """Different platform detected → force=True passed to pipeline."""
        monkeypatch.setattr(sd, '_SRC_DIR', str(tmp_path))
        domains = tmp_path / 'vf_domains.json'
        domains.write_text(
            json.dumps({'_platform': 'OldPlatform', 'domain': {}}),
            encoding='utf-8'
        )
        with patch('discovery.auto_discover_vf_registers.detect_platform_name',
                   return_value='NewPlatform'):
            with patch('discovery.auto_discover_vf_registers.run_discovery_pipeline',
                       return_value=True) as mock_run:
                result = sd.maybe_run_discovery(force=False)
        mock_run.assert_called_once_with(force=True)
        assert result is True
