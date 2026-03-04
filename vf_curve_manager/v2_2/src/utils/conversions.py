"""
Voltage and frequency conversion utilities.

Handles conversion between hardware register values and human-readable units:
- Voltage: raw units → Volts/mV using a per-domain LSB value
  Default: 3.90625 mV/LSB  (= 1/256 V, the standard Intel VF fuse encoding)
  Override: pass voltage_lsb_mv from domain_info when a domain uses a
  different step size (extracted from register descriptions during discovery).
- Frequency: raw ratio × freq_multiplier → MHz
"""

_DEFAULT_VOLT_LSB_MV = 1000.0 / 256   # 3.90625 mV per raw LSB


def voltage_to_mv(raw_value, voltage_lsb_mv: float = _DEFAULT_VOLT_LSB_MV):
    """
    Convert voltage register value to millivolts.

    Args:
        raw_value:       Register value (integer)
        voltage_lsb_mv:  mV per raw LSB (default 3.90625 = 1/256 V).
                         Pass domain_info.get('voltage_lsb_mv', 3.90625)
                         to use a domain-specific step size from discovery.

    Returns:
        float: Voltage in millivolts
    """
    if raw_value is None:
        return None
    return raw_value * voltage_lsb_mv


def voltage_to_volts(raw_value, voltage_lsb_mv: float = _DEFAULT_VOLT_LSB_MV):
    """
    Convert voltage register value to volts.

    Args:
        raw_value:       Register value (integer)
        voltage_lsb_mv:  mV per raw LSB (default 3.90625 = 1/256 V).

    Returns:
        float: Voltage in volts (rounded to 4 decimal places)
    """
    if raw_value is None:
        return None
    return round(raw_value * voltage_lsb_mv / 1000.0, 4)


def mv_to_raw(millivolts, voltage_lsb_mv: float = _DEFAULT_VOLT_LSB_MV):
    """
    Convert millivolts to voltage register value.

    Args:
        millivolts:      Voltage in mV
        voltage_lsb_mv:  mV per raw LSB (default 3.90625 = 1/256 V).

    Returns:
        int: Raw register value
    """
    return int(round(millivolts / voltage_lsb_mv))


def ratio_to_frequency(ratio, freq_multiplier=100):
    """
    Convert frequency ratio to MHz.
    
    Args:
        ratio: Raw frequency ratio from register
        freq_multiplier: Multiplier in MHz (default 100)
        
    Returns:
        float: Frequency in MHz (rounded to 2 decimal places)
    """
    if ratio is None:
        return None
    return round(ratio * freq_multiplier, 2)


def validate_voltage_range(voltage_v, min_v=0.4, max_v=1.5):
    """
    Validate voltage is within acceptable range.
    
    Args:
        voltage_v: Voltage in volts
        min_v: Minimum acceptable voltage (default 0.4V)
        max_v: Maximum acceptable voltage (default 1.5V)
        
    Returns:
        bool: True if voltage is valid, False otherwise
    """
    if voltage_v is None:
        return False
    return min_v <= voltage_v <= max_v


def validate_frequency_range(freq_mhz, min_mhz=400, max_mhz=6000):
    """
    Validate frequency is within acceptable range.
    
    Args:
        freq_mhz: Frequency in MHz
        min_mhz: Minimum acceptable frequency (default 400 MHz)
        max_mhz: Maximum acceptable frequency (default 6000 MHz)
        
    Returns:
        bool: True if frequency is valid, False otherwise
    """
    if freq_mhz is None:
        return False
    return min_mhz <= freq_mhz <= max_mhz
