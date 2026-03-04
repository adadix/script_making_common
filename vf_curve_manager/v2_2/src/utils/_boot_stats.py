"""
_boot_stats.py — Adaptive boot-timeout statistics
==================================================
Physically owns the boot-time recording and P90-based adaptive-timeout
logic.  Has zero hardware dependencies (only ``json`` and ``pathlib``),
so it can be imported by any module without circular-import risk.

Public API
----------
get_adaptive_boot_timeout(default=300) -> int
record_boot_time(elapsed_s)
_load_boot_stats() -> dict
_save_boot_stats(stats)
_BOOT_STATS_PATH : pathlib.Path

Imported by:
  utils.hardware_access   (backward compat + runtime use)
  utils.itp_recovery      (façade re-export)
"""
import json
import logging
import pathlib

log = logging.getLogger(__name__)

# Path to the persistent rolling-sample JSON file.
# Stored in project-root Logs/ alongside all other output files.
_BOOT_STATS_PATH: pathlib.Path = (
    pathlib.Path(__file__).parent.parent.parent / 'Logs' / 'boot_time_stats.json'
)

# Maximum number of boot-time samples to keep in the rolling window.
_MAX_SAMPLES: int = 50


def _load_boot_stats() -> dict:
    """Load persisted boot-time statistics (P90, raw samples).

    Returns a dict with keys ``times`` (list[float]) and ``p90`` (int).
    Returns the safe default ``{'times': [], 'p90': 300}`` when the file
    does not exist or cannot be parsed.
    """
    try:
        if _BOOT_STATS_PATH.exists():
            with open(_BOOT_STATS_PATH) as _f:
                return json.load(_f)
    except Exception:
        pass
    return {'times': [], 'p90': 300}


def _save_boot_stats(stats: dict) -> None:
    """Persist boot-time statistics to disk (silent on any error)."""
    try:
        _BOOT_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_BOOT_STATS_PATH, 'w') as _f:
            json.dump(stats, _f)
    except Exception:
        pass


def get_adaptive_boot_timeout(default: int = 300) -> int:
    """Return the learned P90 + 60 s margin as the recommended boot timeout.

    Falls back to *default* when fewer than 3 samples have been recorded.
    The result is always at least ``default // 2`` seconds to prevent an
    unreasonably short timeout if the platform consistently boots fast.

    Args:
        default: Fallback timeout in seconds (used when sample count < 3).

    Returns:
        int: Recommended boot timeout in seconds.
    """
    stats = _load_boot_stats()
    if len(stats.get('times', [])) < 3:
        return default
    p90 = stats.get('p90', default)
    return max(int(p90), default // 2)


def record_boot_time(elapsed_s: float) -> None:
    """Record a successful observed boot duration and persist the updated P90.

    Appends *elapsed_s* to a rolling window of the last :data:`_MAX_SAMPLES`
    samples, computes the 90th-percentile, and stores ``p90 + 60`` seconds
    as the safety-margined timeout for the next call to
    :func:`get_adaptive_boot_timeout`.

    Args:
        elapsed_s: Observed boot duration in seconds (wall-clock time from
                   ``itp.resettarget()`` to first successful ping + ITP access).
    """
    stats = _load_boot_stats()
    times: list = stats.get('times', [])
    times.append(round(elapsed_s, 1))
    times = times[-_MAX_SAMPLES:]
    sorted_times = sorted(times)
    idx = int(len(sorted_times) * 0.9)
    p90_raw = sorted_times[min(idx, len(sorted_times) - 1)]
    p90_margin = int(p90_raw) + 60
    _save_boot_stats({'times': times, 'p90': p90_margin, 'count': len(times)})
    log.debug("Recorded %.1fs boot. P90+60s estimate: %ds (from %d samples)",
             elapsed_s, p90_margin, len(times))
