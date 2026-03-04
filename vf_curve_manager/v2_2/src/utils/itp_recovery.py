"""
itp_recovery.py — ITP connection, SUT boot-wait, power-state and reset
=======================================================================
Physical implementation of all ITP / SUT-health functions.

These functions were migrated from hardware_access.py (which now acts as
a thin coordinator that re-exports everything for backward compatibility).

State (itp, ipc, MOCK_MODE, …) lives in utils.hardware_access as module-level
variables.  Functions here access that state via the _ha module reference so
that any runtime changes made by init_hardware() are always visible.
"""
# ── Circular-import note ─────────────────────────────────────────────────────
# hardware_access.py imports from THIS module at the bottom (after defining all
# its globals), so the partial-module reference _ha received here is already
# fully constructed by the time any function is actually called.
import logging
import utils.hardware_access as _ha     # noqa: E402

import time
import os
import platform
import subprocess
from datetime import datetime
from utils._boot_stats import record_boot_time, get_adaptive_boot_timeout   # noqa: F401

log = logging.getLogger(__name__)



def init_hardware(ipc_instance, itp_instance, enable_sut_check=True, namespace=None, mock_mode=False) -> None:
    """
    Initialize hardware references from main script.

    Args:
        ipc_instance:    Initialized ipccli.baseaccess() instance  (None in mock mode)
        itp_instance:    Initialized itpii.baseaccess() instance   (None in mock mode)
        enable_sut_check: Enable SUT boot verification and recovery (default: True)
        namespace:       Dict of ITP root objects (pass globals() from the launcher
                         so fuse path resolution does not depend on __main__).
                         Falls back to vars(__main__) if None — backward compatible.
        mock_mode:       When True, reads from vf_discovery_cache.json and all writes
                         are logged but NOT applied to hardware.  ITP is not used.
    """

    _ha.MOCK_MODE = mock_mode

    if mock_mode:
        from utils.mock_backend import load_mock_registers, MockFuseObject
        _registers = load_mock_registers()
        _ha._mock_root = MockFuseObject(_registers)
        _ha.ENABLE_SUT_VERIFICATION = False   # SUT checks have no meaning in mock mode
        log.info("MOCK MODE ACTIVE — hardware reads use cache; writes are no-ops")
        return

    _ha.ipc = ipc_instance
    _ha.itp = itp_instance
    _ha.ENABLE_SUT_VERIFICATION= enable_sut_check

    if namespace is not None:
        _ha._itp_namespace = namespace
        log.info(f"ITP namespace injected: {len(namespace)} symbols available")
    else:
        # Backward-compatible fallback
        import __main__
        _ha._itp_namespace = vars(__main__)
        log.info("ITP namespace: using __main__ globals (pass namespace=globals() to the caller for robustness)")

    if enable_sut_check:
        log.info("SUT verification enabled — will check boot status and reachability")
    else:
        log.info("SUT verification disabled — operating in fast mode (--no-sut-check)")


def _do_itp_reconnect_sequence(label: str = "") -> bool:
    """
    Perform the full ITP link-recovery sequence:
        reconnect  → re-establishes the DCI/JTAG physical link
        refresh    → rebuilds the namednodes tree
        forcereconfig → re-applies tap/hardware configuration
        unlock     → unlocks tap access

    Each step is attempted separately so a missing method never
    blocks the rest of the sequence.

    Args:
        label: Optional context string for log messages.

    Returns:
        bool: True if unlock succeeded (minimum viable recovery).
    """
    prefix: str = f"[ITP-RECOVER{' ' + label if label else ''}]"
    ok = False
    try:
        if hasattr(_ha.itp, 'reconnect'):
            log.info(f"{prefix} _ha.itp.reconnect()...")
            _ha.itp.reconnect()
            time.sleep(1)
        else:
            log.info(f"{prefix} _ha.itp.reconnect() not available — skipping")

        if hasattr(_ha.itp, 'refresh'):
            log.info(f"{prefix} _ha.itp.refresh()...")
            _ha.itp.refresh()
            time.sleep(1)
        else:
            log.info(f"{prefix} _ha.itp.refresh() not available — skipping")

        if hasattr(_ha.itp, 'forcereconfig'):
            log.info(f"{prefix} _ha.itp.forcereconfig()...")
            _ha.itp.forcereconfig()
            time.sleep(2)
        else:
            log.info(f"{prefix} _ha.itp.forcereconfig() not available — skipping")

        log.info(f"{prefix} _ha.itp.unlock()...")
        _ha.itp.unlock()
        time.sleep(1)
        log.info(f"{prefix} sequence complete")
        ok = True
    except Exception as _seq_ex:
        log.info(f"{prefix} sequence failed: {_seq_ex}")
    return ok


def _wait_for_target_reconnect(timeout_s: int = 45) -> bool:
    """Poll until the ITP/hardware link is re-established after a power cycle.

    Called when error code 0x8000000f (target powered down) is detected.
    Repeatedly calls _do_itp_reconnect_sequence() until it succeeds or times out.

    Args:
        timeout_s: Maximum seconds to wait before giving up.

    Returns:
        bool: True if reconnected successfully, False on timeout.
    """
    log.info(f"Target appears powered down — waiting for reconnect (up to {timeout_s}s)...")
    deadline: float = time.time() + timeout_s
    poll_no = 0
    while time.time() < deadline:
        poll_no += 1
        try:
            _do_itp_reconnect_sequence(label="reconnect-poll")
            log.info(f"Target reconnected after {poll_no} poll(s)")
            return True
        except Exception:
            remaining = int(deadline - time.time())
            if remaining > 0:
                wait: int = min(5, remaining)
                log.info(f"Target not ready yet — {remaining}s remaining, retrying in {wait}s...")
                time.sleep(wait)
    log.warning("Target reconnect timed out — hardware may still be powering up")
    return False


def reinitialize_ipc_itp() -> bool:
    """
    Reinitialize IPC and ITP connections when connection is lost.
    This is needed when OpenIPC crashes or loses connection.
    
    Returns:
        bool: True if reinitialization succeeded, False otherwise
    """

    try:
        log.info("[CRITICAL] IPC/ITP connection lost - attempting reinitialization...")

        # Clear existing references before re-creating
        _ha.itp = None
        _ha.ipc = None
        
        # Reinitialize IPC
        # Use local imports to avoid the module-level names being shadowed
        # by `from pysvtools.pmext.services.regs import *`
        log.info("Reinitializing IPC connection...")
        try:
            import ipccli as _ipccli_mod
        except ImportError:
            log.error("ipccli not importable — cannot reinitialize")
            return False
        _ha.ipc = _ipccli_mod.baseaccess()
        time.sleep(1)

        # Reinitialize ITP
        log.info("Reinitializing ITP connection...")
        try:
            import itpii as _itpii_mod
        except ImportError:
            log.error("itpii not importable — cannot reinitialize")
            return False
        _ha.itp = _itpii_mod.baseaccess(True)
        time.sleep(1)
        
        # Unlock
        log.info("Unlocking ITP...")
        _ha.itp.unlock()
        time.sleep(1)
        
        log.info("[SUCCESS] IPC/ITP reinitialization completed successfully")
        return True
        
    except Exception as ex:
        log.error(f"Failed to reinitialize IPC/ITP: {ex}")
        return False


def _get_sut_ip():
    """Get SUT IP from ITP communicator configuration."""
    try:
        # Try CommunicatorConfig first
        try:
            from evtar.services.communicator.config._ux import CommunicatorConfig
            CommunicatorConfig.Reload()
            target_ip = CommunicatorConfig.Target.DefaultPeer2PeerIP
            if target_ip:
                log.info(f"[NETWORK] Found SUT IP from CommunicatorConfig: {target_ip}")
                return target_ip
        except (ImportError, Exception):
            pass
        
        # Try itpii module attributes
        if hasattr(_ha.itp, 'communicator') and hasattr(_ha.itp.communicator, 'target_ip'):
            return _ha.itp.communicator.target_ip
        elif hasattr(_ha.itp, 'target_ip'):
            return _ha.itp.target_ip
        elif hasattr(_ha.itp, 'get_target_ip'):
            return _ha.itp.get_target_ip()
        
        # Check environment variables
        sut_ip: str | None = os.environ.get('SUT_IP') or os.environ.get('TARGET_IP') or os.environ.get('ITP_TARGET')
        if sut_ip:
            log.info(f"[NETWORK] Found SUT IP from environment: {sut_ip}")
            return sut_ip
        
        log.info("Unable to detect SUT IP - will skip ping check")
        return None
        
    except Exception as e:
        log.warning(f"Error getting SUT IP: {e}")
        return None


def _ping_sut(ip, timeout_seconds=2) -> bool:
    """Ping SUT to check if it's reachable."""
    try:
        if platform.system().lower() == "windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout_seconds * 1000), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout_seconds), ip]
        
        result: subprocess.CompletedProcess[str] = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 1)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def check_itp_connection():
    """Check if ITP connection is alive using generic ITP methods."""
    try:
        if hasattr(_ha.itp, 'isrunning'):
            return _ha.itp.isrunning()
        elif hasattr(_ha.itp, 'threads') and len(_ha.itp.threads) > 0:
            return True
        else:
            return _ha.itp is not None
    except Exception as ex:
        log.warning(f"ITP connection check failed: {ex}")
        return False


def wait_for_sut_boot(timeout_seconds=300, check_interval=2, min_boot_time=15):
    """
    Wait for SUT to complete boot after reset using ping only (generic across all projects).
    
    Args:
        timeout_seconds: Maximum time to wait for boot (default 300s / 5 minutes)
        check_interval: Time between checks in seconds (default 2s for faster cold reset detection)
        min_boot_time: Minimum time to wait before considering boot complete (default 15s)
        
    Returns:
        bool: True if SUT booted successfully, False on timeout or cold reset detected
    """

    if not _ha.ENABLE_SUT_VERIFICATION:
        log.info("SUT verification disabled - skipping boot wait")
        return True
    
    log.info(f"Waiting for SUT to boot (timeout: {timeout_seconds}s)...")
    log.info(f"Monitoring for cold reset every {check_interval}s...")
    start_time: float = time.time()
    
    # Get SUT IP for ping checks
    sut_ip = _get_sut_ip()
    
    if not sut_ip:
        log.warning("No SUT IP detected - skipping reachability check")
        time.sleep(10)  # Give some time for potential boot
        return True
    
    # During the initial period, frequently check for cold reset
    log.info(f"Initial {min_boot_time}s monitoring period (checking for cold reset)...")
    
    check_start: float = time.time()
    sut_went_offline = False
    check_count = 0
    
    while (time.time() - check_start) < min_boot_time:
        check_count += 1
        
        # Check for cold reset indicators
        try:
            power_check = check_power_state()
            
            if power_check.get('cold_reset_indicator', False):
                elapsed = int(time.time() - start_time)
                timestamp: str = datetime.now().isoformat()
                log.warning(f"⚠️  COLD RESET DETECTED at {elapsed}s [{timestamp}] ⚠️")
                log.warning(f"State: {power_check['state']}")
                log.warning(f"System powered off - voltage/frequency exceeded stability limits")
                return False
            
            if not power_check['powered_on']:
                elapsed = int(time.time() - start_time)
                timestamp: str = datetime.now().isoformat()
                log.warning(f"⚠️  Target powered off at {elapsed}s [{timestamp}] - cold reset likely ⚠️")
                log.warning(f"State: {power_check['state']}")
                return False
        except Exception as ex:
            error_str: str = str(ex).lower()
            if any(keyword in error_str for keyword in _ha._COLD_RESET_KEYWORDS):
                elapsed = int(time.time() - start_time)
                log.warning(f"⚠️ Cold reset indicator at {elapsed}s: {str(ex)[:200]}")
                return False
        
        # Check if SUT went offline
        if not _ping_sut(sut_ip, timeout_seconds=1):
            if not sut_went_offline:
                sut_went_offline = True
                log.info(f"Confirmed SUT went offline - reset in progress")
        
        time.sleep(1)  # Check every 1 second during initial period
    
    if not sut_went_offline:
        log.warning(f"SUT never went offline during initial {min_boot_time}s - network stayed up")
    
    log.info(f"Initial monitoring complete ({check_count} checks), waiting for boot...")
    
    while (time.time() - start_time) < timeout_seconds:
        elapsed = int(time.time() - start_time)
        remaining: int = timeout_seconds - elapsed
        
        # ENHANCED: More frequent cold reset monitoring during critical boot period (first 90s)
        # Cold reset can happen anytime 30-90s after reset when bad voltages take effect
        is_critical_period: bool = elapsed < 90
        if is_critical_period and check_count % 5 == 0:  # Log every 5 checks during critical period
            log.info(f"Intensive monitoring: {elapsed}s elapsed (critical period: 0-90s)")
        
        # Monitor for cold reset during boot wait - check every cycle
        try:
            power_check = check_power_state()
            
            # If we detect cold reset indicator (SLP_S5, power loss, etc.)
            if power_check.get('cold_reset_indicator', False):
                timestamp: str = datetime.now().isoformat()
                log.warning(f"⚠️  COLD RESET DETECTED during boot wait at {elapsed}s [{timestamp}] ⚠️")
                log.warning(f"State: {power_check['state']}")
                log.warning(f"Target powered off completely (SLP_S5 or power lost)")
                log.warning(f"Hardware fuses automatically reverted to programmed defaults")
                # Return False to trigger cold reset detection in reset_target()
                return False
            
            # If target is powered off (even without explicit cold reset indicator)
            # This catches cases where CPU powers off mid-boot
            if not power_check['powered_on'] and elapsed >= 10:
                timestamp: str = datetime.now().isoformat()
                log.warning(f"⚠️  Target powered off detected at {elapsed}s [{timestamp}] - likely cold reset ⚠️")
                log.warning(f"State: {power_check['state']}")
                return False
                
        except Exception as ex:
            # Check if error message contains cold reset indicators
            error_str: str = str(ex).lower()
            if any(keyword in error_str for keyword in _ha._COLD_RESET_KEYWORDS):
                elapsed = int(time.time() - start_time)
                timestamp: str = datetime.now().isoformat()
                log.warning(f"⚠️  Cold reset indicator in exception at {elapsed}s [{timestamp}]: {str(ex)[:200]}")
                return False
        
        check_count += 1
        
        # Check SUT reachability via ping AND ITP access
        ping_success: bool = _ping_sut(sut_ip, timeout_seconds=2)
        itp_accessible = False
        
        if ping_success:
            # Ping succeeded, now verify ITP can actually access the target
            try:
                if hasattr(_ha.itp, 'threads') and len(_ha.itp.threads) > 0:
                    # Try to access a thread to verify CPU is actually accessible
                    _ = _ha.itp.threads[0]
                    itp_accessible = True
            except Exception as _itp_ex:
                # ITP can't access threads yet, SUT not fully booted
                log.debug(f"ITP thread access failed (SUT may still be booting): {_itp_ex}")
                itp_accessible = False
        
        if ping_success and itp_accessible:
            # Skip progress callback to avoid deadlock - just print
            log.info(f"[SUCCESS] SUT {sut_ip} is reachable and ITP accessible after {elapsed}s")
            record_boot_time(float(elapsed))
            # Give a bit more time for ITP stack to be ready
            time.sleep(3)
            return True
        else:
            # Skip progress callback to avoid deadlock - just print
            if ping_success and not itp_accessible:
                log.info(f"   ⏸ SUT reachable but ITP not accessible yet, waiting... ({remaining}s remaining)")
            else:
                log.info(f"   ⏸ SUT not reachable, waiting... ({remaining}s remaining)")
        
        time.sleep(check_interval)
    
    log.error(f"SUT boot timeout after {timeout_seconds}s")
    return False


def recover_from_deep_sleep(bypass_cooldown=False) -> bool:
    """
    Perform ITP recovery when SUT is unreachable.
    Generic implementation using only ping - no project-specific register paths.
    
    Args:
        bypass_cooldown: If True, skip the cooldown check (for critical errors)
    
    Returns:
        bool: True on success, False on failure or cooldown skip
    """

    if not _ha.ENABLE_SUT_VERIFICATION:
        return True  # Skip recovery if verification disabled
    
    current_time: float = time.time()
    
    # Check cooldown unless explicitly bypassed
    if not bypass_cooldown and (_ha._recovery_in_progress or (current_time - _ha._last_recovery_time) < _ha._recovery_cooldown):
        time_since_last: float = current_time - _ha._last_recovery_time
        if time_since_last < _ha._recovery_cooldown:
            log.info(f"   [SKIP] Recovery cooldown active ({_ha._recovery_cooldown - time_since_last:.1f}s remaining)")
        return False
    
    _ha._recovery_in_progress = True
    _ha._last_recovery_time= current_time
    
    try:
        log.info("Performing ITP recovery...")
        
        # Step 1: Check SUT reachability via ping
        sut_ip = _get_sut_ip()
        if sut_ip:
            log.info(f"[NETWORK] Checking SUT reachability at {sut_ip}...")
            
            if not _ping_sut(sut_ip):
                log.warning(f"SUT {sut_ip} not reachable - waiting...")
                # Wait up to 5 minutes for SUT to become reachable
                if not wait_for_sut_boot(timeout_seconds=300):
                    log.error("SUT did not become reachable")
                    _ha._recovery_in_progress = False
                    return False
            else:
                log.info(f"SUT {sut_ip} is reachable - skipping wait")
        else:
            log.warning("No SUT IP detected - attempting ITP reinit anyway")
        
        # Step 2: If SUT is reachable and ITP is still connected, skip reinit
        if sut_ip and check_itp_connection():
            log.info("SUT reachable and ITP still connected — skipping ITP reinit")
            _ha._recovery_in_progress = False
            return True

        # Step 3: Reinitialize ITP (use local import to avoid module-level name
        # being shadowed to None by `from pysvtools.pmext.services.regs import *`)
        log.info("Reinitializing ITP connection...")
        try:
            import itpii as _itpii_local
        except ImportError:
            log.error("itpii not importable — cannot reinitialize")
            _ha._recovery_in_progress = False
            return False

        _ha.itp = _itpii_local.baseaccess(True)
        time.sleep(1)

        _do_itp_reconnect_sequence(label="recover_from_deep_sleep")

        log.info(f"[SUCCESS] ITP recovery completed")
        
        _ha._recovery_in_progress = False
        return True
    
    except Exception as e:
        log.error(f"ITP recovery failed: {e}")
        _ha._recovery_in_progress = False
        return False


def verify_post_fuse_update():
    """
    Verify SUT is functional after fuse update using generic ITP methods.
    
    Checks if the SUT is still bootable and responsive after fuse modifications.
    This is critical to ensure the voltage/frequency changes didn't brick the system.
    
    Returns:
        dict: {
            'success': bool,
            'message': str,
            'responsive': bool,
            'boot_time': float (seconds)
        }
    """
    # Skip verification when SUT checks are disabled OR in mock mode (no live ITP).
    if not _ha.ENABLE_SUT_VERIFICATION or _ha.MOCK_MODE:
        return {
            'success': True,
            'message': 'SUT verification disabled (mock mode or --no-sut-check)',
            'responsive': True,
            'boot_time': 0.0,
        }
    try:
        log.info("Verifying SUT functionality after fuse update...")
        start_time: float = time.time()
        
        # Step 1: Check basic ITP connectivity
        if not check_itp_connection():
            return {
                'success': False,
                'message': 'ITP connection lost after fuse update',
                'responsive': False,
                'boot_time': 0
            }
        
        # Step 2: Try a simple ITP operation to verify responsiveness
        responsive = True
        try:
            # Use generic ITP method that works across all projects
            if hasattr(_ha.itp, 'threads') and len(_ha.itp.threads) > 0:
                # Just accessing threads is enough to verify basic connectivity
                _ = _ha.itp.threads[0]
        except Exception as _thread_ex:
            log.debug(f"ITP thread access failed during verification: {_thread_ex}")
            responsive = False
        
        # Step 3: If not responsive, attempt recovery
        if not responsive:
            log.warning("SUT not immediately responsive, attempting recovery...")
            recovery_success: bool = recover_from_deep_sleep()
            
            if not recovery_success:
                return {
                    'success': False,
                    'message': 'SUT failed to recover after fuse update',
                    'responsive': False,
                    'boot_time': time.time() - start_time
                }
        
        boot_time: float = time.time() - start_time
        
        log.info(f"[SUCCESS] SUT verified functional after fuse update ({boot_time:.1f}s)")
        
        return {
            'success': True,
            'message': 'SUT is functional and responsive',
            'responsive': True,
            'boot_time': boot_time
        }
        
    except Exception as ex:
        return {
            'success': False,
            'message': f'Verification failed with exception: {ex}',
            'responsive': False,
            'boot_time': 0
        }


def check_power_state():
    """
    Check if SUT is powered on or in a powered-off state.
    Detects cold reset indicators like SLP_S5, CPU power off, etc.
    
    Returns:
        dict: {
            'powered_on': bool,
            'state': str (description of power state),
            'can_access_fuses': bool,
            'cold_reset_indicator': bool (True if SLP_S5 or power loss detected)
        }
    """
    try:
        # Try to check if target is running
        if hasattr(_ha.itp, 'isrunning'):
            is_running = _ha.itp.isrunning()
            if not is_running:
                return {
                    'powered_on': False,
                    'state': 'Target not running (powered off or deep sleep)',
                    'can_access_fuses': False,
                    'cold_reset_indicator': True
                }
        
        # Try to access a common register to check if we can read from target
        # This will fail if target is powered off or in SLP_S5
        try:
            if hasattr(_ha.itp, 'threads') and len(_ha.itp.threads) > 0:
                # Try to access first thread (this will fail if powered off)
                _ = _ha.itp.threads[0]
                
                return {
                    'powered_on': True,
                    'state': 'Target powered on and accessible',
                    'can_access_fuses': True,
                    'cold_reset_indicator': False
                }
        except Exception as thread_ex:
            error_str: str = str(thread_ex).lower()
            
            is_cold_reset: bool = any(keyword in error_str for keyword in _ha._COLD_RESET_KEYWORDS)
            
            return {
                'powered_on': False,
                'state': f'Target appears powered off: {str(thread_ex)[:150]}',
                'can_access_fuses': False,
                'cold_reset_indicator': is_cold_reset
            }
        
        # If we can't determine state, assume it's on
        return {
            'powered_on': True,
            'state': 'Target state unknown (assuming powered on)',
            'can_access_fuses': True,
            'cold_reset_indicator': False
        }
        
    except Exception as ex:
        log.warning(f"Error checking power state: {ex}")
        error_str: str = str(ex).lower()
        is_cold_reset: bool = any(keyword in error_str for keyword in _ha._COLD_RESET_KEYWORDS)
        
        return {
            'powered_on': False,
            'state': f'Error checking power state: {ex}',
            'can_access_fuses': False,
            'cold_reset_indicator': is_cold_reset
        }


def detect_cold_reset(wait_time=5):
    """
    Detect if the system experienced a cold reset (power cycle) vs normal warm reset.
    
    Cold reset indicators:
    - "Device Gone (Target Power Lost or Cable Unplugged)"
    - "PowerDomain : CPU : Off"
    - "System is in sleep state: SLP_S5"
    - ITP connection lost/reconnecting
    
    Args:
        wait_time: Seconds to wait and observe for cold reset indicators (default 5)
    
    Returns:
        dict: {
            'is_cold_reset': bool,
            'indicators': list of str (reasons detected),
            'confidence': str ('high', 'medium', 'low'),
            'timestamp': str (ISO format timestamp)
        }
    """
    indicators = []
    detection_time: str = datetime.now().isoformat()
    
    try:
        # Wait a bit for ITP events to propagate
        log.info(f"Monitoring for cold reset indicators ({wait_time}s)...")
        time.sleep(wait_time)
        
        # Check 1: Try to access threads (will fail if CPU powered off)
        try:
            if hasattr(_ha.itp, 'threads') and len(_ha.itp.threads) > 0:
                _ = _ha.itp.threads[0]
                log.info("Thread access successful - no cold reset detected")
        except Exception as ex:
            error_str: str = str(ex).lower()
            # Look for specific cold reset indicators from ITP messages
            if any(keyword in error_str for keyword in _ha._COLD_RESET_KEYWORDS):
                indicators.append(f"CPU powered off or in S5 state")
                log.error(f"[COLD RESET] [{detection_time}] Detected power-off indicator in error: {str(ex)[:200]}")
            elif 'not running' in error_str or 'no response' in error_str:
                indicators.append(f"Target not responding")
                log.info(f"[COLD RESET] [{detection_time}] Target not responding: {str(ex)[:150]}")
            else:
                log.info(f"Thread access failed but not power-related: {str(ex)[:150]}")
        
        # Check 2: Verify ITP connection stability
        if not check_itp_connection():
            indicators.append("ITP connection unstable or lost")
            log.info(f"[COLD RESET] [{detection_time}] ITP connection unstable")
        
        # Check 3: Check power state
        power_state = check_power_state()
        if not power_state['powered_on']:
            indicators.append(f"Target powered off: {power_state['state']}")
            log.info(f"[COLD RESET] [{detection_time}] {power_state['state']}")
        
        # Determine if this looks like a cold reset
        is_cold_reset: bool = len(indicators) > 0
        
        if len(indicators) >= 2:
            confidence = 'high'
        elif len(indicators) == 1:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        if is_cold_reset:
            log.warning(f"⚠️  COLD RESET DETECTED [{detection_time}] ⚠️")
            log.warning(f"Confidence: {confidence}")
            log.warning(f"Indicators ({len(indicators)}): {indicators}")
            log.warning(f"This indicates voltage/frequency settings exceeded hardware stability limits")
        else:
            log.info(f"✓ No cold reset detected - normal warm reset [{detection_time}]")
        
        return {
            'is_cold_reset': is_cold_reset,
            'indicators': indicators,
            'confidence': confidence,
            'timestamp': detection_time
        }
        
    except Exception as ex:
        log.error(f"[{detection_time}] Error during cold reset detection: {ex}")
        return {
            'is_cold_reset': True,  # Assume cold reset on error
            'indicators': [f'Error during detection: {ex}'],
            'confidence': 'medium',
            'timestamp': detection_time
        }


def reset_target(wait_for_boot=None, boot_timeout=None):
    """
    Reset the target system and optionally wait for boot.
    Detects if target experiences cold reset (power off) vs normal reset.
    
    Args:
        wait_for_boot: Whether to wait for SUT to boot after reset 
                      (default: None = uses _ha.ENABLE_SUT_VERIFICATION setting)
        boot_timeout: Maximum time to wait for boot in seconds
                      (default: None = uses learned P90 + 60 s margin)
    
    Returns:
        dict: {
            'reset_success': bool,
            'boot_success': bool (if verification enabled),
            'boot_time': float (if verification enabled),
            'cold_reset_detected': bool (if target powered off),
            'cold_reset_details': dict (details if cold reset detected),
            'message': str
        }
    """
    # ── Mock mode: simulate a clean warm reset ─────────────────────────────────────
    if _ha.MOCK_MODE:
        log.debug("reset_target() — simulating warm reset (no-op)")
        return {
            'reset_success': True,
            'boot_success': True,
            'boot_time': 0.0,
            'cold_reset_detected': False,
            'cold_reset_details': None,
            'message': 'Mock reset successful',
        }

    try:
        # Use global setting if not specified
        if wait_for_boot is None:
            wait_for_boot: bool = _ha.ENABLE_SUT_VERIFICATION

        # Resolve adaptive timeout on first use so callers can still pass an
        # explicit override (e.g., wait_for_sut_boot(timeout_seconds=600)).
        if boot_timeout is None:
            boot_timeout: int = get_adaptive_boot_timeout(default=300)
            log.info(f"[BOOT TIMEOUT] Using adaptive timeout: {boot_timeout}s")
        log.debug(f"wait_for_boot={wait_for_boot}, _ha.ENABLE_SUT_VERIFICATION={_ha.ENABLE_SUT_VERIFICATION}")
        
        # Check power state BEFORE reset
        pre_reset_state = check_power_state()
        log.info(f"Pre-reset power state: {pre_reset_state['state']}")
        
        # If verification disabled, just do simple reset
        if not wait_for_boot:
            log.info("Resetting target...")
            _ha.itp.resettarget()
            _ha._LOADED_FUSE_RAM_PATHS.clear()  # fuse-RAM object state invalidated by reset
            time.sleep(2)  # Give time for reset to take effect
            
            # Quick check if target came back up
            post_reset_state = check_power_state()
            
            return {
                'reset_success': True,
                'cold_reset_detected': not post_reset_state['powered_on'],
                'cold_reset_details': post_reset_state if not post_reset_state['powered_on'] else None,
                'message': 'Target reset successful'
            }
        
        # Full verification mode
        log.info("Initiating target reset with verification...")
        
        # Skip progress callback on first call to avoid deadlock
        # The callback tries to call QApplication.processEvents() which can deadlock
        # when called from a worker thread on first invocation
        log.info("Skipping progress callback to avoid UI deadlock")
        
        log.info("Unlocking ITP...")
        
        # Ensure ITP is unlocked before reset (prevents hanging)
        try:
            if hasattr(_ha.itp, 'unlock'):
                _ha.itp.unlock()
                log.info("ITP unlocked")
        except Exception as unlock_ex:
            log.warning(f"ITP unlock failed: {unlock_ex}")
        
        # Perform the reset with timeout protection
        try:
            log.info("Sending reset command to target...")
            _ha.itp.resettarget()
            _ha._LOADED_FUSE_RAM_PATHS.clear()  # fuse-RAM object state invalidated by reset
            log.info("Reset command sent successfully")
        except Exception as reset_ex:
            log.error(f"Failed to send reset command: {reset_ex}")
            return {
                'reset_success': False,
                'boot_success': False,
                'cold_reset_detected': False,
                'message': f'Reset command failed: {reset_ex}'
            }
        
        # Wait a moment for reset to take effect
        time.sleep(3)
        
        # Skip progress callback to avoid deadlock
        log.info("Waiting for SUT boot...")
        
        # Wait for SUT to boot
        boot_success = wait_for_sut_boot(timeout_seconds=boot_timeout)
        
        # After boot completes (or times out), check if it was actually a cold reset
        if not boot_success:
            log.warning("Boot failed or timed out - checking if cold reset occurred...")
            cold_reset_info = detect_cold_reset(wait_time=5)
            
            if cold_reset_info['is_cold_reset']:
                log.warning(f"⚠️ COLD RESET DETECTED! Confidence: {cold_reset_info['confidence']}")
                log.warning(f"Target powered off completely (SLP_S5 or power lost)")
                log.warning(f"Indicators: {', '.join(cold_reset_info['indicators'])}")
                log.info(f"Hardware will automatically restore original fuse values")
                
                return {
                    'reset_success': True,
                    'boot_success': False,
                    'cold_reset_detected': True,
                    'cold_reset_details': cold_reset_info,
                    'message': f"COLD RESET: Target powered off completely. Indicators: {', '.join(cold_reset_info['indicators'])}"
                }
        
        if boot_success:
            # Perform post-boot verification
            verification = verify_post_fuse_update()
            
            return {
                'reset_success': True,
                'boot_success': True,
                'boot_time': verification['boot_time'],
                'cold_reset_detected': False,
                'verification': verification,
                'message': f"Target reset and boot successful ({verification['boot_time']:.1f}s)"
            }
        else:
            return {
                'reset_success': True,
                'boot_success': False,
                'boot_time': boot_timeout,
                'cold_reset_detected': False,
                'message': f'Target reset successful but boot timeout after {boot_timeout}s'
            }
            
    except Exception as ex:
        log.error(f"Failed to reset target: {ex}")
        return {
            'reset_success': False,
            'boot_success': False,
            'cold_reset_detected': False,
            'message': f'Reset failed: {ex}'
        }
