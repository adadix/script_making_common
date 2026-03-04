"""
VF Curve Manager Tool v2.2 - Main Launcher

Modular architecture with clean separation of concerns:
- utils: Conversions, hardware access, data export
- core: Configuration loading, curve operations engine
- ui: PyQt5 dashboard interface

Professional interface matching VF Monitor Tool v2.2 theme.
"""

import sys
import os
import logging
import subprocess
import traceback
import time

from utils.process_utils import terminate_openipc

# NOTE: terminate_openipc() is NOT called here on clean startup.
# OpenIPC is started by the Intel toolchain during module imports below.
# Killing it before connecting leaves nothing to connect to.
# It is called automatically as a recovery step if the initial connection fails.

# Structured logging (file + console)
try:
    from utils.log_setup import setup_logging
    setup_logging()
except Exception:
    pass  # Non-fatal — logging is best-effort in the GUI

log = logging.getLogger(__name__)

# Now import ITP modules
try:
    from pysvtools.pmext.services.regs import *
except Exception as _regs_err:
    # Non-fatal: this wildcard import populates convenience register aliases
    # but is not required for core fuse read/write operations.
    # It fails on some platforms (e.g. Novalake/PantherCove) with a TypeError
    # when the platform core topology doesn't match what regs.py expects.
    log.warning("pysvtools.pmext.services.regs import skipped: %s", _regs_err)
import namednodes
import itpii
import ipccli

# Initialize ITP at module level
log.info("\n%s\n  Intel\u00ae CVE VF Curve Manager Tool v2.2\n  Professional Dashboard - Modular Architecture\n%s", '=' * 80, '=' * 80)

log.info("Initializing ITP connection...")
_itp_connected = False

# ── First attempt: connect to the running OpenIPC ──────────────────────
try:
    ipc = ipccli.baseaccess()
    itp = itpii.baseaccess(True)
    itp.unlock()
    log.info("ITP initialized successfully")
    _itp_connected = True
except Exception as _itp_ex:
    log.warning("Initial ITP connect failed: %s", _itp_ex)
    log.info("Terminating stale OpenIPC and waiting for restart...")

# ── Recovery: terminate stale process, then retry with backoff ─────────
if not _itp_connected:
    terminate_openipc()   # kill the broken instance
    _ITP_MAX_ATTEMPTS = 6
    _ITP_RETRY_DELAY  = 5
    for _attempt in range(1, _ITP_MAX_ATTEMPTS + 1):
        time.sleep(_ITP_RETRY_DELAY)
        try:
            ipc = ipccli.baseaccess()
            itp = itpii.baseaccess(True)
            itp.unlock()
            log.info("ITP initialized successfully (recovery attempt %d)", _attempt)
            _itp_connected = True
            break
        except Exception as _itp_retry_ex:
            if _attempt < _ITP_MAX_ATTEMPTS:
                log.info("Recovery attempt %d/%d failed — retrying in %ds...",
                         _attempt, _ITP_MAX_ATTEMPTS, _ITP_RETRY_DELAY)
            else:
                log.error("ITP initialization failed after %d recovery attempts: %s",
                          _ITP_MAX_ATTEMPTS, _itp_retry_ex)
                log.error("Check that the target is connected and OpenIPC is running.")
                sys.exit(1)

# ── Startup refresh dialog + autonomous discovery ────────────────────────
# QApplication must be created before any widget/dialog, including the
# discovery progress (logged to console) that runs below.
from PyQt5.QtWidgets import QApplication

_app = QApplication(sys.argv)

# Run discovery only when the platform has changed or vf_domains.json is empty.
# maybe_run_discovery(force=False) already detects a platform mismatch and
# triggers a full re-scan automatically when needed, without blocking startup
# with a costly full fuse scan on every launch.
from discovery.startup_discovery import maybe_run_discovery
maybe_run_discovery(force=False)
# ─────────────────────────────────────────────────────────────────────────

# Import modular components AFTER namednodes to ensure ITP objects are available
try:
    from utils import hardware_access
    from core.config_loader import ConfigLoader
    from core.curve_engine import CurveEngine
    from ui.curve_manager_ui import CurveManagerUI
    
    # Initialize hardware access module; pass globals() so get_fuse_object
    # resolves ITP root objects (cdie, soc, etc.) from this module's namespace
    # rather than relying on __main__, making it test-friendly.
    hardware_access.init_hardware(ipc, itp, namespace=globals())
    
except ImportError as e:
    log.critical("Failed to import modular components: %s\n"
                 "Please ensure:\n"
                 "  1. All source files are present in src/core, src/ui, and src/utils\n"
                 "  2. PyQt5 is installed: pip install PyQt5\n"
                 "  3. Required packages are installed: pip install -r requirements.txt",
                 e)
    sys.exit(1)


def main():
    """Main entry point for VF Curve Manager Tool."""
    log.info("[1] Loading hardware configuration...")
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'vf_domains.json')
        config_loader = ConfigLoader(config_path)

        # Drop domains whose fuse_path does not exist on this platform
        # (e.g. WildcatLake punit_fuses entries when running on Novalake).
        # Must be called after init_hardware() so the ITP namespace is live.
        config_loader.filter_unreachable_domains()

        # Zero-WP domain filtering (filter_zero_wp_domains) is intentionally
        # deferred to a background thread that runs after the GUI window is
        # visible — calling load_fuse_ram() here would block the main thread
        # and prevent the window from opening on slow platforms (e.g. WCL).
        # The CurveManagerUI __init__ schedules this via QTimer.singleShot.

        # If filtering left zero domains this is a first run on a new platform —
        # auto-trigger discovery so the user doesn't have to pass --rediscover.
        if not config_loader.get_domain_list():
            log.info("No domains found for this platform — running auto-discovery...")
            from discovery.startup_discovery import maybe_run_discovery
            maybe_run_discovery(force=True)
            # Reload the freshly populated config
            config_loader = ConfigLoader(config_path)

        # Validate configuration
        is_valid, msg = config_loader.validate_config()
        if not is_valid:
            log.error("Configuration validation failed: %s", msg)
            return 1
        
        domain_count = len(config_loader.get_domain_list())
        log.info("    \u2713 Loaded %d domains successfully", domain_count)
    except Exception as e:
        log.error("    \u2717 Configuration loading failed: %s", e)
        return 1
    
    log.info("[2] Initializing curve operations engine...")
    try:
        curve_engine = CurveEngine(config_loader)
        log.info("    \u2713 Curve engine ready")
    except Exception as e:
        log.error("    \u2717 Engine initialization failed: %s", e)
        return 1
    
    log.info("[3] Launching professional dashboard...")
    try:
        log.info("%s\n  Dashboard ready - Use UI to select domains and manage VF curves\n%s",
                 '=' * 80, '=' * 80)

        window = CurveManagerUI(curve_engine, config_loader)
        window.show()
        return _app.exec_()
    except Exception as e:
        log.error("    \u2717 Dashboard launch failed: %s", e)
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
