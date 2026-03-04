"""
Hardware register access utilities for VF Curve Manager.

Provides functions for:
- Reading voltage/frequency registers via fuse paths
- Writing voltage/frequency registers
- Fuse RAM loading and flushing
- Target reset operations
- ITP recovery from power states
- SUT reachability checking via ping

Module architecture
-------------------
This file is the canonical owner of all hardware-access code and shared
globals.  Two logical sub-module façades re-export subsets of these
functions with a cleaner, purpose-scoped interface:

  utils.itp_recovery  — ITP reconnect, boot-wait, cold-reset, reset_target
  utils.fuse_io       — fuse-object access, voltage/freq read/write, load/flush

New callers should import from the façade modules; all existing imports
from ``utils.hardware_access`` continue to work unchanged.
"""

import sys
import time
import os
import platform
import subprocess
import io
import contextlib
import re
import logging
import traceback
from datetime import datetime

log = logging.getLogger(__name__)

# Re-use canonical LSB constant from conversions module
from .conversions import _DEFAULT_VOLT_LSB_MV
# Shared keyword list (also used by discovery module — single source of truth)
from .constants import TARGET_DOWN_KEYWORDS as _TARGET_DOWN_KEYWORDS

# ---------------------------------------------------------------------------
# Module-level keyword lists used by load_fuse_ram / flush_fuse_ram retries
# Defined here once to avoid copy-paste in each function body.
# ---------------------------------------------------------------------------
_IPC_LOSS_KEYWORDS: list[str] = [
    'not_connected', 'not connected', '0x80000007',
    'connection to openipc was lost', 'openipc was lost',
    'openipc may no longer be running'
]
_CRITICAL_KEYWORDS: list[str] = [
    'slp_s5', 'sleep state', 'sleep_state',
    'packageawake', 'package awake',
    'pltrst', 'platform reset', 'reset is asserted',
    'early boot', 'earlyboot',
    'timeout setting clock mux', 'clock mux',
    'unable to wake cores', 'wake cores',
    'cpu power is de-asserted', 'power is de-asserted',
    'postcondition', 'post condition'
]
# _TARGET_DOWN_KEYWORDS imported from utils.constants above.

# Keywords that appear in exception messages when the SUT experiences a cold
# reset (power fully removed / SLP_S5).  Consolidated from the five separate
# inline lists that previously existed — use this one constant everywhere.
_COLD_RESET_KEYWORDS: list[str] = [
    'slp_s5', 'sleep state', 'sleep_state', 's5',
    'power lost', 'target power lost',
    'device gone',
    'cpu : off', 'cpu power', 'de-asserted', 'power is de-asserted',
    'powerdomain : cpu : off',
    'unable to apply feature survivability',
]

# Intel toolchain imports — only required for real hardware access.
#
# IMPORTANT: importing ipccli / itpii / pysvtools immediately opens an
# OpenIPC DCI connection.  We must NOT do this when running in mock mode
# (--mock CLI flag) or during tests (VF_MOCK_MODE=1 env var).
#
# Two guard signals are checked at module-load time:
#   1.  '--mock'    in sys.argv        — set by the CLI before any import
#   2.  VF_MOCK_MODE=1 env var         — set by the test suite / subprocesses
_SKIP_TOOLCHAIN: bool = (
    '--mock' in sys.argv
    or os.environ.get('VF_MOCK_MODE', '') == '1'
)

if not _SKIP_TOOLCHAIN:
    try:
        from pysvtools.pmext.services.regs import *
        import itpii
        import ipccli
        _TOOLCHAIN_AVAILABLE = True
    except Exception as _tc_err:   # catches ImportError AND platform TypeError
        _TOOLCHAIN_AVAILABLE = False
        itpii  = None   # noqa: F841
        ipccli = None   # noqa: F841
        log.warning("Toolchain import skipped: %s", _tc_err)
else:
    _TOOLCHAIN_AVAILABLE = False
    itpii  = None   # noqa: F841
    ipccli = None   # noqa: F841

# DO NOT initialize ITP here - it should be initialized in the main script
# DO NOT import namednodes here - it should be imported in the main script
# This module will use the ITP objects that are already in the global namespace

# Module-level references that will be set by init_hardware()
ipc = None
itp = None

# Namespace dict populated by init_hardware(); holds ITP root objects (cdie, soc, etc.)
# Key: root name (e.g. 'cdie'), Value: the live object injected by namednodes
_itp_namespace: dict = {}

# ── Mock mode ────────────────────────────────────────────────────────────────
# When True, all hardware reads return cached values from vf_discovery_cache.json
# and all writes are logged but NOT committed to silicon.
# Activated via init_hardware(mock_mode=True) or the --mock CLI flag.
MOCK_MODE: bool = False
_mock_root = None   # MockFuseObject instance (set by init_hardware in mock mode)

# ── SUT verification ─────────────────────────────────────────────────────────
# Default is now True — safe by default.  Use --no-sut-check to opt out.
ENABLE_SUT_VERIFICATION = True

# Recovery tracking
_recovery_in_progress = False
_last_recovery_time = 0
_recovery_cooldown = 5  # seconds between recovery attempts

# ── Cold-reset detective ──────────────────────────────────────────────────────
# Updated by load_fuse_ram() before every hardware call so that when a
# 0x8000000f "target powered down" error fires we can immediately print
# WHICH domain/path caused the system to reset.
_LAST_FUSE_ACCESS: dict = {
    'domain':        None,   # domain label
    'fuse_path':     None,   # e.g. "soc.fuses.punit_fuses"
    'fuse_ram_path': None,   # fuse RAM parent path
    'timestamp':     None,   # datetime string
}

# ── Session-level fuse RAM load tracker ───────────────────────────────────────
# Discovery (auto_discover_vf_registers.py) loads cdie.fuses once, which covers
# all child paths.  Without this guard the UI would re-load the same physical
# data, causing the postcondition (_enable_dcg IOSF-SB write) to fire into an
# active-boot power state and cold-reset the platform.
#
# Rule: if root path R was loaded, any path starting with R+'.' is also loaded.
_LOADED_FUSE_RAM_PATHS: set = set()

# Persistent boot-time statistics — delegate to the canonical owner module.
# Physical code lives in utils._boot_stats; imported here for backward compat.
from utils._boot_stats import (          # noqa: E402  (after guarded imports)
    _BOOT_STATS_PATH,
    _load_boot_stats,
    _save_boot_stats,
    get_adaptive_boot_timeout,
    record_boot_time,
)

# ── Physical implementations live in the sub-modules below. ──────────────────
# These imports must come AFTER all globals are defined (circular-import safe).
from utils.itp_recovery import (          # noqa: F401  (re-export)
    init_hardware,
    _do_itp_reconnect_sequence,
    _wait_for_target_reconnect,
    reinitialize_ipc_itp,
    _get_sut_ip,
    _ping_sut,
    check_itp_connection,
    wait_for_sut_boot,
    recover_from_deep_sleep,
    verify_post_fuse_update,
    check_power_state,
    detect_cold_reset,
    reset_target,
)

from utils.fuse_io import (               # noqa: F401  (re-export)
    notify_fuse_ram_loaded,
    _is_fuse_ram_already_loaded,
    get_fuse_object,
    get_fuse_ram_object,
    read_voltage_frequency,
    write_voltage,
    write_frequency,
    load_fuse_ram,
    flush_fuse_ram,
    read_all_wps,
    read_adder_voltages,
    read_delta_voltages,
    read_scalar_modifier,
    write_scalar_modifier,
    scalar_physical_to_raw,
    read_all_scalar_modifiers,
    restore_voltages,
    bump_all_voltages,
    read_frequency_ratios,
    write_frequency_ratios,
    apply_discovered_register_edits,
)
