"""Tests for utils/mock_backend.py."""
import os
import sys
import json
import pytest

# src/ already on sys.path via conftest.py

from utils.mock_backend import MockFuseObject, load_mock_registers

# Import here at module level (not inside test methods) to avoid conflicting
# with pytest's capsys capture when the module is first loaded mid-session.
from discovery.auto_discover_vf_registers import _infer_conversion_from_description


class TestMockFuseObject:
    def test_read_known_register(self):
        obj = MockFuseObject({'voltage_0': 850})
        assert obj.voltage_0 == 850

    def test_read_unknown_returns_child_mock(self):
        obj = MockFuseObject({})
        child = obj.some_unknown_path
        assert isinstance(child, MockFuseObject)

    def test_chained_path_resolves(self):
        """cdie.fuses.punit.voltage_0 — multi-level chain should not raise."""
        obj = MockFuseObject({'voltage_0': 900})
        # Each intermediate returns a MockFuseObject that shares the same register dict
        val = obj.cdie.fuses.voltage_0
        assert val == 900

    def test_write_updates_readback(self):
        obj = MockFuseObject({'voltage_0': 800})
        obj.voltage_0 = 900
        assert obj.voltage_0 == 900

    def test_write_new_register(self):
        obj = MockFuseObject({})
        obj.new_reg = 42
        assert obj.new_reg == 42

    def test_load_fuse_ram_is_noop(self, caplog):
        import logging
        obj = MockFuseObject({})
        with caplog.at_level(logging.DEBUG):
            obj.load_fuse_ram()           # should not raise
        assert 'no-op' in caplog.text.lower()

    def test_flush_fuse_ram_is_noop(self, caplog):
        import logging
        obj = MockFuseObject({})
        with caplog.at_level(logging.DEBUG):
            obj.flush_fuse_ram()
        assert 'no-op' in caplog.text.lower()


class TestLoadMockRegisters:
    def test_loads_from_real_cache(self, discovery_cache_path):
        if not os.path.exists(discovery_cache_path):
            pytest.skip("vf_discovery_cache.json not present — run discovery first")
        regs = load_mock_registers(discovery_cache_path)
        assert isinstance(regs, dict)
        assert len(regs) > 0

    def test_missing_path_returns_empty_dict(self, tmp_path):
        regs = load_mock_registers(str(tmp_path / 'does_not_exist.json'))
        assert regs == {}

    def test_malformed_json_returns_empty_dict(self, tmp_path):
        bad = tmp_path / 'bad.json'
        bad.write_text("{ THIS IS NOT VALID JSON }")
        regs = load_mock_registers(str(bad))
        assert regs == {}


class TestInferConversionPatterns:
    """Unit tests for all patterns in _infer_conversion_from_description."""

    @staticmethod
    def _infer(name, desc, value):
        return _infer_conversion_from_description(name, desc, value)

    # --- Pattern 9: U1.8 / U9.1.8 ---
    def test_u1_8_standard(self):
        result = self._infer('fw_fuses_cutoff_v', 'The fuse is in U1.8 volts', 256)
        assert result == '1000.0 mV'

    def test_u9_1_8_extended(self):
        result = self._infer('fw_fuses_bigcore_itd_cutoff_v',
                              'The voltage from which and below, ITD effect is relevant. The fuse is in U9.1.8 volts',
                              285)
        assert '1113.28' in result

    # --- Pattern 10: U0.8 (ITD floor voltage) ---
    def test_u0_8_floor_voltage(self):
        result = self._infer('fw_fuses_itd_floor_v', 'floor voltage, precision U0.8', 128)
        # 128 * 3.90625 = 500 mV
        assert '500.0' in result

    def test_u0_8_zero(self):
        result = self._infer('fw_fuses_itd_floor_v', 'precision U0.8 units Volt', 0)
        assert result == '0.0 mV'

    # --- Pattern 11: /(2^N) divisor ---
    def test_slope_2pow12(self):
        """V per 1°C = fuse_value/(2^12) — ITD_SLOPE_ABOVE_CUTOFF_TEMP"""
        result = self._infer('fw_fuses_itd_slope_above_cutoff',
                              "Slope: V per 1'C = fuse_value/(2^12)", 11)
        # 11/4096 = 0.002685...
        assert '/' not in result or 'C' in result  # returns a numeric string
        assert '0.002686' in result or '0.00268' in result

    def test_slope_2pow16(self):
        """U-7.16 slope: value/65536 — description uses /(2^16) formula."""
        result = self._infer('fw_fuses_itd_slope1',
                              'slope correction 1/(2^16) degree Celsius', 160)
        # 160/65536 = 0.00244140625
        assert '0.002441' in result

    # --- No match returns empty string ---
    def test_no_pattern_returns_empty(self):
        result = self._infer('fw_fuses_some_count', 'A simple counter register', 42)
        assert result == ''
