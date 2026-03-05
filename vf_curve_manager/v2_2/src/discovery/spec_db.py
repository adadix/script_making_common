"""spec_db — load fuse_spec_db.json and enrich flat register records with HAS metadata.

Usage inside discovery_core.py::

    from .spec_db import enrich_records
    enrich_records(platform_name, records)

Fields added to each record (empty string / 0 when no match):
    spec_description  – human-readable field description from the HAS
    spec_precision    – numeric format string, e.g. "U1.8", "100MHz", "U6.6"
    spec_units        – physical unit, e.g. "V", "MHz", "A", "1/C"
    spec_width        – fuse field bit-width (int)
    spec_default      – default / characterization value (str)
    spec_domain       – power domain from the spec (bigcore, gt, de, …)
    spec_doc          – source HAS document filename

Register-name normalisation
---------------------------
pysvtools names look like ``fw_fuses_cluster0_bigcore_vf_voltage_3``.
CoDesign keys look like ``CLUSTER0_BIGCORE.VF_VOLTAGE`` (dot notation) or
``GT_VF_RATIO_0`` (flat), or ``FUSES_IA_VF_RATIO`` (GFC prefix).

The function ``_build_index()`` pre-expands every CoDesign key to all the
forms a pysvtools name could reduce to after prefix-stripping and
index-stripping so that lookups are O(1) and cover cross-platform naming
differences without per-platform special casing.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths and module-level cache
# ---------------------------------------------------------------------------

_DB_PATH: pathlib.Path = pathlib.Path(__file__).parent.parent / 'fuse_spec_db.json'

_DB:  Optional[dict] = None         # raw platform-keyed DB from JSON
_IDX: dict[str, dict] = {}          # {PLATFORM: {normalized_key: entry_dict}}

# ---------------------------------------------------------------------------
# Platform / project maps
# ---------------------------------------------------------------------------

#: Map from platform strings the tool might detect → canonical DB key.
#
#  GFC is the bigcore-tile IP name (Granite Falls Crestmont), not a standalone
#  product.  Product platforms that use GFC core naming (RZL, TTL, HBO) are
#  aliased here so their register lookups resolve against the 'GFC' DB section.
#  WCL/NVL have their own product-level HAS sections and are NOT aliased to GFC.
_PLATFORM_ALIAS: dict[str, str] = {
    'WCL': 'WCL', 'PTL': 'WCL', 'LNL': 'WCL',
    'NVL': 'NVL',
    # GFC is the core IP; RZL, TTL, HBO use GFC-named bigcore fuses
    'RZL': 'GFC', 'TTL': 'GFC', 'HBO': 'GFC',
    'GFC': 'GFC',   # in case a lab system ever reports 'GFC'
    'PNC': 'PNC', 'MTL': 'MTL',
    'LNC': 'LNC', 'RWC': 'RWC', 'GLC': 'GLC',
}

#: Map platform → CoDesign MCP project ID (for spec_db_request.json)
#  RZL, TTL, HBO query the 'GFC' CoDesign project (same core IP)
_CODESIGN_PROJECT: dict[str, str] = {
    'WCL': 'LNL_PTL_WCL', 'PTL': 'LNL_PTL_WCL', 'LNL': 'LNL_PTL_WCL',
    'NVL': 'NVL',
    'GFC': 'GFC', 'RZL': 'GFC', 'TTL': 'GFC', 'HBO': 'GFC',
    'PNC': 'PNC', 'MTL': 'MTL',
    'LNC': 'LNC', 'RWC': 'RWC', 'GLC': 'GLC',
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STRIP_PREFIXES = ('FW_FUSES_', 'FUSES_CORE_IA_', 'FUSES_CORE_', 'FUSES_IA_', 'FUSES_')


def _norm_candidates(reg_name: str) -> list[str]:
    """Return candidate lookup keys for a pysvtools register name.

    Steps applied to *reg_name*:
      1. Uppercase
      2. Try as-is (handles GT_VF_RATIO_0 style)
      3. Strip common pysvtools/DB prefixes
      4. For each resulting form, also strip trailing instance index ``_N``
    """
    base = reg_name.strip().upper()
    forms: list[str] = [base]

    # Strip pysvtools-side prefix
    for pfx in _STRIP_PREFIXES:
        if base.startswith(pfx):
            forms.append(base[len(pfx):])
            break

    # For every form so far also strip trailing ``_<digit(s)>``
    result: list[str] = []
    seen: set[str] = set()
    for f in forms:
        no_idx = re.sub(r'_\d+$', '', f)
        for c in (f, no_idx):
            if c and c not in seen:
                seen.add(c)
                result.append(c)
    return result


def _build_index(platform_data: dict) -> dict[str, dict]:
    """Pre-index a platform's spec entries by every candidate lookup key.

    For each CoDesign canonical key we add:
      • The key itself (e.g. ``CLUSTER0_BIGCORE.VF_VOLTAGE``)
      • Dot → underscore form (``CLUSTER0_BIGCORE_VF_VOLTAGE``)
      • Without trailing instance index (``CLUSTER0_BIGCORE_VF_VOLTAGE``)
      • GFC-style: strip ``FUSES_CORE_IA_``, ``FUSES_CORE_``, ``FUSES_IA_``,
        ``FUSES_`` prefixes from the underscore form
    """
    idx: dict[str, dict] = {}

    for canonical_key, entry in platform_data.items():
        dot_under = canonical_key.replace('.', '_')
        no_idx    = re.sub(r'_\d+$', '', dot_under)

        candidates: set[str] = {canonical_key, dot_under, no_idx}

        # GFC-style DB prefix stripping — try ALL matching prefixes so that
        # e.g. FUSES_IA_VF_RATIO generates both VF_RATIO (strip FUSES_IA_)
        # and IA_VF_RATIO (strip FUSES_), covering all pysvtools name variants.
        for form in (dot_under, no_idx):
            for pfx in ('FUSES_CORE_IA_', 'FUSES_CORE_', 'FUSES_IA_', 'FUSES_'):
                if form.startswith(pfx):
                    stripped = form[len(pfx):]
                    candidates.add(stripped)
                    candidates.add(re.sub(r'_\d+$', '', stripped))

        for k in candidates:
            if k and k not in idx:
                idx[k] = entry

    return idx


def _load() -> None:
    """Lazily load and index the spec DB (once)."""
    global _DB, _IDX
    if _DB is not None:
        return
    try:
        _DB   = json.loads(_DB_PATH.read_text(encoding='utf-8'))
        _IDX  = {}
        count = 0
        for plat, data in _DB.items():
            if plat.startswith('__'):
                continue
            _IDX[plat] = _build_index(data)
            count += len(data)
        log.debug(
            'spec_db: loaded %d entries across %d platform(s) from %s',
            count, len(_IDX), _DB_PATH,
        )
    except FileNotFoundError:
        log.debug('spec_db: %s not found — running without spec enrichment', _DB_PATH)
        _DB  = {}
        _IDX = {}
    except Exception as exc:
        log.warning('spec_db: failed to load %s — %s', _DB_PATH, exc)
        _DB  = {}
        _IDX = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup(platform: str, reg_name: str) -> Optional[dict]:
    """Return the spec entry for *reg_name* on *platform*, or ``None``."""
    _load()
    plat_key = _PLATFORM_ALIAS.get(str(platform).upper(), str(platform).upper())
    idx      = _IDX.get(plat_key, {})
    for candidate in _norm_candidates(reg_name):
        entry = idx.get(candidate)
        if entry:
            return entry
    return None


def enrich_records(platform: str, records: list) -> None:
    """Add ``spec_*`` fields **in-place** to each record dict in *records*.

    No exception is raised if the DB is absent; all spec fields are simply
    set to their empty defaults so downstream code never sees ``KeyError``.
    """
    _load()
    hits = 0
    for rec in records:
        entry = lookup(platform, rec.get('name', ''))
        if entry:
            rec['spec_description'] = str(entry.get('description') or '')
            rec['spec_precision']   = str(entry.get('precision')   or '')
            rec['spec_units']       = str(entry.get('units')       or '')
            rec['spec_width']       = int(entry.get('width')       or 0)
            rec['spec_default']     = str(entry.get('default')     or '')
            rec['spec_domain']      = str(entry.get('domain')      or '')
            rec['spec_doc']         = str(entry.get('doc_source')  or '')
            hits += 1
        else:
            rec.setdefault('spec_description', '')
            rec.setdefault('spec_precision',   '')
            rec.setdefault('spec_units',       '')
            rec.setdefault('spec_width',       0)
            rec.setdefault('spec_default',     '')
            rec.setdefault('spec_domain',      '')
            rec.setdefault('spec_doc',         '')

    if records:
        log.debug(
            'spec_db.enrich_records: %d/%d match(es) for platform=%s',
            hits, len(records), platform,
        )


def get_codesign_project(platform: str) -> str:
    """Return the CoDesign MCP project ID for *platform* (empty string if unknown)."""
    return _CODESIGN_PROJECT.get(str(platform).upper(), '')


def write_request(platform: str, register_names: list) -> pathlib.Path:
    """Write ``spec_db_request.json`` so the user knows what to query.

    Called when a platform is detected that has no entry in fuse_spec_db.json.
    The generated file contains the platform name, CoDesign project ID, and a
    sample register list so Copilot can query CoDesign and update the DB.
    """
    import datetime as _dt

    req_path = _DB_PATH.parent / 'spec_db_request.json'
    req: dict = {
        'platform':         platform,
        'codesign_project': get_codesign_project(platform),
        'timestamp':        _dt.datetime.now().isoformat(timespec='seconds'),
        'register_count':   len(register_names),
        'register_names':   register_names[:200],
        'instructions': (
            'Open VS Code and ask Copilot: '
            '"Please query CoDesign for all VF/ITD/PM fuse specs on '
            f'{platform} (CoDesign project: {get_codesign_project(platform)}) '
            'and add them to src/fuse_spec_db.json."'
        ),
    }
    req_path.write_text(json.dumps(req, indent=2), encoding='utf-8')
    log.info('spec_db: wrote spec request → %s', req_path)
    return req_path
