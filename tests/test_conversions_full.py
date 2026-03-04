"""
Comprehensive tests for utils.conversions — covers all functions including
previously-uncovered None-guard branches and range-validation helpers.
"""
import sys
import os
import pytest

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.conversions import (
    voltage_to_mv,
    voltage_to_volts,
    mv_to_raw,
    ratio_to_frequency,
    validate_voltage_range,
    validate_frequency_range,
    _DEFAULT_VOLT_LSB_MV,
)


# ---------------------------------------------------------------------------
# Conversion constant
# ---------------------------------------------------------------------------

class TestDefaultLsb:
    def test_value_approx(self):
        assert abs(_DEFAULT_VOLT_LSB_MV - 3.90625) < 1e-6

    def test_equals_1_over_256_volts(self):
        assert abs(_DEFAULT_VOLT_LSB_MV - 1000.0 / 256) < 1e-10


# ---------------------------------------------------------------------------
# voltage_to_mv
# ---------------------------------------------------------------------------

class TestVoltageToMv:
    def test_zero_raw(self):
        assert voltage_to_mv(0) == 0.0

    def test_typical_raw(self):
        # 200 * 3.90625 = 781.25 mV
        assert abs(voltage_to_mv(200) - 781.25) < 1e-6

    def test_custom_lsb(self):
        assert voltage_to_mv(100, voltage_lsb_mv=5.0) == 500.0

    def test_none_returns_none(self):
        assert voltage_to_mv(None) is None

    def test_fractional_raw(self):
        result = voltage_to_mv(256)
        assert abs(result - 1000.0) < 1e-6


# ---------------------------------------------------------------------------
# voltage_to_volts
# ---------------------------------------------------------------------------

class TestVoltageToVolts:
    def test_zero_raw(self):
        assert voltage_to_volts(0) == 0.0

    def test_typical_raw(self):
        # 200 * 3.90625 / 1000 = 0.78125 V → Python banker's rounding to 4dp → 0.7812
        assert voltage_to_volts(200) == 0.7812

    def test_none_returns_none(self):
        assert voltage_to_volts(None) is None

    def test_custom_lsb(self):
        result = voltage_to_volts(100, voltage_lsb_mv=10.0)
        assert abs(result - 1.0) < 1e-6

    def test_roundtrip_consistent_with_mv(self):
        raw = 180
        mv = voltage_to_mv(raw)
        volts = voltage_to_volts(raw)
        assert abs(volts - mv / 1000.0) < 1e-3


# ---------------------------------------------------------------------------
# mv_to_raw
# ---------------------------------------------------------------------------

class TestMvToRaw:
    def test_zero(self):
        assert mv_to_raw(0) == 0

    def test_typical(self):
        # 781.25 / 3.90625 = 200
        assert mv_to_raw(781.25) == 200

    def test_roundtrip(self):
        for raw in [100, 150, 200, 230]:
            mv = voltage_to_mv(raw)
            assert mv_to_raw(mv) == raw

    def test_custom_lsb(self):
        assert mv_to_raw(500.0, voltage_lsb_mv=5.0) == 100


# ---------------------------------------------------------------------------
# ratio_to_frequency
# ---------------------------------------------------------------------------

class TestRatioToFrequency:
    def test_none_returns_none(self):
        assert ratio_to_frequency(None) is None

    def test_zero_ratio(self):
        assert ratio_to_frequency(0) == 0.0

    def test_typical(self):
        # ratio=30, multiplier=100 → 3000 MHz
        assert ratio_to_frequency(30, freq_multiplier=100) == 3000.0

    def test_default_multiplier(self):
        result = ratio_to_frequency(36)
        assert result == 3600.0

    def test_fractional_ratio(self):
        result = ratio_to_frequency(33.5, freq_multiplier=100)
        assert abs(result - 3350.0) < 0.01

    def test_custom_multiplier(self):
        assert ratio_to_frequency(10, freq_multiplier=133) == 1330.0


# ---------------------------------------------------------------------------
# validate_voltage_range
# ---------------------------------------------------------------------------

class TestValidateVoltageRange:
    def test_none_returns_false(self):
        assert validate_voltage_range(None) is False

    def test_below_min(self):
        assert validate_voltage_range(0.3) is False

    def test_at_min(self):
        assert validate_voltage_range(0.4) is True

    def test_in_range(self):
        assert validate_voltage_range(0.9) is True

    def test_at_max(self):
        assert validate_voltage_range(1.5) is True

    def test_above_max(self):
        assert validate_voltage_range(1.6) is False

    def test_custom_range_pass(self):
        assert validate_voltage_range(0.7, min_v=0.5, max_v=0.8) is True

    def test_custom_range_fail(self):
        assert validate_voltage_range(0.9, min_v=0.5, max_v=0.8) is False

    def test_zero_voltage(self):
        assert validate_voltage_range(0.0) is False


# ---------------------------------------------------------------------------
# validate_frequency_range
# ---------------------------------------------------------------------------

class TestValidateFrequencyRange:
    def test_none_returns_false(self):
        assert validate_frequency_range(None) is False

    def test_below_min(self):
        assert validate_frequency_range(300) is False

    def test_at_min(self):
        assert validate_frequency_range(400) is True

    def test_in_range(self):
        assert validate_frequency_range(3000) is True

    def test_at_max(self):
        assert validate_frequency_range(6000) is True

    def test_above_max(self):
        assert validate_frequency_range(7000) is False

    def test_custom_range_pass(self):
        assert validate_frequency_range(2000, min_mhz=1000, max_mhz=4000) is True

    def test_custom_range_fail(self):
        assert validate_frequency_range(500, min_mhz=1000, max_mhz=4000) is False

    def test_zero_frequency(self):
        assert validate_frequency_range(0) is False
