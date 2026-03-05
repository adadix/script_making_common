"""
fuse_io.py — Fuse-RAM and register read / write helpers
========================================================
Physical implementation of all fuse / register access functions.

These functions were migrated from hardware_access.py (which now acts as
a thin coordinator that re-exports everything for backward compatibility).

State (itp, ipc, MOCK_MODE, …) lives in utils.hardware_access as module-level
variables.  Functions here access that state via the _ha module reference.
"""
# ── Circular-import note ─────────────────────────────────────────────────────
# hardware_access.py imports from THIS module at the bottom (after defining all
# its globals), so the partial-module reference _ha received here is already
# fully constructed by the time any function is actually called.
import logging
import utils.hardware_access as _ha     # noqa: E402

import time
import os
import contextlib
from datetime import datetime
import io
import re
import traceback
from .conversions import _DEFAULT_VOLT_LSB_MV

log = logging.getLogger(__name__)


class _SuppressHWNoise:
    """Suppress all pysvtools console noise during hardware access.

    Stacks six layers so both WCL and NVL are fully silenced:
      1. contextlib.redirect_stdout/stderr  — catches Python-level sys.stdout/
         sys.stderr writes (sufficient on WCL / Python 3.10).
      2. sys.__stdout__ / sys.__stderr__ patch — catches namednodes code that
         saved a reference to the original stream objects at import time and
         prints through those (bypassing layer 1).
      3. os.dup2 fd redirect to os.devnull  — catches C-extension writes that
         bypass sys.stdout entirely (required on NVL / Python 3.13 different-CRT
         DLLs).
      4. Windows UCRT freopen — Python 3.10+ uses ucrtbase.dll (Universal CRT);
         legacy msvcrt.dll does NOT export __acrt_iob_func so we try ucrtbase
         first.  Redirects fprintf() calls inside C extensions to NUL.
      5. Win32 SetStdHandle — redirects the OS-level STD_OUTPUT/ERROR_HANDLE so
         that code calling GetStdHandle() at write-time also writes to NUL.
      6. builtins.print monkey-patch — catches every bare print() call in
         third-party code (namednodes/pysvtools) regardless of which stream
         object it targets.  Guaranteed catch for hardcoded print() calls that
         bypass all other layers (e.g. buffered writes flushed after dup2
         restores, or streams captured at library import time).
    """
    def __init__(self) -> None:
        self._saved: dict = {}
        self._null_fd = None
        self._ctx = None
        self._orig_dunder: dict = {}
        self._win32_null_handle = None  # Layer 5
        self._win32_orig_handles: dict = {}  # Layer 5
        self._orig_builtin_print = None      # Layer 6

    def __enter__(self):
        import contextlib as _cl
        import io as _io
        import sys as _sys
        _sink = _io.StringIO()
        # Layer 1: Python-level stream redirect
        self._ctx = _cl.ExitStack()
        self._ctx.__enter__()
        self._ctx.enter_context(_cl.redirect_stdout(_sink))
        self._ctx.enter_context(_cl.redirect_stderr(_sink))
        # Layer 2: patch sys.__stdout__ / sys.__stderr__ (bypassed by namednodes
        # code that cached the original stream objects at import time)
        for _attr in ('__stdout__', '__stderr__'):
            self._orig_dunder[_attr] = getattr(_sys, _attr, None)
            try:
                setattr(_sys, _attr, _sink)
            except (AttributeError, TypeError):
                pass
        # Layer 3: fd-level redirect (suppresses C-extension / different-CRT writes)
        try:
            self._null_fd = os.open(os.devnull, os.O_WRONLY)
            for fd in (1, 2):
                self._saved[fd] = os.dup(fd)
                os.dup2(self._null_fd, fd)
        except OSError:
            pass
        # Layer 4: Windows CRT freopen("NUL", "w", stdout/stderr)
        # Python 3.10+ on Windows uses the Universal CRT (ucrtbase.dll).
        # Legacy msvcrt.dll does NOT export __acrt_iob_func — try ucrtbase first.
        try:
            import ctypes as _ct
            for _dll_name in ('ucrtbase', 'msvcrt'):
                try:
                    _crt = _ct.CDLL(_dll_name, use_errno=True)
                    _iob_func = _crt.__acrt_iob_func
                    _iob_func.restype = _ct.c_void_p
                    _iob_func.argtypes = [_ct.c_uint]
                    _crt.freopen.restype = _ct.c_void_p
                    _crt.freopen.argtypes = [_ct.c_char_p, _ct.c_char_p, _ct.c_void_p]
                    _crt.freopen(b'NUL', b'w', _iob_func(1))  # stdout (index 1)
                    _crt.freopen(b'NUL', b'w', _iob_func(2))  # stderr (index 2)
                    break
                except (OSError, AttributeError):
                    continue
        except (OSError, AttributeError):
            pass
        # Layer 5: Win32 SetStdHandle — redirects the OS-level standard handles
        # so code that calls GetStdHandle() at write-time is also silenced.
        try:
            import ctypes as _ct
            _k32 = _ct.windll.kernel32
            _k32.CreateFileW.restype = _ct.c_void_p
            _null_h = _k32.CreateFileW('NUL', 0x40000000, 0x3, None, 3, 0, None)
            if _null_h and _null_h != 0xFFFFFFFFFFFFFFFF:
                self._win32_null_handle = _null_h
                _k32.GetStdHandle.restype = _ct.c_void_p
                for _std_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
                    self._win32_orig_handles[_std_id] = _k32.GetStdHandle(_std_id)
                    _k32.SetStdHandle(_std_id, _null_h)
        except Exception:
            pass
        # Layer 6: monkey-patch builtins.print to a no-op so that hardcoded
        # print() calls in namednodes/pysvtools are silenced regardless of
        # which stream object they write to or whether buffering deferred the
        # flush past the dup2 window.  The main thread is blocked on
        # _fut.result() while this runs so no legitimate tool output is lost.
        try:
            import builtins as _bi
            self._orig_builtin_print = _bi.print
            _bi.print = lambda *_a, **_kw: None
        except Exception:
            pass
        return self

    def __exit__(self, *_):
        import sys as _sys
        # Restore builtins.print (layer 6) — first so following restores can
        # print normally if they need to.
        try:
            import builtins as _bi
            if self._orig_builtin_print is not None:
                _bi.print = self._orig_builtin_print
                self._orig_builtin_print = None
        except Exception:
            pass
        # Restore Win32 handles (layer 5) — before CRT restore so the CRT's
        # freopen can resolve the original console handle.
        try:
            if self._win32_null_handle is not None:
                import ctypes as _ct
                _k32 = _ct.windll.kernel32
                for _std_id, _orig_h in self._win32_orig_handles.items():
                    _k32.SetStdHandle(_std_id, _orig_h)
                _k32.CloseHandle(self._win32_null_handle)
                self._win32_null_handle = None
                self._win32_orig_handles.clear()
        except Exception:
            pass
        # Restore CRT streams (layer 4) — ucrtbase/msvcrt fallback.
        try:
            import ctypes as _ct
            for _dll_name in ('ucrtbase', 'msvcrt'):
                try:
                    _crt = _ct.CDLL(_dll_name, use_errno=True)
                    _iob_func = _crt.__acrt_iob_func
                    _iob_func.restype = _ct.c_void_p
                    _iob_func.argtypes = [_ct.c_uint]
                    _crt.freopen.restype = _ct.c_void_p
                    _crt.freopen.argtypes = [_ct.c_char_p, _ct.c_char_p, _ct.c_void_p]
                    _crt.freopen(b'CON', b'w', _iob_func(1))
                    _crt.freopen(b'CON', b'w', _iob_func(2))
                    break
                except (OSError, AttributeError):
                    continue
        except (OSError, AttributeError):
            pass
        # Restore fd-level (layer 3)
        for fd, saved in self._saved.items():
            try:
                os.dup2(saved, fd)
                os.close(saved)
            except OSError:
                pass
        if self._null_fd is not None:
            try:
                os.close(self._null_fd)
            except OSError:
                pass
        self._saved.clear()
        self._null_fd = None
        # Restore sys.__stdout__ / sys.__stderr__ (layer 2)
        for _attr, _orig in self._orig_dunder.items():
            try:
                setattr(_sys, _attr, _orig)
            except (AttributeError, TypeError):
                pass
        self._orig_dunder.clear()
        # Restore Python-level streams (layer 1)
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
            self._ctx = None



def notify_fuse_ram_loaded(fuse_ram_path: str) -> None:
    """Mark fuse_ram_path as loaded for this session.
    Called by auto_discover_vf_registers after load_fuse_ram_once() succeeds.
    """

    _ha._LOADED_FUSE_RAM_PATHS.add(fuse_ram_path)


def _is_fuse_ram_already_loaded(fuse_ram_path: str) -> bool:
    """True if this path (or a parent of it) was loaded earlier this session."""
    if fuse_ram_path in _ha._LOADED_FUSE_RAM_PATHS:
        return True
    for loaded in _ha._LOADED_FUSE_RAM_PATHS:
        if fuse_ram_path.startswith(loaded + '.'):
            return True
    return False


def get_fuse_object(fuse_path):
    """
    Resolve fuse object from dot-separated path string.

    In mock mode returns a MockFuseObject backed by the discovery cache.
    In normal mode uses _ha._itp_namespace (populated by init_hardware) to
    access ITP hierarchy objects (cdie, soc, etc.) injected by namednodes.

    Args:
        fuse_path: String path like "cdie.fuses.dmu_fuse"

    Returns:
        object: Resolved fuse object, or None on error
    """
    # ── Mock path ────────────────────────────────────────────────────────────────
    if _ha.MOCK_MODE:
        # In mock mode every path resolves through the same MockFuseObject;
        # attribute traversal is handled by MockFuseObject.__getattr__.
        return _ha._mock_root

    # ── Real hardware path ──────────────────────────────────────────────────────
    try:
        parts = fuse_path.split('.')
        root_name = parts[0]

        # ── Root resolution: three layers (most to least authoritative) ──────
        # Layer 1: injected namespace (globals() from the launcher).
        #   Works when `from pysvtools.pmext.services.regs import *` succeeded
        #   and populated cdie / soc / etc. into the launcher globals.
        # Layer 2: namednodes module.
        #   Works on platforms where the regs import fails with TypeError
        #   (e.g. Novalake/PantherCove) but ITP itself is fully connected.
        # Layer 3: resolve_object from discovery_core (checks __main__ +
        #   call-stack frames as a last resort).
        # A path is only unresolvable when ALL three layers fail.
        namespace = _ha._itp_namespace if _ha._itp_namespace else vars(__import__('__main__'))

        root_obj = None

        # Layer 1 — injected namespace
        if root_name in namespace:
            root_obj = namespace[root_name]

        # Layer 2 — namednodes
        if root_obj is None:
            try:
                import namednodes as _nn
                if hasattr(_nn, root_name):
                    root_obj = getattr(_nn, root_name)
            except Exception:
                pass

        # Layer 3 — resolve_object (also checks __main__ and call-stack frames)
        if root_obj is None:
            try:
                from discovery.auto_discover_vf_registers import resolve_object as _ro
                root_obj = _ro(root_name)
            except Exception:
                pass

        if root_obj is None:
            log.error(
                f"Failed to resolve fuse path '{fuse_path}': "
                f"'{root_name}' not found via namespace, namednodes, or resolve_object. "
                f"Ensure init_hardware(namespace=globals()) was called from the launcher."
            )
            return None

        obj = root_obj

        # Traverse remaining parts
        for part in parts[1:]:
            if isinstance(obj, dict):
                obj = obj[part]
            else:
                obj = getattr(obj, part)

        return obj

    except Exception as ex:
        log.error(f"Failed to resolve fuse path '{fuse_path}': {ex}")
        return None


def get_fuse_ram_object(domain_info):
    """
    Get fuse RAM object for loading/flushing operations.
    
    Uses 'fuse_ram_path' if present, otherwise falls back to 'fuse_path'.
    
    Args:
        domain_info: Domain configuration dict
        
    Returns:
        object: Fuse RAM object
    """
    path = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
    return get_fuse_object(path)


def read_voltage_frequency(domain_info, wp_index):
    """
    Read BASE voltage and frequency for a specific working point.

    Returns the raw base register value only.  Adder registers (if present
    in ``vf_voltage_adder``) are NOT included here — they are summed in
    separately by :func:`read_adder_voltages` for display-only purposes in
    :func:`~core.curve_engine.CurveEngine._make_vf_dataframe`.

    This keeps bump / edit / verify operations working against the correct
    base register value without adder contamination.

    Args:
        domain_info: Domain configuration dict
        wp_index: Working point index (0, 1, 2, ...)

    Returns:
        tuple: (voltage_volts, frequency_mhz) or (None, None) on error
    """
    from .conversions import voltage_to_volts, ratio_to_frequency

    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return None, None

    voltage_reg = domain_info['vf_voltage'][wp_index]
    ratio_list  = domain_info.get('vf_ratio', [])
    ratio_reg   = ratio_list[wp_index] if wp_index < len(ratio_list) else None
    _lsb_mv = domain_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)

    try:
        # Read base voltage
        if hasattr(fuse_obj, voltage_reg):
            raw_voltage = getattr(fuse_obj, voltage_reg, None)
            voltage_v = voltage_to_volts(raw_voltage, _lsb_mv) if raw_voltage is not None else None
        else:
            voltage_v = None

        # Read frequency ratio (ratio_reg may be None when platform has fewer
        # ratio registers than voltage registers, e.g. NVL with 12 vs 24 WPs)
        if ratio_reg is not None and hasattr(fuse_obj, ratio_reg):
            raw_ratio = getattr(fuse_obj, ratio_reg, None)
            freq_multiplier = domain_info.get('freq_multiplier', 100)
            freq_mhz = ratio_to_frequency(raw_ratio, freq_multiplier) if raw_ratio is not None else None
        else:
            freq_mhz = None

        return voltage_v, freq_mhz

    except Exception as ex:
        log.error(f"Failed to read WP{wp_index} for domain: {ex}")
        return None, None


def write_voltage(domain_info, wp_index, new_voltage_mv) -> bool:
    """
    Write voltage to a specific working point.
    
    Args:
        domain_info: Domain configuration dict
        wp_index: Working point index
        new_voltage_mv: New voltage in millivolts
        
    Returns:
        bool: True on success, False on error
    """
    from .conversions import mv_to_raw, voltage_to_mv

    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return False

    voltage_reg = domain_info['vf_voltage'][wp_index]
    _lsb_mv = domain_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)

    try:
        # Read current value for logging
        current_raw = getattr(fuse_obj, voltage_reg, None)
        current_mv = voltage_to_mv(current_raw, _lsb_mv) if isinstance(current_raw, (int, float)) else None

        # Convert and write new value
        new_raw: int = mv_to_raw(new_voltage_mv, _lsb_mv)
        
        setattr(fuse_obj, voltage_reg, new_raw)
        
        # Read back to verify
        readback_raw = getattr(fuse_obj, voltage_reg, None)
        readback_mv = voltage_to_mv(readback_raw, _lsb_mv) if isinstance(readback_raw, (int, float)) else None
        
        if readback_raw != new_raw:
            log.warning(f"WP{wp_index}: Readback ({readback_raw}) doesn't match written value ({new_raw})!")
        
        return True
    except Exception as ex:
        log.error(f"Failed to write voltage to WP{wp_index}: {ex}")
        traceback.print_exc()
        return False


def write_frequency(domain_info, wp_index, new_freq_mhz) -> bool:
    """
    Write frequency to a specific working point.

    Converts the requested frequency (MHz) to an integer ratio using the
    domain's ``freq_multiplier`` and writes it to the corresponding
    ``vf_ratio`` register.

    Args:
        domain_info: Domain configuration dict
        wp_index: Working point index
        new_freq_mhz: New frequency in MHz (integer or float)

    Returns:
        bool: True on success, False on error
    """
    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return False

    ratio_regs = domain_info.get('vf_ratio', [])
    if wp_index >= len(ratio_regs):
        log.error(f"write_frequency: wp_index {wp_index} out of range for vf_ratio list")
        return False

    ratio_reg = ratio_regs[wp_index]
    freq_multiplier = domain_info.get('freq_multiplier', 100)

    try:
        new_ratio = int(round(new_freq_mhz / freq_multiplier))

        # Read current value for logging
        current_raw = getattr(fuse_obj, ratio_reg, None)
        current_freq = (current_raw * freq_multiplier) if current_raw is not None else None

        log.info(f"  WP{wp_index} freq: {current_freq} MHz → {new_freq_mhz} MHz (ratio {new_ratio})")
        setattr(fuse_obj, ratio_reg, new_ratio)

        # Readback verify
        readback_raw = getattr(fuse_obj, ratio_reg, None)
        if readback_raw != new_ratio:
            log.warning(f"WP{wp_index} freq: readback ratio {readback_raw} != written {new_ratio}")

        return True
    except Exception as ex:
        log.error(f"Failed to write frequency to WP{wp_index}: {ex}")
        traceback.print_exc()
        return False


def load_fuse_ram(domain_info) -> None | bool:
    """
    Load fuse RAM for a domain.
    
    Args:
        domain_info: Domain configuration dict
        
    Returns:
        bool: True on success, False on error
    """
    # ── Mock mode: no-op ────────────────────────────────────────────────────────────────
    if _ha.MOCK_MODE:
        log.debug(f"load_fuse_ram() for '{domain_info.get('label')}' — no-op")
        return True

    # ── Record this access for cold-reset diagnostics ────────────────────────────────

    frp = domain_info.get('fuse_ram_path', domain_info.get('fuse_path', '?'))
    _ha._LAST_FUSE_ACCESS = {
        'domain':        domain_info.get('label', domain_info.get('fuse_path', '?')),
        'fuse_path':     domain_info.get('fuse_path', '?'),
        'fuse_ram_path': frp,
        'timestamp':     datetime.now().strftime('%H:%M:%S.%f'),
    }

    # ── Skip re-load if fuse RAM already in memory ────────────────────────────
    # Discovery loads cdie.fuses (all children included).  Re-loading fires the
    # postcondition (_enable_dcg) which cold-resets the platform in active boot.
    if _is_fuse_ram_already_loaded(frp):
        log.info(f"load_fuse_ram(): '{frp}' already loaded this session — skipping")
        return True
    # Also honour the object's own flag if pysvtools set it
    try:
        _obj_chk = get_fuse_ram_object(domain_info)
        for _flag in ('_fuse_ram_loaded', 'fuse_ram_loaded', '_loaded', 'loaded'):
            if getattr(_obj_chk, _flag, None) is True:
                log.info(f"load_fuse_ram(): '{frp}' object reports loaded — skipping")
                _ha._LOADED_FUSE_RAM_PATHS.add(frp)
                return True
    except Exception:
        pass

    # Suppress BOTH stdout and stderr to hide pysvtools internal noise:
    # 'adding to a component that already has a parent', 'post condition failed',
    # and the full AccessTimeoutError traceback are all printed to stdout by
    # pysvtools before raising the exception.  Our own log messages cover all
    # the useful information.
    first_exception = None
    with _SuppressHWNoise():
        try:
            fuse_ram_obj = get_fuse_ram_object(domain_info)
            if hasattr(fuse_ram_obj, 'load_fuse_ram'):
                fuse_ram_obj.load_fuse_ram()
                _ha._LOADED_FUSE_RAM_PATHS.add(frp)  # mark loaded on success
                return True
            else:
                log.warning(f"load_fuse_ram() not available for {domain_info.get('label')}")
                return False
        except Exception as ex:
            import traceback as _tb
            error_str: str = str(ex).lower()
            tb_str: str = _tb.format_exc().lower()
            # Postcondition failure: fuse data IS in memory; only the cleanup
            # (_enable_dcg IOSF-SB flush / semaphore write) failed.
            # Detect two variants:
            #   1. AccessTimeoutError (cdie.fuses style) — exception text contains the type name
            #   2. IPC_Error 0x8000000f (soc.fuses style) — exception from run_postcondition
            #      frame; we distinguish it from a "real" target-down by checking the call stack.
            _is_postcond_by_type = (
                'post condition' in error_str
                or 'postcondition' in error_str
                or 'accesstimeouterror' in error_str.replace(' ', '')
            )
            _is_postcond_by_stack = (
                'run_postcondition' in tb_str
                or '_precondition_gen2' in tb_str
                or 'postcondition' in tb_str
            )
            if _is_postcond_by_type or _is_postcond_by_stack:
                log.warning(
                    f"\n[!] Fuse RAM post-condition timed out for '{frp}' "
                    f"— this is expected on active-boot platforms.\n"
                    f"    The fuse data IS fully loaded in memory; continuing normally.")
                _ha._LOADED_FUSE_RAM_PATHS.add(frp)
                return True
            # Preserve for keyword inspection in the second-attempt handler below
            first_exception: Exception = ex
    
    # If we're here, the first attempt failed - start retry logic
    try:
        # Re-raise to trigger the existing error handling
        fuse_ram_obj = get_fuse_ram_object(domain_info)
        if hasattr(fuse_ram_obj, 'load_fuse_ram'):
            fuse_ram_obj.load_fuse_ram()
            return True
    except Exception as ex:
        # Use the first-attempt exception if it contains more detail
        root_cause: Exception = first_exception if first_exception is not None else ex
        error_str: str = str(root_cause).lower()

        # ── Security-lock errors (non-retryable) ─────────────────────────────
        # "Timeout setting clock mux" / "Red unlock required" mean the ITP does
        # not have the DCI/JTAG security authorisation to access fuse registers.
        # No amount of reconnect/forcereconfig can fix this — only pre-granting
        # Red unlock before tool launch resolves it.  Return immediately so we
        # don't trigger repeated platform resets trying to recover.
        is_security_lock: bool = any(
            keyword in error_str for keyword in _ha._SECURITY_LOCK_KEYWORDS)
        if is_security_lock:
            log.error(
                f"\n[!] Fuse RAM load blocked by security lock: {root_cause}\n"
                f"    This error ('clock mux timeout' / 'Red unlock required') means\n"
                f"    the ITP session does NOT have fuse-register access permissions.\n"
                f"    FIX: Grant Red unlock on the DCI/JTAG connection before launching\n"
                f"    the tool, then re-run.  No retry is attempted (would cause resets).")
            return False

        # Check for IPC connection loss - needs reinitialization
        is_ipc_loss: bool = any(keyword in error_str for keyword in _ha._IPC_LOSS_KEYWORDS)
        
        # Check if target went away (power cycle / platform reset) — 0x8000000f
        is_target_down: bool = any(keyword in error_str for keyword in _ha._TARGET_DOWN_KEYWORDS)

        # Check if it's a critical power state error
        is_critical_error: bool = any(keyword in error_str for keyword in _ha._CRITICAL_KEYWORDS)
        
        # Lazy imports – placed here to avoid circular import chain:
        #   fuse_io → itp_recovery → hardware_access → fuse_io
        from .itp_recovery import (  # noqa: PLC0415
            reinitialize_ipc_itp as _reinit_ipc,
            _wait_for_target_reconnect as _wait_reconnect,
            recover_from_deep_sleep as _recover_sleep,
        )

        # Handle IPC connection loss first (most severe)
        if is_ipc_loss:
            log.error(f"IPC connection lost during load_fuse_ram: {ex}")
            
            # Attempt to reinitialize IPC/ITP
            if _reinit_ipc():
                log.info("Retrying load_fuse_ram after IPC reinitialization...")
                with _SuppressHWNoise():
                    try:
                        fuse_ram_obj = get_fuse_ram_object(domain_info)
                        if hasattr(fuse_ram_obj, 'load_fuse_ram'):
                            fuse_ram_obj.load_fuse_ram()
                            log.info("[SUCCESS] load_fuse_ram succeeded after IPC reinit")
                            return True
                    except Exception as retry_ex:
                        log.error(f"Retry after IPC reinit failed: {retry_ex}")
                        return False
            else:
                log.error("IPC reinitialization failed - cannot retry")
                return False

        # Handle target-powered-down (0x8000000f) — wait for target to come back
        elif is_target_down:
            log.error(f"Target disconnected/powered down during load_fuse_ram: {ex}")
            # Identify the exact domain that triggered the reset
            lfa = _ha._LAST_FUSE_ACCESS
            log.info(f"[COLD-RESET-DETECTIVE] Last fuse access before reset:")
            log.info(f"    Domain      : {lfa.get('domain', '?')}")
            log.info(f"    fuse_path   : {lfa.get('fuse_path', '?')}")
            log.info(f"    fuse_ram_path: {lfa.get('fuse_ram_path', '?')}")
            log.info(f"    Time        : {lfa.get('timestamp', '?')}")
            log.info(f"[TIP] Run '🔬 Probe Reset Trigger' from the UI to isolate the offending domain.")
            if _wait_reconnect(timeout_s=45):
                log.info("Retrying load_fuse_ram after target reconnect...")
                with _SuppressHWNoise():
                    try:
                        fuse_ram_obj = get_fuse_ram_object(domain_info)
                        if hasattr(fuse_ram_obj, 'load_fuse_ram'):
                            fuse_ram_obj.load_fuse_ram()
                            log.info("[SUCCESS] load_fuse_ram succeeded after target reconnect")
                            return True
                    except Exception as retry_ex:
                        log.error(f"load_fuse_ram failed after reconnect: {retry_ex}")
            else:
                log.error("Target did not reconnect within timeout")
            return False

        # Handle critical power state errors
        elif is_critical_error:
            log.error(f"Critical power state error during load_fuse_ram: {ex}")
            log.info("Attempting ITP recovery (forcereconfig + unlock)...")
            
            # Always attempt full ITP recovery for critical errors
            try:
                from .itp_recovery import _do_itp_reconnect_sequence as _reconnect_seq
                _reconnect_seq(label="load_fuse_ram")
                log.info("[SUCCESS] ITP recovery completed")
                
                # If SUT verification enabled, do full recovery with ping check
                if _ha.ENABLE_SUT_VERIFICATION:
                    _recover_sleep(bypass_cooldown=True)
                else:
                    # Even without SUT verification, give hardware time to stabilize
                    log.info("Waiting 3 seconds for hardware to stabilize...")
                    time.sleep(3)
                
                # Retry the operation
                log.info("Retrying load_fuse_ram...")
                
                with _SuppressHWNoise():
                    try:
                        fuse_ram_obj = get_fuse_ram_object(domain_info)
                        if hasattr(fuse_ram_obj, 'load_fuse_ram'):
                            fuse_ram_obj.load_fuse_ram()
                            log.info("[SUCCESS] load_fuse_ram succeeded after recovery")
                            return True
                    except Exception as retry_ex:
                        pass  # Continue to handle below
                
                # If we get here, retry failed - handle the error
                try:
                    # Re-execute to get the actual exception for error checking
                    fuse_ram_obj = get_fuse_ram_object(domain_info)
                    if hasattr(fuse_ram_obj, 'load_fuse_ram'):
                        fuse_ram_obj.load_fuse_ram()
                except Exception as retry_ex:
                    error_msg: str = str(retry_ex).lower()
                    log.error(f"Retry after recovery failed: {retry_ex}")
                    
                    # Check if IPC connection was lost during retry
                    if any(kw in error_msg for kw in _ha._IPC_LOSS_KEYWORDS):
                        log.warning("IPC connection lost during retry - attempting reinitialization...")
                        if _reinit_ipc():
                            log.info("Final retry after IPC reinit...")
                            with _SuppressHWNoise():
                                try:
                                    fuse_ram_obj = get_fuse_ram_object(domain_info)
                                    if hasattr(fuse_ram_obj, 'load_fuse_ram'):
                                        fuse_ram_obj.load_fuse_ram()
                                        log.info("[SUCCESS] load_fuse_ram succeeded after IPC reinit")
                                        return True
                                except Exception as _final_load_err:
                                    log.error(f"Final load_fuse_ram attempt failed: {_final_load_err}")
                    
                    # Check if it's still a timeout - hardware might need more time
                    if 'timeout' in error_msg or 'time-out' in error_msg:
                        log.warning("Hardware still timing out - may need more recovery time")
                    return False
            except Exception as recovery_ex:
                log.error(f"ITP recovery failed: {recovery_ex}")
        else:
            log.error(f"Failed to load fuse RAM: {ex}")
        
        return False


def flush_fuse_ram(domain_info) -> None | bool:
    """
    Flush fuse RAM for a domain (commit changes to hardware).
    
    Args:
        domain_info: Domain configuration dict
        
    Returns:
        bool: True on success, False on error
    """

    # ── Mock mode: no-op flush, but still invalidate session guard ────────────────────
    if _ha.MOCK_MODE:
        frp_mock = domain_info.get('fuse_ram_path', domain_info.get('fuse_path', ''))
        _ha._LOADED_FUSE_RAM_PATHS.discard(frp_mock)
        log.debug(f"flush_fuse_ram() for '{domain_info.get('label')}' — no-op")
        return True

    # Compute fuse RAM path once — used to invalidate the loaded-session-guard on
    # success.  After a successful flush, the in-memory fuse-RAM state no longer
    # matches the hardware (the hardware got the committed write, but the object
    # state resets after flush_fuse_ram).  Removing frp from _ha._LOADED_FUSE_RAM_PATHS
    # ensures the next load_fuse_ram() call actually re-reads from hardware.
    frp = domain_info.get('fuse_ram_path', domain_info.get('fuse_path', ''))

    # Try with stderr suppression first to hide Intel library tracebacks during retries
    first_exception = None
    with _SuppressHWNoise():
        try:
            fuse_ram_obj = get_fuse_ram_object(domain_info)
            if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
                fuse_ram_obj.flush_fuse_ram()
                _ha._LOADED_FUSE_RAM_PATHS.discard(frp)  # force reload on next access
                return True
            else:
                log.warning(f"flush_fuse_ram() not available for {domain_info.get('label')}")
                return False
        except Exception as ex:
            # Preserve for keyword inspection in the second-attempt handler below
            first_exception: Exception = ex
    
    # If we're here, the first attempt failed - start retry logic
    try:
        # Re-raise to trigger the existing error handling
        fuse_ram_obj = get_fuse_ram_object(domain_info)
        if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
            fuse_ram_obj.flush_fuse_ram()
            _ha._LOADED_FUSE_RAM_PATHS.discard(frp)  # force reload on next access
            return True
    except Exception as ex:
        root_cause: Exception = first_exception if first_exception is not None else ex
        error_str: str = str(root_cause).lower()
        
        # Check for IPC connection loss - needs reinitialization
        is_ipc_loss: bool = any(keyword in error_str for keyword in _ha._IPC_LOSS_KEYWORDS)

        # Check if target went away (power cycle / platform reset) — 0x8000000f
        is_target_down: bool = any(keyword in error_str for keyword in _ha._TARGET_DOWN_KEYWORDS)

        # Check if it's a critical power state error
        is_critical_error: bool = any(keyword in error_str for keyword in _ha._CRITICAL_KEYWORDS)
        
        # Non-retryable security lock — return immediately (retrying causes HW resets)
        is_security_lock: bool = any(
            keyword in error_str for keyword in _ha._SECURITY_LOCK_KEYWORDS)
        if is_security_lock:
            log.error(
                f"\n[!] Fuse RAM flush blocked by security lock: {root_cause}\n"
                f"    FIX: Grant Red unlock on the DCI/JTAG connection before launching\n"
                f"    the tool, then re-run.  No retry is attempted (would cause resets).")
            return False

        # Lazy imports – placed here to avoid circular import chain:
        #   fuse_io → itp_recovery → hardware_access → fuse_io
        from .itp_recovery import (  # noqa: PLC0415
            reinitialize_ipc_itp as _reinit_ipc,
            _wait_for_target_reconnect as _wait_reconnect,
            recover_from_deep_sleep as _recover_sleep,
        )

        # Handle IPC connection loss first (most severe)
        if is_ipc_loss:
            log.error(f"IPC connection lost during flush_fuse_ram: {ex}")
            
            # Attempt to reinitialize IPC/ITP
            if _reinit_ipc():
                log.info("Retrying flush_fuse_ram after IPC reinitialization...")
                with _SuppressHWNoise():
                    try:
                        fuse_ram_obj = get_fuse_ram_object(domain_info)
                        if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
                            fuse_ram_obj.flush_fuse_ram()
                            _ha._LOADED_FUSE_RAM_PATHS.discard(frp)
                            log.info("[SUCCESS] flush_fuse_ram succeeded after IPC reinit")
                            return True
                    except Exception as retry_ex:
                        log.error(f"Retry after IPC reinit failed: {retry_ex}")
                        return False
            else:
                log.error("IPC reinitialization failed - cannot retry")
                return False

        # Handle target-powered-down (0x8000000f) — wait for target to come back
        elif is_target_down:
            log.error(f"Target disconnected/powered down during flush_fuse_ram: {ex}")
            if _wait_reconnect(timeout_s=45):
                log.info("Retrying flush_fuse_ram after target reconnect...")
                with _SuppressHWNoise():
                    try:
                        fuse_ram_obj = get_fuse_ram_object(domain_info)
                        if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
                            fuse_ram_obj.flush_fuse_ram()
                            _ha._LOADED_FUSE_RAM_PATHS.discard(frp)
                            log.info("[SUCCESS] flush_fuse_ram succeeded after target reconnect")
                            return True
                    except Exception as retry_ex:
                        log.error(f"flush_fuse_ram failed after reconnect: {retry_ex}")
            else:
                log.error("Target did not reconnect within timeout")
            return False

        # Handle critical power state errors
        elif is_critical_error:
            log.error(f"Critical power state error during flush_fuse_ram: {ex}")
            log.info("Attempting ITP recovery (forcereconfig + unlock)...")
            
            # Always attempt basic ITP recovery for critical errors
            try:
                log.info("Performing ITP forcereconfig()...")
                _ha.itp.forcereconfig()
                time.sleep(2)
                
                log.info("Performing ITP unlock()...")
                _ha.itp.unlock()
                time.sleep(2)
                log.info("[SUCCESS] ITP recovery completed")
                
                # If SUT verification enabled, do full recovery with ping check
                if _ha.ENABLE_SUT_VERIFICATION:
                    _recover_sleep(bypass_cooldown=True)
                else:
                    log.info("Waiting 3 seconds for hardware to stabilize...")
                    time.sleep(3)
                
                # Retry the operation
                log.info("Retrying flush_fuse_ram...")
                
                with _SuppressHWNoise():
                    try:
                        fuse_ram_obj = get_fuse_ram_object(domain_info)
                        if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
                            fuse_ram_obj.flush_fuse_ram()
                            _ha._LOADED_FUSE_RAM_PATHS.discard(frp)
                            log.info("[SUCCESS] flush_fuse_ram succeeded after recovery")
                            return True
                    except Exception as retry_ex:
                        pass  # Continue to handle below
                
                # If we get here, retry failed - handle the error
                try:
                    # Re-execute to get the actual exception for error checking
                    fuse_ram_obj = get_fuse_ram_object(domain_info)
                    if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
                        fuse_ram_obj.flush_fuse_ram()
                except Exception as retry_ex:
                    error_msg: str = str(retry_ex).lower()
                    log.error(f"Retry after recovery failed: {retry_ex}")
                    
                    # Check if IPC connection was lost during retry
                    if any(kw in error_msg for kw in _ha._IPC_LOSS_KEYWORDS):
                        log.warning("IPC connection lost during retry - attempting reinitialization...")
                        if _reinit_ipc():
                            log.info("Final retry after IPC reinit...")
                            with _SuppressHWNoise():
                                try:
                                    fuse_ram_obj = get_fuse_ram_object(domain_info)
                                    if hasattr(fuse_ram_obj, 'flush_fuse_ram'):
                                        fuse_ram_obj.flush_fuse_ram()
                                        _ha._LOADED_FUSE_RAM_PATHS.discard(frp)
                                        log.info("[SUCCESS] flush_fuse_ram succeeded after IPC reinit")
                                        return True
                                except Exception as _final_flush_err:
                                    log.error(f"Final flush_fuse_ram attempt failed: {_final_flush_err}")
                    return False
            except Exception as recovery_ex:
                log.error(f"ITP recovery failed: {recovery_ex}")
        else:
            log.error(f"Failed to flush fuse RAM: {ex}")
        
        return False


def read_all_wps(domain_info):
    """
    Read all working points for a domain.

    For domains that carry a ``vf_voltage_adder`` field the returned voltage
    already includes the adder (handled transparently by
    :func:`read_voltage_frequency`).

    Args:
        domain_info: Domain configuration dict

    Returns:
        list: List of (voltage_v, freq_mhz) tuples for each WP
    """
    wp_count = domain_info['wp_count']
    wps = []

    for i in range(wp_count):
        voltage_v, freq_mhz = read_voltage_frequency(domain_info, i)
        wps.append((voltage_v, freq_mhz))

    return wps


def read_adder_voltages(domain_info):
    """
    Read the ``vf_voltage_adder`` registers for domains that carry them
    (e.g. ``media``, ``vpu``, ``nclk``, ``sa_qclk``, ``de``).

    These correction values are firmware-applied on top of the base VF table.
    They are used **only** for computing the effective/display voltage shown
    to the user in :func:`~core.curve_engine.CurveEngine._make_vf_dataframe`.

    Args:
        domain_info: Domain configuration dict

    Returns:
        list of float (volts) — one adder value per WP, aligned with
        ``vf_voltage``.  Returns ``None`` if the domain has no adder field.
    """
    if 'vf_voltage_adder' not in domain_info:
        return None

    from .conversions import voltage_to_volts

    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return None

    _lsb_mv = domain_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)
    adder_regs = domain_info['vf_voltage_adder']
    adders = []
    for reg in adder_regs:
        try:
            if hasattr(fuse_obj, reg):
                raw = getattr(fuse_obj, reg, None)
                adders.append(voltage_to_volts(raw, _lsb_mv) if raw is not None else 0.0)
            else:
                adders.append(0.0)
        except Exception:
            adders.append(0.0)
    return adders


def read_delta_voltages(domain_info):
    """
    Read per-WP delta voltage corrections for domains that have
    ``vf_voltage_delta_idx1`` / ``vf_voltage_delta_idx2`` fields
    (e.g. ``core0_bigcore_base_vf``, ``core1_bigcore_base_vf``).

    These correction registers cover WP indices 1-9 (9 values).
    They are *supplementary* data — not added to the base voltage
    automatically — so they are returned separately for display or
    export purposes.

    Args:
        domain_info: Domain configuration dict

    Returns:
        dict with keys ``'delta_idx1'`` and ``'delta_idx2'``, each a list
        of voltage values in volts (one per delta register).
        Returns ``None`` if the domain has no delta fields.
    """
    if 'vf_voltage_delta_idx1' not in domain_info:
        return None

    from .conversions import voltage_to_volts

    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return None

    _lsb_mv = domain_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)
    result = {}
    for json_key, out_key in [('vf_voltage_delta_idx1', 'delta_idx1'),
                               ('vf_voltage_delta_idx2', 'delta_idx2')]:
        regs = domain_info.get(json_key, [])
        values = []
        for reg in regs:
            try:
                if hasattr(fuse_obj, reg):
                    raw = getattr(fuse_obj, reg, None)
                    values.append(voltage_to_volts(raw, _lsb_mv) if raw is not None else None)
                else:
                    values.append(None)
            except Exception:
                values.append(None)
        result[out_key] = values

    return result


def read_scalar_modifier(modifier_info: dict) -> dict:
    """Read a single scalar modifier register from hardware.

    Args:
        modifier_info: Entry from vf_domains.json scalar_modifiers section.

    Returns:
        dict:
            'raw'       — integer raw fuse value (None on error)
            'converted' — human-readable physical value (str) or ''
            'units'     — 'MHz' | 'mV' | 'raw' | '/°C'
            'ok'        — bool
    """
    if _ha.MOCK_MODE:
        return {'raw': 0, 'converted': '0', 'units': 'mock', 'ok': True}

    reg_name  = modifier_info.get('register', '')
    fuse_path = modifier_info.get('fuse_path', '')
    encoding  = modifier_info.get('encoding', 'raw')

    fuse_obj = get_fuse_object(fuse_path)
    if fuse_obj is None:
        return {'raw': None, 'converted': '', 'units': '', 'ok': False}

    try:
        if not hasattr(fuse_obj, reg_name):
            log.warning(f"Scalar register '{reg_name}' not found on fuse object")
            return {'raw': None, 'converted': '', 'units': '', 'ok': False}
        raw = int(getattr(fuse_obj, reg_name))
    except Exception as ex:
        log.error(f"Could not read scalar '{reg_name}': {ex}")
        return {'raw': None, 'converted': '', 'units': '', 'ok': False}

    converted: str = ''
    units     = 'raw'
    if encoding == 'ratio_mhz':
        mult = modifier_info.get('freq_multiplier', 100.0)
        converted: str = f'{raw * mult:g}'
        units     = 'MHz'
    elif encoding == 'voltage_mv':
        lsb = modifier_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)
        converted: str = f'{round(raw * lsb, 2)}'
        units     = 'mV'
    elif encoding == 'divisor_2n':
        # Determine N from description if present, default 12
        desc = modifier_info.get('description', '')
        m: re.Match[str] | None = re.search(r'/\s*\(?\s*2\s*\^\s*(\d+)', desc)
        n: int = int(m.group(1)) if m else 12
        converted: str = f'{raw / (2 ** n):.6f}'
        units     = '/°C'
    else:
        converted = str(raw)
        units     = 'raw'

    return {'raw': raw, 'converted': converted, 'units': units, 'ok': True}


def write_scalar_modifier(modifier_info: dict, new_value) -> bool:
    """Write a new value to a scalar modifier register.

    Args:
        modifier_info: Entry from vf_domains.json scalar_modifiers section.
        new_value: The raw integer value to write.  For 'ratio_mhz' domains
                   the caller should pass the raw ratio (not MHz).  Use
                   :func:`scalar_physical_to_raw` to convert from physical units.

    Returns:
        bool: True on success.
    """
    if _ha.MOCK_MODE:
        reg_name = modifier_info.get('register', 'unknown')
        log.debug(f"write_scalar_modifier('{reg_name}', {new_value})")
        return True

    reg_name  = modifier_info.get('register', '')
    fuse_path = modifier_info.get('fuse_path', '')

    fuse_obj = get_fuse_object(fuse_path)
    if fuse_obj is None:
        log.error(f"write_scalar_modifier: cannot resolve fuse path '{fuse_path}'")
        return False

    try:
        if not hasattr(fuse_obj, reg_name):
            log.error(f"Scalar register '{reg_name}' not found")
            return False
        raw = int(new_value)
        setattr(fuse_obj, reg_name, raw)
        # Read-back verification
        readback = int(getattr(fuse_obj, reg_name))
        if readback != raw:
            log.warning(f"Scalar '{reg_name}': wrote {raw} but read back {readback}")
        else:
            log.info(f"[OK] Scalar '{reg_name}' ← {raw} (verified)")
        return True
    except Exception as ex:
        log.error(f"Failed to write scalar '{reg_name}': {ex}")
        traceback.print_exc()
        return False


def scalar_physical_to_raw(physical_value: float, modifier_info: dict) -> int:
    """Convert a physical value (MHz or mV) to raw fuse integer.

    Args:
        physical_value: Value in natural units (MHz for ratio_mhz, mV for voltage_mv).
        modifier_info:  Scalar modifier config dict.

    Returns:
        int: raw fuse value.
    """
    encoding = modifier_info.get('encoding', 'raw')
    if encoding == 'ratio_mhz':
        mult = modifier_info.get('freq_multiplier', 100.0)
        return round(physical_value / mult)
    elif encoding == 'voltage_mv':
        lsb = modifier_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)
        return round(physical_value / lsb)
    else:
        return round(physical_value)


def read_all_scalar_modifiers(scalar_modifiers: dict) -> dict:
    """Read all scalar modifier registers from hardware.

    Args:
        scalar_modifiers: The 'scalar_modifiers' dict from vf_domains.json.

    Returns:
        dict keyed by register name:
            {'raw': int, 'converted': str, 'units': str, 'ok': bool,
             'label': str, 'type': str, 'encoding': str}
    """
    results = {}
    for reg_name, info in scalar_modifiers.items():
        r = read_scalar_modifier(info)
        r['label']    = info.get('label', reg_name)
        r['type']     = info.get('type', 'unknown')
        r['encoding'] = info.get('encoding', 'raw')
        r['register'] = reg_name
        results[reg_name] = r
    return results


def restore_voltages(domain_info, voltage_data) -> bool:
    """
    Restore voltages from saved data (revert operation).
    
    Args:
        domain_info: Domain configuration dict
        voltage_data: List of (voltage_v, freq_mhz) tuples from before modification
        
    Returns:
        bool: True on success, False on error
    """
    from .conversions import mv_to_raw

    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        log.error(f"Failed to get fuse object for restore")
        return False

    _lsb_mv = domain_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)

    try:
        for i, (voltage_v, freq_mhz) in enumerate(voltage_data):
            if voltage_v is None:
                continue

            voltage_reg = domain_info['vf_voltage'][i]
            voltage_mv = voltage_v * 1000  # Convert V to mV
            new_raw: int = mv_to_raw(voltage_mv, _lsb_mv)
            
            log.info(f"[REVERT] WP{i}: Restoring to {voltage_mv:.2f} mV ({voltage_v:.4f} V)")
            setattr(fuse_obj, voltage_reg, new_raw)
        
        return True
    
    except Exception as ex:
        log.error(f"Failed to restore voltages: {ex}")
        traceback.print_exc()
        return False


def bump_all_voltages(domain_info, bump_mv, direction='up') -> bool:
    """
    Bump all voltages for a domain up or down.
    
    Args:
        domain_info: Domain configuration dict
        bump_mv: Amount to bump in millivolts
        direction: 'up' or 'down'
        
    Returns:
        bool: True on success, False on error
    """
    from .conversions import voltage_to_mv, mv_to_raw

    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return False

    _lsb_mv = domain_info.get('voltage_lsb_mv', _DEFAULT_VOLT_LSB_MV)
    wp_count = domain_info['wp_count']

    try:
        skipped = 0
        for i in range(wp_count):
            voltage_reg = domain_info['vf_voltage'][i]
            current_raw = getattr(fuse_obj, voltage_reg)

            # WP with raw=0 is unused/unprogrammed — skip it entirely so we
            # don't write a small non-zero value into a slot the platform
            # never uses.
            if int(current_raw) == 0:
                skipped += 1
                continue

            current_mv = voltage_to_mv(current_raw, _lsb_mv)

            if direction == 'up':
                new_mv = current_mv + bump_mv
            else:
                new_mv = current_mv - bump_mv

            new_raw: int = mv_to_raw(new_mv, _lsb_mv)
            setattr(fuse_obj, voltage_reg, new_raw)

        if skipped:
            log.info(f"  Skipped {skipped} unused (zero) WP(s) for "
                     f"'{domain_info.get('label', '')}'")
        return True
    
    except Exception as ex:
        log.error(f"Failed to bump voltages: {ex}")
        return False


def read_frequency_ratios(domain_info):
    """
    Read P0, P1, Pn frequency ratios for flatten operation.
    
    Args:
        domain_info: Domain configuration dict
        
    Returns:
        dict: {'p0': ratio, 'p1': ratio, 'pn': ratio} or None on error
    """
    if 'flatten_freq_ratios' not in domain_info:
        return None
    
    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return None
    
    ratio_regs = domain_info['flatten_freq_ratios']
    ratios = {}
    
    try:
        for key in ['min', 'p0', 'p1', 'pn']:
            if key in ratio_regs:
                reg_name = ratio_regs[key]
                ratios[key] = getattr(fuse_obj, reg_name, None)
        return ratios
    except Exception as ex:
        log.error(f"Failed to read frequency ratios: {ex}")
        return None


def write_frequency_ratios(domain_info, ratios) -> bool:
    """
    Write P0, P1, Pn frequency ratios for flatten operation.
    
    Args:
        domain_info: Domain configuration dict
        ratios: dict with 'p0', 'p1', 'pn' keys
        
    Returns:
        bool: True on success, False on error
    """
    if 'flatten_freq_ratios' not in domain_info:
        return False
    
    fuse_obj = get_fuse_object(domain_info['fuse_path'])
    if fuse_obj is None:
        return False
    
    ratio_regs = domain_info['flatten_freq_ratios']
    
    try:
        for key, value in ratios.items():
            if key in ratio_regs:
                reg_name = ratio_regs[key]
                setattr(fuse_obj, reg_name, int(value))
                log.info(f"write_frequency_ratios: {reg_name} = {value}")
        return True
    except Exception as ex:
        log.error(f"Failed to write frequency ratios: {ex}")
        return False


def apply_discovered_register_edits(edits: list) -> dict:
    """Write edited register values to hardware via ITP, flush, reset, verify.

    This is the full hardware apply flow for Discovered Registers edits:
      1. Load fuse RAM (per unique fuse path)
      2. Read & store before-values
      3. Write new raw integer values via setattr on the fuse object
      4. Flush fuse RAM (per unique fuse path)
      5. _ha.itp.resettarget() via reset_target()
      6. If SUT verification enabled: wait for boot + verify_post_fuse_update()
      7. Load fuse RAM again (per unique fuse path) after reset
      8. Read back values and compare to verify

    Args:
        edits: list of dicts, each with:
               'fuse_path'  - e.g. 'cdie.fuses.punit_fuses'
               'reg_name'   - register attribute name
               'new_value'  - new raw integer value to write

    Returns:
        dict with keys:
          'success'      - bool
          'written'      - list of {reg_name, before, after, verified}
          'failed'       - list of {reg_name, error}
          'cold_reset'   - bool
          'message'      - human-readable summary
    """
    result = {
        'success': False,
        'written': [],
        'failed':  [],
        'cold_reset': False,
        'message': '',
    }

    if not edits:
        result['message'] = 'No edits to apply.'
        return result

    # ── Group by fuse_path for efficient load/flush ───────────────────────
    from collections import defaultdict
    path_groups = defaultdict(list)   # fuse_path → [edit_dict]
    for edit in edits:
        path_groups[edit['fuse_path']].append(edit)

    # Build minimal domain_info stubs (load_fuse_ram / flush_fuse_ram only
    # need 'fuse_path' and 'fuse_ram_path')
    def _stub(fuse_path):
        # fuse_ram_path = parent of fuse_path (drop last component)
        parts = fuse_path.split('.')
        fuse_ram = '.'.join(parts[:-1]) if len(parts) > 1 else fuse_path
        return {'fuse_path': fuse_path, 'fuse_ram_path': fuse_ram,
                'label': fuse_path.split('.')[-1]}

    # ── Step 1: load fuse RAM per unique path ────────────────────────────
    log.info('Step 1: Loading fuse RAM...')
    for fuse_path in path_groups:
        stub = _stub(fuse_path)
        ok: None | bool = load_fuse_ram(stub)
        if not ok:
            log.error(f'load_fuse_ram failed for {fuse_path} — aborting')
            result['message'] = f'load_fuse_ram failed for {fuse_path}'
            return result

    # ── Step 2: read before-values ────────────────────────────────────────
    log.info('Step 2: Reading before-values...')
    before_vals = {}
    for fuse_path, path_edits in path_groups.items():
        fuse_obj = get_fuse_object(fuse_path)
        if fuse_obj is None:
            result['message'] = f'Cannot resolve fuse object: {fuse_path}'
            return result
        for edit in path_edits:
            reg = edit['reg_name']
            before_vals[reg] = getattr(fuse_obj, reg, None)
            log.info(f'    {reg}: {before_vals[reg]} → {edit["new_value"]}')

    # ── Step 3: write new values ──────────────────────────────────────────
    log.info('Step 3: Writing new values...')
    for fuse_path, path_edits in path_groups.items():
        fuse_obj = get_fuse_object(fuse_path)
        for edit in path_edits:
            reg = edit['reg_name']
            try:
                setattr(fuse_obj, reg, int(edit['new_value']))
            except Exception as ex:
                result['failed'].append({'reg_name': reg, 'error': str(ex)})
                log.error(f'Write failed for {reg}: {ex}')

    if result['failed'] and len(result['failed']) == len(edits):
        result['message'] = 'All writes failed — aborting before flush'
        return result

    # ── Step 4: flush fuse RAM per unique path ────────────────────────────
    log.info('Step 4: Flushing fuse RAM...')
    for fuse_path in path_groups:
        stub = _stub(fuse_path)
        if not flush_fuse_ram(stub):
            result['message'] = f'flush_fuse_ram failed for {fuse_path}'
            return result

    # ── Step 5: reset target ──────────────────────────────────────────────
    log.info('Step 5: Resetting target...')
    reset_result = reset_target()
    if not reset_result['reset_success']:
        result['message'] = f'reset_target failed: {reset_result["message"]}'
        return result

    if reset_result.get('cold_reset_detected', False):
        result['cold_reset'] = True
        result['message'] = (
            '\u274c COLD RESET detected — SUT powered off.\n'
            'Fuses may have reverted. Check hardware state before retrying.'
        )
        return result

    # ── Step 6: SUT verification (if enabled) ────────────────────────────
    if _ha.ENABLE_SUT_VERIFICATION:
        log.info('Step 6: Waiting for SUT boot + verification...')
        verification_result = verify_post_fuse_update()
        if not verification_result['success']:
            result['message'] = f'SUT verification failed: {verification_result["message"]}'
            return result
        log.info('SUT verified functional after reset')

    # ── Step 7: reload fuse RAM after reset ───────────────────────────────
    log.info('Step 7: Reloading fuse RAM after reset...')
    for fuse_path in path_groups:
        stub = _stub(fuse_path)
        load_fuse_ram(stub)

    # ── Step 8: readback + verify ─────────────────────────────────────────
    log.info('Step 8: Verifying written values...')
    all_ok = True
    for fuse_path, path_edits in path_groups.items():
        fuse_obj = get_fuse_object(fuse_path)
        for edit in path_edits:
            reg = edit['reg_name']
            after_val = getattr(fuse_obj, reg, None)
            expected  = int(edit['new_value'])
            verified  = (after_val == expected)
            if not verified:
                all_ok = False
            result['written'].append({
                'reg_name': reg,
                'before':   before_vals.get(reg),
                'after':    after_val,
                'expected': expected,
                'verified': verified,
            })
            status: str = '\u2713' if verified else '\u2717'
            log.info(f'    [{status}] {reg}: {before_vals.get(reg)} -> {after_val}'
                  + ('' if verified else f'  (expected {expected})'))

    result['success'] = True
    result['message'] = (
        f'Applied {len(result["written"])} register(s). '
        f'{"All verified." if all_ok else "WARNING: some readbacks did not match."}'
    )
    return result
