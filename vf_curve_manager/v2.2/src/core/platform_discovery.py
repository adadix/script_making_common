"""
platform_discovery.py � compatibility shim
==========================================

The full platform-discovery logic has been consolidated into
``auto_discover_vf_registers.py`` (``run_discovery_pipeline``).

This shim preserves the public ``discover_and_save`` API that
``config_loader.py`` uses, so no callers need to be updated.

The original 531-line implementation is preserved in
``platform_discovery.py.bak`` if you ever need to reference it.
"""

from pathlib import Path as _Path
import logging
import sys
import os

log = logging.getLogger(__name__)


def discover_and_save(output_path: str = "vf_domains_auto.json") -> dict:
    """Run the VF register discovery pipeline.

    Delegates to ``auto_discover_vf_registers.run_discovery_pipeline``.
    The *output_path* argument is accepted for API compatibility but the
    pipeline always writes directly to ``vf_domains.json`` in the src/
    directory (the authoritative config file for the tool).

    Returns:
        dict: Empty dict (discovery results are written to vf_domains.json,
              not returned in memory). Callers should reload vf_domains.json
              after this returns.
    """
    # Ensure src/ is on the path so the discovery package can be imported
    _src_dir = str(_Path(__file__).parent.parent)
    if _src_dir not in sys.path:
        sys.path.insert(0, _src_dir)

    try:
        from discovery.auto_discover_vf_registers import run_discovery_pipeline
        run_discovery_pipeline(force=True)
    except Exception as exc:
        log.warning("platform_discovery shim: run_discovery_pipeline failed: %s", exc)

    return {}


if __name__ == "__main__":
    discover_and_save()
