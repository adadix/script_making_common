"""
Shared keyword / constant lists for VF Curve Manager.

This module is intentionally dependency-free (no project imports) so it can
be safely imported by any module in the tree without creating circular refs.
"""

# ---------------------------------------------------------------------------
# Target-disconnected / powered-down detection
# ---------------------------------------------------------------------------
# IPC error 0x8000000f — the SUT itself disappeared (power cycle / platform
# reset).  Distinct from 0x80000007 (OpenIPC crash) in hardware_access.py.
#
# Canonical source: was previously duplicated as:
#   _TARGET_DOWN_KEYWORDS      in utils/hardware_access.py
#   _DISCOVERY_TARGET_DOWN_KW  in discovery/auto_discover_vf_registers.py
#
TARGET_DOWN_KEYWORDS: list[str] = [
    '0x8000000f', 'internal_error',
    'target is powered down', 'powered down or otherwise not available',
    'exi bridge connection is not established',
    'device attached to the system is not functioning',
    'dci: device gone', 'target power lost',
    'jtag scans are not possible', 'failed to make tap',
]
