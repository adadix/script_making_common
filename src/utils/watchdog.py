"""
ITP Health-Check Watchdog
=========================

Runs a lightweight daemon thread that periodically calls a user-supplied
*probe function*.  If the probe raises or returns a falsy value the watchdog
fires an *on_fault* callback and marks itself as unhealthy.  When the probe
recovers it marks itself healthy again.

Typical usage in CLI main():

    from utils.watchdog import HealthWatchdog

    def _itp_probe():
        from utils import hardware_access as ha
        if ha.itp is None:
            return True          # not yet fully initialised — skip
        try:
            return bool(ha.itp.cv.isconnected())
        except Exception:
            return False

    wdog = HealthWatchdog(
        probe_fn = _itp_probe,
        interval = 30,
        on_fault = lambda r: print(f"\\n[WATCHDOG] \\u26a0  {r}"),
    )
    wdog.start()
    try:
        ... run commands ...
    finally:
        wdog.stop()

In mock mode pass ``probe_fn=lambda: True`` so the thread is a harmless no-op.
"""

import threading
import time
import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)


class HealthWatchdog:
    """
    Background daemon thread that probes ITP health at a fixed interval and
    fires a callback on the first detection of a fault / recovery.
    """

    def __init__(
        self,
        probe_fn: Callable[[], bool],
        interval: int = 30,
        on_fault: Optional[Callable[[str], None]] = None,
        on_recover: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            probe_fn:   Callable → bool.  Return True = healthy.
                        Raising any exception counts as unhealthy.
            interval:   Seconds between probes (minimum 1).
            on_fault:   Called once on the first detection of each fault.
                        Receives a human-readable reason string.
            on_recover: Called once when health is restored after a fault.
        """
        self._probe_fn    = probe_fn
        self._interval    = max(1, int(interval))
        self._on_fault    = on_fault
        self._on_recover  = on_recover
        self._stop_event  = threading.Event()
        self._healthy     = True          # optimistic until first probe
        self._last_fault: Optional[str] = None
        self._thread      = threading.Thread(
            target=self._run, name='HealthWatchdog', daemon=True
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the watchdog daemon thread."""
        log.debug("[WATCHDOG] Starting (interval=%ds)", self._interval)
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop and wait up to (interval+2) seconds."""
        self._stop_event.set()
        self._thread.join(timeout=self._interval + 2)
        log.debug("[WATCHDOG] Stopped")

    @property
    def is_healthy(self) -> bool:
        """True if the most recent probe succeeded (or no probe has run yet)."""
        return self._healthy

    @property
    def last_fault(self) -> Optional[str]:
        """Human-readable description of the last fault, or None if healthy."""
        return self._last_fault

    # ------------------------------------------------------------------ #
    # Internal loop
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        """Main watchdog loop — runs on the daemon thread."""
        while not self._stop_event.wait(timeout=self._interval):
            healthy, reason = self._probe()

            if healthy:
                if not self._healthy:
                    # Transition: unhealthy → healthy
                    self._healthy    = True
                    self._last_fault = None
                    log.info("[WATCHDOG] \u2713 Hardware connection restored")
                    if self._on_recover:
                        try:
                            self._on_recover()
                        except Exception as cb_ex:
                            log.debug("[WATCHDOG] on_recover callback raised: %s", cb_ex)
            else:
                if self._healthy:
                    # Transition: healthy → unhealthy (fire callback once)
                    self._healthy    = False
                    self._last_fault = reason
                    log.warning("[WATCHDOG] \u26a0 Hardware fault: %s", reason)
                    if self._on_fault:
                        try:
                            self._on_fault(reason)
                        except Exception as cb_ex:
                            log.debug("[WATCHDOG] on_fault callback raised: %s", cb_ex)
                else:
                    # Still unhealthy on subsequent probes — log at debug only
                    log.debug("[WATCHDOG] Still unhealthy: %s", reason)

    def _probe(self):
        """Call probe_fn, return (healthy: bool, reason: str)."""
        try:
            result = self._probe_fn()
            if result:
                return True, None
            return False, "Probe returned falsy value"
        except Exception as ex:
            return False, f"Probe raised {type(ex).__name__}: {ex}"
