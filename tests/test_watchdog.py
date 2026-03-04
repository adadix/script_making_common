"""Tests for utils/watchdog.py."""
import time
import threading
import pytest

import sys, os
# src/ is on path via conftest.py

from utils.watchdog import HealthWatchdog


class TestHealthWatchdogBasic:
    def test_starts_and_stops_cleanly(self):
        wdog = HealthWatchdog(probe_fn=lambda: True, interval=1)
        wdog.start()
        time.sleep(0.1)
        wdog.stop()
        assert True  # no exception

    def test_is_healthy_true_when_probe_passes(self):
        wdog = HealthWatchdog(probe_fn=lambda: True, interval=1)
        wdog.start()
        time.sleep(1.3)   # wait for at least one probe cycle
        wdog.stop()
        assert wdog.is_healthy is True

    def test_is_healthy_false_when_probe_fails(self):
        wdog = HealthWatchdog(probe_fn=lambda: False, interval=1)
        wdog.start()
        time.sleep(1.3)
        wdog.stop()
        assert wdog.is_healthy is False

    def test_last_fault_populated_on_failure(self):
        wdog = HealthWatchdog(probe_fn=lambda: False, interval=1)
        wdog.start()
        time.sleep(1.3)
        wdog.stop()
        assert wdog.last_fault is not None

    def test_last_fault_none_when_healthy(self):
        wdog = HealthWatchdog(probe_fn=lambda: True, interval=1)
        wdog.start()
        time.sleep(1.3)
        wdog.stop()
        assert wdog.last_fault is None


class TestHealthWatchdogCallbacks:
    def test_on_fault_callback_fires(self):
        faults = []
        wdog = HealthWatchdog(
            probe_fn=lambda: False,
            interval=1,
            on_fault=lambda r: faults.append(r),
        )
        wdog.start()
        time.sleep(1.5)
        wdog.stop()
        assert len(faults) >= 1

    def test_on_fault_fires_only_once_per_fault(self):
        """Callback should not fire repeatedly while probe keeps failing."""
        faults = []
        wdog = HealthWatchdog(
            probe_fn=lambda: False,
            interval=1,
            on_fault=lambda r: faults.append(r),
        )
        wdog.start()
        time.sleep(3.5)   # ~3 probe cycles
        wdog.stop()
        assert len(faults) == 1   # only the first detection

    def test_on_recover_fires_after_fault(self):
        recoveries = []
        call_count = [0]

        def _alternating_probe():
            call_count[0] += 1
            # Fail on first call, pass on subsequent
            return call_count[0] > 1

        wdog = HealthWatchdog(
            probe_fn=_alternating_probe,
            interval=1,
            on_recover=lambda: recoveries.append(True),
        )
        wdog.start()
        time.sleep(2.5)
        wdog.stop()
        assert len(recoveries) >= 1

    def test_exception_in_probe_counts_as_unhealthy(self):
        faults = []

        def _bad_probe():
            raise RuntimeError("ITP connection lost")

        wdog = HealthWatchdog(
            probe_fn=_bad_probe,
            interval=1,
            on_fault=lambda r: faults.append(r),
        )
        wdog.start()
        time.sleep(1.5)
        wdog.stop()
        assert len(faults) == 1
        assert 'RuntimeError' in faults[0]

    def test_on_fault_exception_does_not_kill_thread(self):
        """A crashing callback must not kill the watchdog thread."""
        def _bad_on_fault(r):
            raise ValueError("callback exploded")

        wdog = HealthWatchdog(
            probe_fn=lambda: False,
            interval=1,
            on_fault=_bad_on_fault,
        )
        wdog.start()
        time.sleep(1.5)
        wdog.stop()
        # Thread should have exited cleanly (join with timeout)
        assert not wdog._thread.is_alive()

    def test_mock_mode_always_healthy(self):
        """Watchdog configured for mock mode (always-true probe) stays healthy."""
        wdog = HealthWatchdog(probe_fn=lambda: True, interval=1)
        wdog.start()
        time.sleep(2.3)
        wdog.stop()
        assert wdog.is_healthy is True
        assert wdog.last_fault is None
