"""
Mock Hardware Backend for VF Curve Manager
==========================================

What mock mode is
-----------------
Normally every CLI/GUI command requires:
  real silicon on the board  →  OpenIPC running  →  ITP connection  →  live register reads

Mock mode short-circuits the entire hardware stack.  Instead of talking to
real silicon, the tool reads register values from the already-populated
vf_discovery_cache.json (built by a previous real-hardware run).

  Reads:  return the cached raw value (0 if not in cache)
  Writes: logged to stdout but NOT committed to hardware
  load_fuse_ram / flush_fuse_ram / reset_target: no-ops

Enable with:
  run_cli.bat --mock list
  run_cli.bat --mock show --domains cluster0_bigcore
  run_cli.bat --mock bump --domains cluster0_bigcore --value 10 --direction up --yes

Use cases
---------
• Script/pipeline development without a live board
• CI/CD validation of CLI argument parsing and output format
• Offline analysis of a previously captured platform snapshot
• Training / demo on a laptop with no silicon attached

Limitations
-----------
• No cold-reset or SUT-boot behaviour (reset_target always succeeds)
• Bumped/edited voltages are not persistent between invocations
  (in-memory writes only; changes do NOT persist to vf_discovery_cache.json)
• Frequency ratios are read from cache; may differ from a live system
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Default cache path: src/vf_discovery_cache.json
_CACHE_PATH = Path(__file__).parent.parent / 'vf_discovery_cache.json'


class MockFuseObject:
    """
    Simulates a nested ITP fuse-hierarchy object.

    Attribute reads  → return cached register value (0 if not in cache)
    Attribute writes → log to stdout; update in-memory so readback is consistent
    Sub-path access  → return another MockFuseObject (enables cdie.fuses.punit_fuses chaining)
    load_fuse_ram()  → no-op
    flush_fuse_ram() → no-op
    """

    def __init__(self, registers: dict, path: str = '<mock-root>'):
        # Use object.__setattr__ to bypass our own __setattr__ for private attrs
        object.__setattr__(self, '_registers', registers)
        object.__setattr__(self, '_path', path)

    # --------------------------------------------------------------------- #
    # Attribute access — register reads
    # --------------------------------------------------------------------- #
    def __getattr__(self, name: str):
        registers = object.__getattribute__(self, '_registers')
        path = object.__getattribute__(self, '_path')

        if name in registers:
            return registers[name]

        # Return a child MockFuseObject so chained paths resolve without error
        # e.g.  cdie.fuses.punit_fuses.fw_fuses_cluster0_bigcore_vf_voltage_0
        return MockFuseObject(registers, f'{path}.{name}')

    # --------------------------------------------------------------------- #
    # Attribute writes — register writes
    # --------------------------------------------------------------------- #
    def __setattr__(self, name: str, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
            return

        registers = object.__getattribute__(self, '_registers')
        path = object.__getattribute__(self, '_path')
        old_val = registers.get(name, 'N/A')

        # Update in-memory so subsequent readback returns the written value
        registers[name] = value

        log.debug("MOCK WRITE  %s.%s: %s -> %s  (NOT committed to hardware)",
                 path, name, old_val, value)

    # --------------------------------------------------------------------- #
    # Fuse RAM methods — no-ops in mock mode
    # --------------------------------------------------------------------- #
    def load_fuse_ram(self):
        path = object.__getattribute__(self, '_path')
        log.debug("MOCK load_fuse_ram()   path=%s  — no-op", path)

    def flush_fuse_ram(self):
        path = object.__getattribute__(self, '_path')
        log.debug("MOCK flush_fuse_ram()  path=%s  — no-op", path)


# --------------------------------------------------------------------------- #
# Cache loader
# --------------------------------------------------------------------------- #

def load_mock_registers(cache_path: str = None) -> dict:
    """
    Load register name → raw integer value mapping from vf_discovery_cache.json.

    Args:
        cache_path: Override path to the cache file.  Defaults to
                    src/vf_discovery_cache.json next to this module.

    Returns:
        dict: {register_name: raw_int_value}  (empty dict on any error)
    """
    path = Path(cache_path) if cache_path else _CACHE_PATH

    if not path.exists():
        log.warning("MOCK: Cache not found at %s — run a real-hardware discovery first", path)
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        registers: dict = {}
        for entry in data.get('registers', []):
            name = entry.get('name')
            value = entry.get('value', 0)
            if name:
                registers[name] = value

        platform = data.get('platform', 'unknown')
        ts = data.get('timestamp', 'unknown')
        count = len(registers)

        log.debug("MOCK loaded %d registers from cache  (platform=%s  timestamp=%s)",
                 count, platform, ts)
        return registers

    except Exception as ex:
        log.error("MOCK: Failed to load cache: %s", ex)
        return {}
