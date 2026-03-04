"""
auto_discover_vf_registers.py — Backward-compatibility shim
============================================================
All implementation has been migrated to the two sub-modules:

  discovery_core   — platform detection, fuse-path scan, register
                     categorisation, cache persistence
  discovery_learn  — pattern learning, auto_merge, scalar discovery,
                     build_vf_domains_from_discovery, run_discovery_pipeline

Existing callers that import directly from this file continue to work
unchanged because everything is re-exported here via wildcard imports.
"""
from __future__ import annotations

from .discovery_core import *   # noqa: F401, F403
from .discovery_learn import *  # noqa: F401, F403

# Private symbols are not exported by '*' — re-export explicitly so that
# existing callers using  `from discovery.auto_discover_vf_registers import _X`
# continue to work after the monolith was split into sub-modules.
from .discovery_core import (  # noqa: F401
    _DISCOVERY_TARGET_DOWN_KW,
    _FUSE_ROOT_CANDIDATES,
    _ZERO_VALID_PATTERNS,
    VF_KEYWORDS,
    _save_discovery_cache,
    _all_results_to_flat_records,
)
from .discovery_learn import _infer_conversion_from_description  # noqa: F401
