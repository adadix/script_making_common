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

# ── Startup refresh dialog ──────────────────────────────────────────────
# QApplication must be created before any widget/dialog.
from PyQt5.QtWidgets import QApplication

_app = QApplication(sys.argv)
# ─────────────────────────────────────────────────────────────────────────

# Import modular components AFTER namednodes to ensure ITP objects are available
try:
    from utils import hardware_access
    from core.config_loader import ConfigLoader
    from core.curve_engine import CurveEngine
    from ui.curve_manager_ui import CurveManagerUI
    from discovery.startup_discovery import maybe_run_discovery

    # Initialize hardware access module FIRST; pass globals() so get_fuse_object
    # resolves ITP root objects (cdie, soc, etc.) from this module's namespace
    # rather than relying on __main__, making it test-friendly.
    hardware_access.init_hardware(ipc, itp, namespace=globals())

    # Register end-of-session cleanup: backs up then clears vf_domains.json and
    # vf_discovery_cache.json so the next launch always runs fresh discovery.
    # Pass _app so cleanup is also connected to QApplication.aboutToQuit —
    # on Windows, Qt can call os._exit() on window-close which bypasses atexit.
    from utils.session_cleanup import register_cleanup as _reg_cleanup
    _reg_cleanup(qt_app=_app)

    # NOTE: maybe_run_discovery() is called inside main() — AFTER init_hardware() —
    # so that the ITP namespace is fully populated before any register probing.

except ImportError as e:
    log.critical("Failed to import modular components: %s\n"
                 "Please ensure:\n"
                 "  1. All source files are present in src/core, src/ui, and src/utils\n"
                 "  2. PyQt5 is installed: pip install PyQt5\n"
                 "  3. Required packages are installed: pip install -r requirements.txt",
                 e)
    sys.exit(1)


class _DiscoveryWorker:
    """Thin wrapper that runs ``maybe_run_discovery`` in a background thread.

    Using a plain Python ``threading.Thread`` instead of ``QThread`` avoids
    the extra QObject overhead and works fine here because we never emit Qt
    signals from the worker itself — the main thread polls ``is_alive()``
    via a QTimer.
    """

    def __init__(self, force: bool) -> None:
        import threading
        self._force = force
        self._result: bool = False
        self._exc: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="DiscoveryWorker")

    def _run(self) -> None:
        try:
            self._result = maybe_run_discovery(force=self._force)
        except BaseException as exc:  # noqa: BLE001
            self._exc = exc

    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def result(self) -> bool:
        return self._result

    @property
    def exception(self) -> "BaseException | None":
        return self._exc


def _run_discovery_with_splash(force: bool = False) -> bool:
    """Run maybe_run_discovery() while showing a non-blocking progress dialog.

    The discovery pipeline is executed in a background daemon thread so the
    Qt event loop on the main thread stays alive — the dialog repaints, the
    OS keeps the window responsive, and the animated progress bar actually
    pulses.

    Returns True if discovery ran and wrote at least one domain.
    """
    from PyQt5.QtWidgets import QProgressDialog
    from PyQt5.QtCore import Qt, QTimer, QEventLoop

    splash = QProgressDialog(
        "Discovering VF registers from hardware…\n"
        "This may take several minutes on first run.",
        None,   # no Cancel button
        0, 0,   # indeterminate / pulsing bar
    )
    splash.setWindowTitle("VF Curve Manager — First-run Discovery")
    splash.setWindowModality(Qt.ApplicationModal)
    splash.setMinimumWidth(480)
    splash.setMinimumDuration(0)   # show immediately without the default 2 s delay
    splash.setAutoClose(False)
    splash.setAutoReset(False)
    # Do NOT call setValue() here — with range(0,0) that triggers Qt's internal
    # "complete" detection and immediately resets/hides the pulsing bar.
    splash.show()
    _app.processEvents()

    worker = _DiscoveryWorker(force=force)
    worker.start()

    # Pump the Qt event loop at ~20 Hz until the background thread finishes.
    # This keeps the dialog responsive and the pulsing bar animated.
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)   # 50 ms → 20 Hz

    def _check_done():
        if not worker.is_alive():
            timer.stop()
            loop.quit()

    timer.timeout.connect(_check_done)
    timer.start()
    loop.exec_()   # blocks main thread but keeps event loop running

    splash.close()
    _app.processEvents()

    if worker.exception is not None:
        raise worker.exception  # re-raise on the main thread

    return worker.result


def main():
    """Main entry point for VF Curve Manager Tool."""
    from PyQt5.QtWidgets import QMessageBox

    log.info("[1] Loading hardware configuration...")
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'vf_domains.json')

        # ── Discovery ─────────────────────────────────────────────────────────
        # Runs AFTER init_hardware() so the ITP namespace is fully populated
        # before any register probing takes place.
        # A blocking splash dialog is shown during first-run or platform-change
        # discovery so the user sees progress instead of an empty window.
        log.info("    Checking platform / domain cache...")

        from discovery.startup_discovery import _domains_json_is_populated, _get_domains_platform
        from discovery.auto_discover_vf_registers import detect_platform_name as _dpn

        # Determine up-front whether we expect discovery to run, so we can
        # show the splash only when it will actually do something.
        _needs_discovery = (
            not _domains_json_is_populated()
            or _get_domains_platform() != _dpn().lower()
        )

        if _needs_discovery:
            _discovery_ran = _run_discovery_with_splash(force=False)
        else:
            _discovery_ran = False
            maybe_run_discovery(force=False)
        # ──────────────────────────────────────────────────────────────────────

        config_loader = ConfigLoader(config_path)

        # Drop domains whose fuse_path does not exist on this platform.
        config_loader.filter_unreachable_domains()

        # If filtering left zero domains it means either:
        #   a) First run on a brand-new platform and vf_domains.json is still empty
        #   b) A platform mismatch that wasn't caught by the stamp check
        # Either way, force a full re-discovery with the splash shown.
        if not config_loader.get_domain_list():
            log.info("No domains found after first discovery pass — forcing full re-discovery...")
            _discovery_ran = _run_discovery_with_splash(force=True)
            # Reload the freshly written config and re-filter.
            config_loader = ConfigLoader(config_path)
            config_loader.filter_unreachable_domains()

        # Signal to the GUI that discovery ran this session so the zero-WP
        # background filter is skipped.  Discovery validated all registers as
        # accessible; re-reading them 500 ms later while the fuse RAM session
        # guard prevents a reload causes spurious all-zero reads that would
        # incorrectly prune every domain from the selector.
        config_loader._just_discovered = _discovery_ran

        # ── Post-discovery fuse RAM pre-warm + synchronous zero-WP filter ────
        # On first run the session guard (set by discovery) blocks the
        # background-thread filter from re-loading fuse RAM, and the ITP fuse
        # objects may not be thread-safe.  Run the load + filter HERE on the
        # main thread while ITP is in a stable post-discovery state so the
        # domain list is correctly trimmed before the GUI window ever opens.
        if _discovery_ran:
            log.info("    [post-discovery] Pre-loading fuse RAM and filtering zero-WP domains...")
            try:
                from utils.fuse_io import load_fuse_ram
                from utils import hardware_access as _ha_ref

                # Collect unique fuse_ram_path values across all domains.
                _loaded: set = set()
                for _dcfg in config_loader.get_all_domains().values():
                    _frp = _dcfg.get('fuse_ram_path', _dcfg.get('fuse_path', ''))
                    if _frp and _frp not in _loaded:
                        if _frp in _ha_ref._LOADED_FUSE_RAM_PATHS:
                            # Discovery already loaded this path successfully —
                            # session guard is valid, no need to reload.
                            log.info("    [post-discovery] Already loaded by discovery: %s", _frp)
                        else:
                            # Discovery failed or didn't reach this path — try once now.
                            log.info("    [post-discovery] Loading fuse RAM: %s", _frp)
                            load_fuse_ram(_dcfg)
                        _loaded.add(_frp)

                # Now run the zero-WP filter synchronously on the main thread.
                _pruned = config_loader.filter_zero_wp_domains()
                if _pruned:
                    log.info("    [post-discovery] Removed %d zero-WP domain(s): %s",
                             len(_pruned), ', '.join(_pruned))
                else:
                    log.info("    [post-discovery] All domains have non-zero WPs — none pruned.")
            except Exception as _pw_ex:
                log.warning("    [post-discovery] Pre-warm skipped (non-fatal): %s", _pw_ex)
        # ─────────────────────────────────────────────────────────────────────

        # ── Hard stop: never open the GUI with zero domains ──────────────────
        # If discovery succeeded the domain list will be non-empty.
        # If it is still empty, something went wrong (ITP not ready, platform
        # not recognised, discovery exception) — show a clear error and exit
        # rather than opening a useless empty window.
        if not config_loader.get_domain_list():
            log.error("Discovery completed but NO domains were found. "
                      "Check ITP connection, platform_config.json, and logs.")
            QMessageBox.critical(
                None,
                "VF Curve Manager — Discovery Failed",
                "VF register discovery completed but found no domains.\n\n"
                "Possible causes:\n"
                "  • ITP / OpenIPC not connected or not fully initialised\n"
                "  • Platform not recognised in platform_config.json\n"
                "  • Fuse RAM load failed (check terminal for errors)\n\n"
                "Check the terminal output for detailed errors,\n"
                "then re-run the tool once ITP is ready.",
            )
            return 1

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
