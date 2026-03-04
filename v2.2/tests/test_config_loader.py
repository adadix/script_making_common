"""Tests for core/config_loader.py."""
import pytest


class TestConfigLoaderRead:
    def test_get_all_domains_returns_dict(self, config_loader):
        domains = config_loader.get_all_domains()
        assert isinstance(domains, dict)

    def test_at_least_one_domain(self, config_loader):
        assert len(config_loader.get_domain_list()) > 0

    def test_get_known_domain_returns_dict(self, config_loader):
        first = config_loader.get_domain_list()[0]
        d = config_loader.get_domain(first)
        assert isinstance(d, dict)

    def test_get_unknown_domain_returns_none(self, config_loader):
        assert config_loader.get_domain('__does_not_exist__') is None

    def test_domain_has_required_fields(self, config_loader):
        required = ['label', 'freq_multiplier', 'wp_count', 'fuse_path', 'vf_voltage', 'vf_ratio']
        for name in config_loader.get_domain_list():
            d = config_loader.get_domain(name)
            for field in required:
                assert field in d, f"Domain '{name}' missing field '{field}'"

    def test_wp_count_matches_voltage_array(self, config_loader):
        for name in config_loader.get_domain_list():
            d = config_loader.get_domain(name)
            assert len(d['vf_voltage']) == d['wp_count'], \
                f"Domain '{name}': vf_voltage length mismatch"

    def test_wp_count_matches_ratio_array(self, config_loader):
        for name in config_loader.get_domain_list():
            d = config_loader.get_domain(name)
            assert len(d['vf_ratio']) == d['wp_count'], \
                f"Domain '{name}': vf_ratio length mismatch"


class TestConfigLoaderValidate:
    def test_validate_passes_on_real_config(self, config_loader):
        ok, msg = config_loader.validate_config()
        assert ok, msg

    def test_validate_catches_missing_domains_key(self, config_loader, tmp_path):
        """A config with no 'domains' key must fail validation."""
        import json
        from core.config_loader import ConfigLoader
        bad = tmp_path / 'bad.json'
        bad.write_text(json.dumps({'info': 'missing domains key'}))
        cl = ConfigLoader(str(bad))
        ok, msg = cl.validate_config()
        assert not ok
        assert 'domains' in msg.lower()

    def test_validate_catches_voltage_out_of_range(self, config_loader, tmp_path):
        """A voltage of 99 V should fail the range check."""
        import json
        from core.config_loader import ConfigLoader
        first = config_loader.get_domain_list()[0]
        d = config_loader.get_domain(first)
        bad_domain = dict(d)
        bad_domain['vf_voltage'] = [99.0] * d['wp_count']   # absurd voltage
        cfg = {'domains': {first: bad_domain}}
        p = tmp_path / 'bad_voltage.json'
        p.write_text(json.dumps(cfg))
        cl = ConfigLoader(str(p))
        ok, msg = cl.validate_config()
        assert not ok
        assert 'range' in msg.lower() or 'vf_voltage' in msg.lower()
