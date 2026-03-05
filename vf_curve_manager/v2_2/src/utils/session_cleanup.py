"""
session_cleanup.py — End-of-session file cleanup
=================================================

On every clean exit (GUI close, CLI return, Ctrl-C) this module:

  1. Copies vf_domains.json       → vf_domains.json.bak      (overwrite)
  2. Copies vf_discovery_cache.json → vf_discovery_cache.json.bak  (overwrite)
  3. Replaces vf_domains.json with an empty stub (_platform: "")
  4. Replaces vf_discovery_cache.json with {}

Effect on next launch:
  • Empty _platform stamp triggers `maybe_run_discovery()` unconditionally
    so the tool always rediscovers on the next run — picks up ALL fuse roots
    (cdie.fuses, hub.fuses, …) regardless of platform.
  • .bak files are preserved for debugging — overwritten each session so only
    the most-recent-run snapshot is kept.

Both GUI and CLI call `register_cleanup()` once after ITP init.
`atexit` guarantees cleanup runs on normal exit, KeyboardInterrupt, and
most unhandled exceptions.  The GUI additionally hooks `QApplication.aboutToQuit`
for clean window-close handling.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil

log = logging.getLogger(__name__)

# Resolve src/ directory (this file lives in src/utils/)
_SRC_DIR = os.path.dirname(os.path.dirname(__file__))

_DOMAINS_JSON        = os.path.join(_SRC_DIR, 'vf_domains.json')
_CACHE_JSON          = os.path.join(_SRC_DIR, 'vf_discovery_cache.json')
_DOMAINS_BAK         = _DOMAINS_JSON  + '.bak'
_CACHE_BAK           = _CACHE_JSON   + '.bak'

# Empty stubs written back after backup
_DOMAINS_EMPTY_STUB: dict = {
    "_platform":          "",
    "_platform_updated":  "",
    "_generated_by":      "build_vf_domains_from_discovery",
    "domains":            {},
}
_CACHE_EMPTY_STUB: dict = {}

_cleanup_registered: bool = False
_cleanup_ran:        bool = False


def _do_cleanup() -> None:
    """Back up then clear session files.  Idempotent — safe to call multiple times."""
    global _cleanup_ran
    if _cleanup_ran:
        return
    _cleanup_ran = True
    log.info("[cleanup] Running end-of-session file cleanup...")
    # ── vf_domains.json ───────────────────────────────────────────────────
    try:
        if os.path.exists(_DOMAINS_JSON):
            # Only back up if the file has actual domains (skip empty stubs)
            try:
                with open(_DOMAINS_JSON, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('domains'):
                    shutil.copy2(_DOMAINS_JSON, _DOMAINS_BAK)
                    log.info("[cleanup] vf_domains.json → vf_domains.json.bak")
            except Exception:
                pass  # unreadable — skip backup, still clear below

            with open(_DOMAINS_JSON, 'w', encoding='utf-8') as f:
                json.dump(_DOMAINS_EMPTY_STUB, f, indent=2)
            log.info("[cleanup] vf_domains.json cleared (will rediscover on next launch)")
    except Exception as exc:
        log.warning("[cleanup] Could not clear vf_domains.json: %s", exc)

    # ── vf_discovery_cache.json ───────────────────────────────────────────
    try:
        if os.path.exists(_CACHE_JSON):
            try:
                with open(_CACHE_JSON, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                if cache:  # non-empty → worth backing up
                    shutil.copy2(_CACHE_JSON, _CACHE_BAK)
                    log.info("[cleanup] vf_discovery_cache.json → vf_discovery_cache.json.bak")
            except Exception:
                pass

            with open(_CACHE_JSON, 'w', encoding='utf-8') as f:
                json.dump(_CACHE_EMPTY_STUB, f, indent=2)
            log.info("[cleanup] vf_discovery_cache.json cleared")
    except Exception as exc:
        log.warning("[cleanup] Could not clear vf_discovery_cache.json: %s", exc)


def register_cleanup(qt_app=None) -> None:
    """Register cleanup with atexit and optionally with Qt's aboutToQuit signal.

    Args:
        qt_app: A QApplication instance.  When provided, _do_cleanup() is also
                connected to qt_app.aboutToQuit so cleanup runs even when Qt
                terminates via os._exit() on Windows window-close, which
                bypasses Python's atexit entirely.
    """
    global _cleanup_registered
    if not _cleanup_registered:
        atexit.register(_do_cleanup)
        _cleanup_registered = True
        log.debug("[cleanup] Registered via atexit")

    if qt_app is not None:
        try:
            qt_app.aboutToQuit.connect(_do_cleanup)
            log.debug("[cleanup] Registered via QApplication.aboutToQuit")
        except Exception as exc:
            log.warning("[cleanup] Could not connect aboutToQuit: %s", exc)
