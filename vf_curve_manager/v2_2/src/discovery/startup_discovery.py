"""
startup_discovery.py — Autonomous discovery bridge
====================================================

Called by vf_curve_manager.py (GUI) and vf_curve_manager_cli.py (CLI)
immediately after ITP is initialised and BEFORE vf_domains.json is loaded.

Logic:
  • If vf_domains.json has ≥1 domain  →  skip (already populated).
  • If vf_domains.json is empty/missing  →  run full discovery pipeline.
  • If force=True (--rediscover flag)    →  always run, even if populated.

After discovery the tool continues to start normally; vf_domains.json has
been freshly written so ConfigLoader picks up all discovered domains.
"""

from __future__ import annotations

import json
import logging
import os
import traceback

log = logging.getLogger(__name__)

_SRC_DIR = os.path.dirname(os.path.dirname(__file__))


def _get_cached_platform() -> str:
    """Return the platform name stored in vf_discovery_cache.json, or '' if unavailable."""
    try:
        cache_path = os.path.join(_SRC_DIR, 'vf_discovery_cache.json')
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('platform', '').lower()
    except Exception:
        pass
    return ''


def _get_domains_platform() -> str:
    """Return the platform name stamped inside vf_domains.json (_platform key).

    This is the authoritative source — written by auto_merge_to_vf_domains()
    every time the domains file is regenerated.  Returns '' if the file is
    missing, unpopulated, or was written before the stamp was introduced.
    """
    try:
        domains_path = os.path.join(_SRC_DIR, 'vf_domains.json')
        if os.path.exists(domains_path):
            with open(domains_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('_platform', '').lower()
    except Exception:
        pass
    return ''


def _domains_json_is_populated() -> bool:
    """Return True if vf_domains.json exists and contains at least one domain."""
    try:
        domains_path = os.path.join(_SRC_DIR, 'vf_domains.json')
        if not os.path.exists(domains_path):
            return False
        with open(domains_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return len(data.get('domains', {})) > 0
    except Exception:
        return False


def maybe_run_discovery(force: bool = False) -> bool:
    """Run VF register discovery if vf_domains.json is unpopulated (or forced).

    Decision logic (evaluated in order):
      1. force=True  → always run (--rediscover mode).
      2. vf_domains.json is empty / missing  → run unconditionally.
      3. Platform stamp in vf_domains.json differs from live hardware  → run.
      4. Discovery cache platform differs from live hardware  → run.
      5. Platform matches and domains exist  → SKIP (early return False).

    Must be called AFTER hardware_access.init_hardware() so that
    detect_platform_name() can probe the live ITP namespace.

    Returns:
        True  — discovery pipeline ran and vf_domains.json was updated.
        False — skipped because the file was already up-to-date.
    """
    if not force:
        # If the file is empty or missing, always run — no point in a platform
        # check because there is nothing to compare against.
        if not _domains_json_is_populated():
            log.info("vf_domains.json is empty or missing — discovery required.")
            force = True

    if not force:
        # File has domains — check whether they were built for THIS platform.
        try:
            from .auto_discover_vf_registers import detect_platform_name
            current = detect_platform_name().lower()

            # Primary check: _platform stamp written by build_vf_domains_from_discovery
            domains_plat = _get_domains_platform()
            # Empty stamp means the file was deliberately cleared (e.g. after a
            # code update) — always rediscover so new root-enumeration logic runs.
            if not domains_plat:
                log.info("vf_domains.json has no _platform stamp — forcing re-discovery")
                force = True
            elif domains_plat != current:
                log.warning(
                    "vf_domains.json was built for '%s' but connected platform is '%s' "
                    "— forcing re-discovery", domains_plat, current
                )
                force = True

            # Fallback: discovery cache (present on tools built before the stamp)
            if not force:
                cached = _get_cached_platform()
                if cached and current and cached != current:
                    log.warning(
                        "Discovery cache platform '%s' != connected platform '%s' "
                        "— forcing re-discovery", cached, current
                    )
                    force = True

            # Platform matches and domains exist — nothing to do.
            if not force:
                log.info(
                    "Platform '%s' matches vf_domains.json — skipping discovery.",
                    current,
                )
                return False

        except Exception as _pf_ex:
            # If we cannot detect the platform (e.g. ITP not yet ready), run
            # discovery to be safe — it will detect the platform itself.
            log.debug("Platform cross-check failed: %s — will run discovery", _pf_ex)
            force = True

    # ── Discovery must run ────────────────────────────────────────────────
    print(
        "\n[*] Starting VF register discovery — this takes several minutes "
        "on first run or after platform change.",
        flush=True,
    )
    try:
        from .auto_discover_vf_registers import run_discovery_pipeline
        return run_discovery_pipeline(force=force)
    except ImportError as exc:
        log.error("Discovery module not available: %s — continuing with existing vf_domains.json", exc)
        return False
    except Exception as exc:
        log.error("Discovery pipeline error: %s — continuing with existing vf_domains.json", exc)
        traceback.print_exc()
        return False
