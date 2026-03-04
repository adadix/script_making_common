"""
discovery_learn.py — Pattern learning, domain building, scalar discovery, pipeline
==================================================================================
Canonical home for the higher-level discovery functions, split from the original
auto_discover_vf_registers.py monolith.

All shared state comes from discovery_core; functions that need core helpers
import them via ``from .discovery_core import ...``.
"""
from __future__ import annotations

import logging
import sys
import subprocess
import shutil
import copy
import time
import json
import re
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime
import io
import traceback

# Shared state and all core functions — learn functions may call any of them
from .discovery_core import (
    SCRIPT_DIR, PLATFORM_CONFIG_PATH, _LOGS_ROOT, log, _DISCOVERY_TARGET_DOWN_KW,
    _platform_config_cache, _LAST_DISCOVERY_ACCESS,
    # private helpers called directly in learn functions
    _read_platform_config_json, _invalidate_platform_config_cache,
    # core public API available for cross-calls
    detect_platform_name, load_platform_config, resolve_object,
    discover_fuse_paths, load_fuse_ram_once, get_vf_registers_in_path,
    get_register_info, categorize_register, analyze_fuse_path,
    generate_recommendations, load_discovery_cache, save_discovery_cache_edits,
    _count_active, _save_discovery_cache, _all_results_to_flat_records,
    export_discovered_registers_to_excel,
    export_scalar_modifiers_to_excel,
    export_register_change_to_excel,
    export_scalar_change_to_excel,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
_VOLT_LSB_MV: float = 1000.0 / 256  # 3.90625 mV per LSB (U1.8 / U0.8 voltage format)

# ---------------------------------------------------------------------------
# Domain keyword scoring table — used by _infer_domain_from_name().
# Name matches score 3x; description matches score 1x.
# ---------------------------------------------------------------------------
_AUTO_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    'cluster0_atom':     ['cluster0_atom', 'clust0_atom', 'c0_atom'],
    'cluster0_bigcore':  ['cluster0_bigcore', 'clust0_bigcore', 'c0_bigcore', 'cluster0_big'],
    'cluster1_atom':     ['cluster1_atom', 'clust1_atom', 'c1_atom'],
    'cluster1_bigcore':  ['cluster1_bigcore', 'clust1_bigcore', 'c1_bigcore', 'cluster1_big'],
    'de':                ['_de_vf', 'fw_fuses_de_'],
    'gt':                ['_gt_vf', 'fw_fuses_gt_', 'gt_itd', 'gt_fuse'],
    'gt_acm_vpg':        ['gt_acm_vpg', 'acm_vpg'],
    'media':             ['_media_vf', 'fw_fuses_media_', 'media_fuse'],
    'nclk':              ['_nclk_vf', 'fw_fuses_nclk_'],
    'ring':              ['_ring_vf', 'fw_fuses_ring_'],
    'sa_qclk':           ['sa_qclk', 'fw_fuses_sa_qclk'],
    'vpu':               ['_vpu_vf', 'fw_fuses_vpu_'],
    'ia':                ['fw_fuses_ia_', 'ia_p0_ratio', 'ia_min_ratio', 'ia_pn_ratio'],
}

# ---------------------------------------------------------------------------
# Regex patterns for extracting conversion hints from description text.
# Used by _parse_desc_conversion_hints().
# ---------------------------------------------------------------------------
# Frequency: returns group(1) as MHz value
_DESC_FREQ_PATTERNS: list = [
    re.compile(r'(\d+(?:\.\d+)?)\s*mhz\s*\*'),          # "100MHz * fuse_value"
    re.compile(r'in\s+(\d+(?:\.\d+)?)\s*mhz'),           # "in 100 MHz"
    re.compile(r'(\d+(?:\.\d+)?)\s*mhz\s+(?:per|bins|step|unit)'), # "100 MHz bins"
    re.compile(r'units\s+of\s+(\d+(?:\.\d+)?)\s*mhz'),  # "units of 100 MHz"
    re.compile(r'(\d+(?:\.\d+)?)\s*mhz\s+increments'),  # "100 MHz increments"
    re.compile(r'resolution\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*mhz'), # "resolution of 100 MHz"
]

# Voltage: returns group(1); patterns starting with '1' yield denominator (1/N V)
_DESC_VOLT_PATTERNS: list = [
    re.compile(r'1\s*/\s*(\d+(?:\.\d+)?)\s*[vV](?!Hz)'),        # "1/256 V" -> lsb_mv = 1000/256
    re.compile(r'multiplied\s+by\s+(\d+(?:\.\d+)?)\s*mv'),     # "multiplied by 3.9 mV"
    re.compile(r'resolution\s+of\s+~?(\d+(?:\.\d+)?)\s*mv'),   # "resolution of ~4 mV"
    re.compile(r'(\d+(?:\.\d+)?)\s*mv\s+(?:per|per lsb|lsb)'), # "3.9 mV per LSB"
    re.compile(r'(\d+(?:\.\d+)?)\s*mv/lsb'),                   # "3.9 mV/LSB"
]

# ---------------------------------------------------------------------------
# Scalar modifier patterns — (type_key, encoding, [regex_patterns])
# Used by discover_scalar_modifiers() to classify registers.
# Checked in order; first match wins.
# ---------------------------------------------------------------------------
_SCALAR_MOD_PATTERNS: list[tuple[str, str, list[str]]] = [
    # ITD cutoff / floor voltages
    ('itd_voltage',  'voltage_mv',  [r'itd_cutoff_v', r'itd_floor_v', r'itd.*_v\d*$', r'itd.*_v$']),
    # ITD slope (divisor encoded as 2^N)
    ('itd_slope',    'divisor_2n',  [r'itd_slope']),
    # ACODE minimum ratio
    ('acode_min',    'ratio_mhz',   [r'acode.*ia_min_ratio', r'acode_ia_min']),
    # P0 override / AVX / TMUL per-core deltas
    ('p0_override',  'ratio_mhz',   [r'_p0_ratio_avx', r'ia_p0_ratio_avx', r'ia_p0_ratio_tmul',
                                     r'acode.*_p0_ratio']),
    # Downbin registers (per-core power-limit ratio downbin)
    ('downbin',      'ratio_mhz',   [r'_downbin']),
    # Atom per-P0 delta ratios
    ('atom_delta',   'ratio_mhz',   [r'_atom_delta']),
    # Bigcore / MCT delta ratios
    ('mct_delta',    'ratio_mhz',   [r'_bigcore_delta']),
]

# ---------------------------------------------------------------------------
# Flatten-ratio key patterns — (flatten_key, [substring_patterns])
# Used by _flatten_key() to map a register to its flatten_freq_ratios slot.
# ---------------------------------------------------------------------------
_FLATTEN_RATIO_PATTERNS: list[tuple[str, list[str]]] = [
    ('min', ['_min_ratio', 'ia_min_ratio']),
    ('p0',  ['_p0_ratio',  'ia_p0_ratio']),
    ('p1',  ['_p1_ratio',  'ia_p1_ratio']),
    ('pn',  ['_pn_ratio',  'ia_pn_ratio']),
]

# ---------------------------------------------------------------------------
# VF field-type qualifiers — suffixes after _vf_voltage_/_vf_ratio_ that
# indicate a field variant (adder, delta) rather than a distinct sub-domain.
# Used by _extract_subdomain_key() to avoid creating spurious sub-domains.
# ---------------------------------------------------------------------------
_VF_FIELD_QUALIFIERS: frozenset[str] = frozenset({
    'reg_adder', 'adder', 'delta', 'index', 'base', 'floor', 'ceil',
    'vfloor', 'vceil', 'v_gap', 'num_of',
})


def _load_known_domains() -> tuple:
    """Load KNOWN_DOMAINS from platform_config.json, fall back to built-in list."""
    try:
        data = _read_platform_config_json()
        if 'known_domains' in data:
            return tuple(data['known_domains'])
    except Exception:
        pass
    return ('bigcore', 'atom', 'ring', 'gt', 'media', 'sa', 'io')


def _parse_register_group_key(reg_name: str, fuse_path: str) -> tuple[str, str, int] | tuple[str, Literal['vf_voltage', 'vf_ratio'], int] | tuple[str | Any, Literal['vf_voltage_adder'], int] | tuple[str | Any, Literal['vf_voltage'], int] | tuple[str | Any, Literal['vf_ratio'], int] | tuple[None, None, None]:
    """Parse a VF register name into (domain_key, field_type, index).

    Supports:
    - fw_fuses_DOMAIN_vf_voltage_N              → (DOMAIN, 'vf_voltage', N)
    - fw_fuses_DOMAIN_vf_voltage_SUFFIX_N       → (DOMAIN_SUFFIX, 'vf_voltage', N)
    - fw_fuses_DOMAIN_vf_ratio_N                → (DOMAIN, 'vf_ratio', N)
    - fw_fuses_DOMAIN_vf_ratio_SUFFIX_N         → (DOMAIN_SUFFIX, 'vf_ratio', N)
    - fw_fuses_DOMAIN_vf_voltage_reg_adder_N    → (DOMAIN, 'vf_voltage_adder', N)
    - core_fuse_..._ia_base_vf_(voltage|ratio)_N → (coreK_bigcore_base_vf, field, N)
    - core_fuse_..._ia_delta_idxK_vf_voltage_N  → (coreK_bigcore_base_vf, 'vf_voltage_delta_idxK', N)

    Returns (None, None, None) if the name doesn't match any known VF pattern.
    """
    name: str = reg_name.lower()

    # 1. Core-specific delta registers
    m: re.Match[str] | None = re.match(r'core_fuse_core_fuse_acode_ia_delta_idx(\d+)_vf_voltage_(\d+)$', name)
    if m:
        idx_variant, idx = int(m.group(1)), int(m.group(2))
        core_m: re.Match[str] | None = re.search(r'core(\d+)_fuse', fuse_path)
        core_num = core_m.group(1) if core_m else '0'
        return f'core{core_num}_bigcore_base_vf', f'vf_voltage_delta_idx{idx_variant}', idx

    # 2. Core-specific base VF registers
    m: re.Match[str] | None = re.match(r'core_fuse_core_fuse_acode_ia_base_vf_(voltage|ratio)_(\d+)$', name)
    if m:
        field: str = 'vf_voltage' if m.group(1) == 'voltage' else 'vf_ratio'
        core_m: re.Match[str] | None = re.search(r'core(\d+)_fuse', fuse_path)
        core_num = core_m.group(1) if core_m else '0'
        return f'core{core_num}_bigcore_base_vf', field, int(m.group(2))

    # 3. Adder registers — must check BEFORE generic voltage to avoid partial match
    m: re.Match[str] | None = re.match(r'fw_fuses_(.+?)_vf_voltage_reg_adder_(\d+)$', name)
    if m:
        return m.group(1), 'vf_voltage_adder', int(m.group(2))

    # 4. Generic fw_fuses voltage (optional sub-group suffix, e.g. acm_vpg)
    m: re.Match[str] | None = re.match(r'fw_fuses_(.+?)_vf_voltage(?:_([a-z][a-z0-9_]*?))?_(\d+)$', name)
    if m:
        base, suffix, idx = m.group(1), m.group(2), int(m.group(3))
        domain_key = f'{base}_{suffix}' if suffix else base
        return domain_key, 'vf_voltage', idx

    # 5. Generic fw_fuses ratio (optional sub-group suffix)
    m: re.Match[str] | None = re.match(r'fw_fuses_(.+?)_vf_ratio(?:_([a-z][a-z0-9_]*?))?_(\d+)$', name)
    if m:
        base, suffix, idx = m.group(1), m.group(2), int(m.group(3))
        domain_key = f'{base}_{suffix}' if suffix else base
        return domain_key, 'vf_ratio', idx

    return None, None, None


def _infer_freq_multiplier(domain_key: str, existing_domains: dict) -> float:
    """Infer freq_multiplier for a brand-new domain.

    Strategy:
    1. Inherit from the existing domain whose key starts with the same first token
       (e.g. 'gt_acm_vpg' inherits from 'gt').
    2. Fall back to a keyword heuristic.
    3. Default to 100 MHz.
    """
    first_token: str = domain_key.split('_')[0]
    for ex_key, ex_dom in existing_domains.items():
        if ex_key.split('_')[0] == first_token:
            return ex_dom.get('freq_multiplier', 100)
    key_lower: str = domain_key.lower()
    if 'qclk' in key_lower:
        return 33.33
    if 'de' == key_lower or key_lower.endswith('_de') or key_lower.startswith('de_'):
        return 24.0   # Display Engine: 24 MHz/ratio unit
    if any(k in key_lower for k in ('gt', 'media', 'vpu', 'nclk', 'acm', 'vpg')):
        return 50
    return 100


def _parse_desc_conversion_hints(descriptions: list) -> dict:
    """Parse hardware register descriptions to extract conversion factors.

    Returns dict with any subset of:
        'freq_multiplier'  — float, MHz per ratio unit
        'voltage_lsb_mv'   — float, mV per raw LSB

    No LLM required — Intel hardware descriptions use highly structured language
    like "ratio in units of 100 MHz" or "1/256 V per LSB".
    Returns empty dict if nothing parseable is found.
    """
    hints: dict = {}
    for desc in descriptions:
        if not desc:
            continue
        d = desc.lower()

        # ── Frequency multiplier ─────────────────────────────────────────
        if 'freq_multiplier' not in hints:
            for pat in _DESC_FREQ_PATTERNS:
                m: re.Match[str] | None = pat.search(d)
                if m:
                    try:
                        val = float(m.group(1))
                        if 1 <= val <= 10000:   # sanity: 1–10000 MHz is valid
                            hints['freq_multiplier'] = val
                            break
                    except ValueError:
                        pass

        # ── Voltage LSB ──────────────────────────────────────────────────
        if 'voltage_lsb_mv' not in hints:
            # First: Intel fixed-point U(I.F) notation — e.g. "U1.8 format"
            # means 8 fractional bits → 1/2^8 V/LSB = 3.90625 mV/LSB.
            # This is the canonical Intel VF voltage encoding and takes
            # precedence over any mV-resolution descriptions.
            m_ufmt: re.Match[str] | None = re.search(r'\bu\d+\.(\d+)\b', d)
            if m_ufmt:
                frac_bits = int(m_ufmt.group(1))
                if 4 <= frac_bits <= 12:  # sanity: 4–12 fractional bits valid
                    hints['voltage_lsb_mv'] = round(1000.0 / (2 ** frac_bits), 6)
            else:
                for pat in _DESC_VOLT_PATTERNS:
                    m: re.Match[str] | None = pat.search(d)
                    if m:
                        try:
                            raw = float(m.group(1))
                            if pat.pattern.startswith('1'):
                                # Matched "1/N V" pattern — convert to mV/LSB
                                lsb_mv: float = 1000.0 / raw
                            else:
                                lsb_mv: float = raw
                            if 0.1 <= lsb_mv <= 100:  # sanity: 0.1–100 mV/LSB
                                hints['voltage_lsb_mv'] = round(lsb_mv, 6)
                                break
                        except (ValueError, ZeroDivisionError):
                            pass

        if len(hints) == 2:
            break  # both found, stop scanning
    return hints


def _generate_domain_label(domain_key: str) -> str:
    """Convert a domain key to a human-readable label.

    Known short abbreviations are uppercased; everything else is title-cased.
    """
    _ACRONYMS: set[str] = {'gt', 'vpu', 'de', 'sa', 'io', 'acm', 'vpg', 'ia',
                 'vf', 'nclk', 'qclk', 'ipu', 'mfx'}
    parts: list[str] = domain_key.replace('_', ' ').split()
    return ' '.join(p.upper() if p in _ACRONYMS else p.capitalize() for p in parts)


def auto_merge_to_vf_domains(all_path_results: dict, cfg: dict) -> int:
    """Step 6.5 — Automatically merge newly discovered VF curve registers into
    vf_domains.json so the tool is always up to date after a discovery run.

    .. deprecated::
        Use :func:`build_vf_domains_from_discovery` instead.
        This function uses an incremental merge strategy that can leave stale
        entries.  ``build_vf_domains_from_discovery`` performs a full rebuild
        from the discovery snapshot and is the authoritative path.

    Rules applied:
    - Active (non-zero) registers in the 'vf_curve' category only.
    - Registers already listed in vf_domains.json are skipped.
    - Existing domain + new optional field (adder / delta) → field is appended.
    - New domain (both voltage AND ratio found) → full domain entry is created.
    - Incomplete groups (voltage without ratio, or vice-versa) are skipped.
    - Original vf_domains.json is backed up as vf_domains.json.bak before writing.

    Returns the number of changes (new domains + new fields) applied.
    """
    vf_domains_path: Path = SCRIPT_DIR / 'vf_domains.json'
    if not vf_domains_path.exists():
        log.error(f"    [!] vf_domains.json not found — skipping auto-merge")
        return 0

    with open(vf_domains_path, 'r', encoding='utf-8') as f:
        vf_data = json.load(f)
    existing_domains = vf_data['domains']

    # Build a flat set of every register name already in vf_domains.json
    known_registers: set = set()
    for dom_cfg in existing_domains.values():
        for field in ('vf_voltage', 'vf_ratio', 'vf_voltage_adder',
                      'vf_voltage_delta_idx1', 'vf_voltage_delta_idx2'):
            known_registers.update(dom_cfg.get(field, []))

    # -----------------------------------------------------------------------
    # Phase 1: group all NEW active vf_curve registers by (domain_key, field_type)
    # Structure: {domain_key: {'_fuse_path': str, field_type: {idx: reg_name}}}
    # -----------------------------------------------------------------------
    discovered: dict = {}

    # Per-domain sequential counters used by the name-based fallback path so
    # indices stay monotonically increasing across registers in the same domain.
    _fallback_idx: dict = {}

    for fuse_path, results_by_cat in all_path_results.items():
        for reg_info in results_by_cat.get('vf_curve', []):
            if not reg_info['active']:
                continue
            if reg_info['name'] in known_registers:
                continue  # already integrated

            # --- Primary: regex-based structured name parsing ---
            domain_key, field_type, idx = _parse_register_group_key(
                reg_info['name'], fuse_path)

            # --- Fallback: use categorize_register() results when the name
            #     doesn't match a known fw_fuses_* / core_fuse_* pattern.
            #     Applies to ring, sa, gt, media etc. on platforms (e.g.
            #     Novalake) whose register names differ from fw_fuses_DOMAIN_*.
            if domain_key is None:
                inferred_domain = reg_info.get('domain', 'unknown')
                if inferred_domain == 'unknown':
                    continue  # truly unclassifiable — skip

                name_lower = reg_info['name'].lower()
                if 'ratio' in name_lower:
                    field_type = 'vf_ratio'
                elif 'voltage' in name_lower or 'volt' in name_lower:
                    field_type = 'vf_voltage'
                elif 'adder' in name_lower:
                    field_type = 'vf_voltage_adder'
                else:
                    # Can't tell — skip to avoid corrupting the domain entry
                    continue

                domain_key = inferred_domain
                # Assign a sequential index within this domain+field bucket
                bucket: str = f'{domain_key}:{field_type}'
                idx = _fallback_idx.get(bucket, 0)
                _fallback_idx[bucket] = idx + 1

            if domain_key not in discovered:
                discovered[domain_key] = {'_fuse_path': fuse_path}
            if field_type not in discovered[domain_key]:
                discovered[domain_key][field_type] = {}
            discovered[domain_key][field_type][idx] = reg_info['name']

    if not discovered:
        log.info(f"    [+] vf_domains.json is already up to date — no new registers found")
        return 0

    # -----------------------------------------------------------------------
    # Phase 2: merge into existing domains or create new entries
    # -----------------------------------------------------------------------
    changes = 0
    new_domains_log: list = []
    updated_fields_log: list = []

    for domain_key, group in discovered.items():
        fuse_path = group['_fuse_path']

        if domain_key in existing_domains:
            # Domain exists — only add genuinely new optional fields
            dom = existing_domains[domain_key]
            for field_type in ('vf_voltage_adder',
                               'vf_voltage_delta_idx1',
                               'vf_voltage_delta_idx2'):
                if field_type in group and field_type not in dom:
                    sorted_regs = [group[field_type][i]
                                   for i in sorted(group[field_type])]
                    dom[field_type] = sorted_regs
                    changes += 1
                    updated_fields_log.append(
                        f'{domain_key}.{field_type} ({len(sorted_regs)} regs)')
        else:
            # Brand-new domain — requires both voltage AND ratio registers
            if 'vf_voltage' not in group or 'vf_ratio' not in group:
                log.error(f"    [!] Skipping '{domain_key}': missing voltage or ratio registers")
                continue
            vol_regs   = [group['vf_voltage'][i]  for i in sorted(group['vf_voltage'])]
            ratio_regs = [group['vf_ratio'][i]    for i in sorted(group['vf_ratio'])]
            if len(vol_regs) != len(ratio_regs):
                log.error(f"    [!] Skipping '{domain_key}': "
                      f"voltage ({len(vol_regs)}) / ratio ({len(ratio_regs)}) count mismatch")
                continue

            wp_count: int   = len(vol_regs)
            # 3-tier priority: spec_hints > keyword_heuristic (no desc available here)
            _spec_conv = cfg.get('spec_conversion_hints', {})
            freq_mult  = (
                _spec_conv.get(domain_key, {}).get('freq_multiplier')
                or _infer_freq_multiplier(domain_key, existing_domains)
            )
            fuse_ram: str   = '.'.join(fuse_path.split('.')[:-1])  # drop last component
            label: str      = _generate_domain_label(domain_key)

            new_entry: dict = {
                'label':            label,
                'freq_multiplier':  freq_mult,
                'wp_count':         wp_count,
                'fuse_path':        fuse_path,
                'fuse_ram_path':    fuse_ram,
                'vf_voltage':       vol_regs,
                'vf_ratio':         ratio_regs,
            }
            for field_type in ('vf_voltage_adder',
                               'vf_voltage_delta_idx1',
                               'vf_voltage_delta_idx2'):
                if field_type in group:
                    new_entry[field_type] = [group[field_type][i]
                                             for i in sorted(group[field_type])]

            existing_domains[domain_key] = new_entry
            changes += 1
            new_domains_log.append(
                f'{domain_key}  ({wp_count} WP | {freq_mult} MHz mult | {fuse_path})')

    if changes == 0:
        log.info(f"    [+] vf_domains.json is already up to date")
        return 0

    # -----------------------------------------------------------------------
    # Phase 3: backup + write
    # -----------------------------------------------------------------------
    backup_path: Path = vf_domains_path.with_suffix('.json.bak')
    shutil.copy2(vf_domains_path, backup_path)

    # Stamp the platform so startup can do a fast cross-check on next boot
    # without needing to probe every domain via ITP.
    try:
        vf_data['_platform']         = detect_platform_name().lower()
        vf_data['_platform_updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass

    with open(vf_domains_path, 'w', encoding='utf-8') as f:
        json.dump(vf_data, f, indent=2)

    if new_domains_log:
        log.info(f"    [+] Added {len(new_domains_log)} new domain(s):")
        for entry in new_domains_log:
            log.info(f"        + {entry}")
    if updated_fields_log:
        log.info(f"    [+] Added {len(updated_fields_log)} new field(s) to existing domains:")
        for entry in updated_fields_log:
            log.info(f"        + {entry}")
    log.info(f"    [+] vf_domains.json updated  ({len(existing_domains)} total domains)")
    log.info(f"    [+] Backup saved to: {backup_path.name}")
    return changes


def _infer_domain_from_name(reg_name: str, description: str) -> str:
    """Score a register name + description against _AUTO_DOMAIN_KEYWORDS.
    Name matches score 3x higher than description matches.
    Returns the highest-scoring domain, or 'unknown' if no match.
    """
    name_lower: str = reg_name.lower()
    desc_lower: str = (description or '').lower()
    scores = defaultdict(int)
    for domain, keywords in _AUTO_DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                scores[domain] += 3
            if kw in desc_lower:
                scores[domain] += 1
    if not scores:
        return 'unknown'
    return max(scores, key=scores.get)


def _extract_pattern(reg_name: str) -> str:
    """Extract a reusable matching pattern from a register name.

    Strips trailing numeric suffix so the pattern covers the whole family:
      fw_fuses_config_tdp_ratio_1  ->  fw_fuses_config_tdp_ratio
      fw_fuses_tdp_power_of_sku    ->  fw_fuses_tdp_power_of_sku
      dlvr_vrcivrdfxovr_target_v   ->  dlvr_vrcivrdfxovr_target_v
    """
    return re.sub(r'_\d+$', '', reg_name.lower())


def _save_learned_patterns(platform_name: str, domain_patterns: dict,
                           cfg: dict = None) -> None:
    """Persist updated domain_patterns for platform_name back to platform_config.json.

    If platform_name is not yet in the JSON (brand-new platform), a full entry is
    created automatically by cloning the generic entry and applying the learned
    patterns.  When cfg is supplied, any live-probed values (e.g. a fuse_root that
    was auto-detected at runtime) overwrite the cloned generic defaults, so the
    second run on that platform is fully self-sufficient.
    """
    try:
        with open(PLATFORM_CONFIG_PATH, 'r', encoding='utf-8') as f:
            all_configs = json.load(f)

        platforms = all_configs.setdefault('platforms', {})

        if platform_name in platforms:
            platforms[platform_name]['domain_patterns'] = domain_patterns
            # Also persist any runtime-discovered infra values (e.g. probed fuse_root)
            if cfg:
                for key in ('fuse_root', 'core_fuse_pattern',
                            'system_fuse_names', 'extra_fuse_names'):
                    if key in cfg:
                        platforms[platform_name][key] = cfg[key]
        else:
            # Brand-new platform — clone generic structure, apply learned patterns
            new_entry = copy.deepcopy(platforms.get('generic', {}))
            new_entry['display_name'] = f"{platform_name.title()} (Auto-discovered)"
            new_entry['domain_patterns'] = domain_patterns
            new_entry['_comment'] = (
                "Auto-generated on first run — review and refine as needed"
            )
            # Overwrite any generic defaults with actual runtime-discovered values
            # so the new entry is immediately correct for this platform.
            if cfg:
                for key in ('fuse_root', 'core_fuse_pattern',
                            'system_fuse_names', 'extra_fuse_names'):
                    if key in cfg:
                        new_entry[key] = cfg[key]
            platforms[platform_name] = new_entry
            log.info(f"    [+] Created new platform entry '{platform_name}' in platform_config.json")

        with open(PLATFORM_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(all_configs, f, indent=2)
        _invalidate_platform_config_cache()
    except Exception as e:
        log.error(f"    [!] Could not save learned patterns: {e}")


def auto_learn_unknown_patterns(all_path_results: dict, platform_name: str, cfg: dict) -> int:
    """Auto-classify all unknown-domain registers using name/description scoring.

    Workflow:
      1. Collect every register with domain='unknown' from all_path_results
      2. Infer domain using _AUTO_DOMAIN_KEYWORDS scoring
      3. Extract a reusable pattern from each register name
      4. Update cfg['domain_patterns'] in memory (affects this run's output)
      5. Update the domain field in-place in all_path_results (affects report/recs)
      6. Persist new patterns to platform_config.json (zero unknowns on future runs)

    Returns the number of registers newly classified.
    """
    # Only vf_curve registers need domain assignment — scalar/frequency/itd
    # registers (itd_voltage, frequency, p0_override …) legitimately have no
    # VF domain and cannot be scored by _AUTO_DOMAIN_KEYWORDS.  Including
    # them would always trigger the "could not infer" warning falsely.
    unknowns = [
        reg
        for results_by_cat in all_path_results.values()
        for category, registers in results_by_cat.items()
        for reg in registers
        if category == 'vf_curve' and reg.get('domain') == 'unknown'
    ]

    if not unknowns:
        return 0

    log.info(f"\n[*] Step 4.5: Auto-learning {len(unknowns)} unknown vf_curve register(s)...")

    domain_to_new_patterns: dict = defaultdict(set)
    newly_classified = 0

    for reg in unknowns:
        inferred: str = _infer_domain_from_name(reg['name'], reg.get('description') or '')
        if inferred != 'unknown':
            reg['domain'] = inferred              # update in-place
            pattern: str = _extract_pattern(reg['name'])
            domain_to_new_patterns[inferred].add(pattern)
            newly_classified += 1

    if not domain_to_new_patterns:
        log.error(f"    [!] Could not infer domain for any unknown registers — manual review needed")
        return 0

    # Merge new patterns into cfg (in memory) and report additions
    for domain, new_patterns in domain_to_new_patterns.items():
        existing = cfg.setdefault('domain_patterns', {}).get(domain, [])
        added = [p for p in sorted(new_patterns) if p not in existing]
        if added:
            cfg['domain_patterns'][domain] = existing + added
            log.info(f"    [+] {domain}: +{len(added)} pattern(s): {added}")

    # Persist to JSON (pass cfg so any runtime-probed fuse_root is saved too)
    _save_learned_patterns(platform_name, cfg['domain_patterns'], cfg)

    remaining: int = len(unknowns) - newly_classified
    log.info(f"    [+] {newly_classified}/{len(unknowns)} classified  |  {remaining} still unknown")
    if newly_classified:
        log.info(f"    [+] Patterns saved to platform_config.json — zero unknowns on future runs")
    return newly_classified


def _infer_conversion_from_description(reg_name: str, description: str, value) -> str:
    """Infer a human-readable physical value from register description text.

    Patterns parsed from description:
      'Frequency is NNMHz * fuse_value'  -> value * NN MHz
      'in NN MHz' / 'NN MHz bins/units'  -> value * NN MHz
      '100Mhz Units for IA/RING'         -> value * 100 MHz
      'multiplied by N.N mv'             -> value * N.N mV
      'resolution of ~NmV'               -> value * N mV  (adder)
      'Resolution: 1/N watt'             -> value / N W

    Returns '' when no conversion can be inferred.
    """
    if value is None:
        return ''
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return ''

    dl: str = (description or '').lower()

    # 1 -- Frequency: "NNMHz * fuse_value" or "NN.NN MHz * fuse_value"
    m: re.Match[str] | None = re.search(r'(\d+(?:\.\d+)?)\s*mhz\s*\*\s*fuse.?value', dl)
    if m:
        mult = float(m.group(1))
        return f'{raw * mult:g} MHz'

    # 2 -- Frequency: "in NN MHz" / "characterizing ratio in NNMhz"
    m: re.Match[str] | None = re.search(r'(?:in|characterizing ratio in)\s*(\d+(?:\.\d+)?)\s*mhz', dl)
    if m:
        mult = float(m.group(1))
        return f'{raw * mult:g} MHz'

    # 3 -- Frequency: "NN MHz bins" / "NN MHz units" (e.g. GT/media 50 MHz bins)
    m: re.Match[str] | None = re.search(r'(\d+(?:\.\d+)?)\s*mhz\s+(?:bins?|units?)', dl)
    if m:
        mult = float(m.group(1))
        return f'{raw * mult:g} MHz'

    # 4 -- Voltage: "multiplied by N.N mv" (absolute encoding)
    m: re.Match[str] | None = re.search(r'multiplied by\s+(\d+(?:\.\d+)?)\s*m[vV]', description or '', re.IGNORECASE)
    if m:
        mult = float(m.group(1))
        return f'{round(raw * mult)} mV'

    # 5 -- Voltage adder: "resolution of ~NmV" or "resolution of ~N mV"
    m: re.Match[str] | None = re.search(r'resolution\s+of\s+~?(\d+(?:\.\d+)?)\s*m[vV]', description or '', re.IGNORECASE)
    if m:
        mult = float(m.group(1))
        return f'~{round(raw * mult)} mV'

    # 6 -- Power: "Resolution: 1/N watt"
    m: re.Match[str] | None = re.search(r'1/(\d+)\s*watt', dl)
    if m:
        div = float(m.group(1))
        return f'{raw / div:.3g} W'

    # 7 -- VF voltage register: U1.8 format = 3.90625 mV/LSB (universal Intel)
    # Only for base working-point voltage fuses — exclude adders/deltas which
    # have different encodings and are already handled by patterns 4 & 5 above.
    name_l: str = reg_name.lower()
    is_vf_volt: bool = ('_vf_voltage' in name_l or 'vf_voltage_' in name_l)
    is_adder_or_delta: bool = ('adder' in name_l or 'delta' in name_l)
    if is_vf_volt and not is_adder_or_delta:
        return f'{round(raw * _VOLT_LSB_MV, 2)} mV'

    # 8 -- VF ratio register: freq = raw * freq_multiplier MHz
    # freq_multiplier inferred from domain keyword embedded in register name
    if '_vf_ratio' in name_l or 'vf_ratio_' in name_l:
        m2: re.Match[str] | None = re.match(r'^fw_fuses_(.+?)_vf_ratio', name_l)
        if m2:
            subdomain = m2.group(1)          # e.g. 'cluster0_bigcore', 'de', 'gt'
        else:
            # core_fuse_* or other naming — strip prefix and trailing index
            subdomain: str = re.sub(r'^(fw_fuses_|core_fuse_)', '', name_l)
            subdomain: str = subdomain.split('_vf_ratio')[0]
        subdomain: str = re.sub(r'_\d+$', '', subdomain)  # strip trailing index
        mult: float = _infer_freq_multiplier(subdomain, {})
        return f'{raw * mult:g} MHz'

    # 9 -- Voltage: Intel U1.8 format declared in description text
    # "The fuse is in U9.1.8 volts" (9-bit size + U1.8 encoding) or plain "U1.8"
    # Confirmed by Intel ITD HAS spec: precision=U1.8, resolution = 1/256 V = 3.90625 mV/LSB
    # Covers itd_cutoff_v, itd_cutoff_v2, reference_voltage, and any future U1.8 fuses.
    if re.search(r'\bu\d*\.?1\.8\b', dl):
        return f'{round(raw * _VOLT_LSB_MV, 2)} mV'

    # 10 -- Voltage: Intel U0.8 format (8-bit, no integer part)
    # "precision=U0.8" — used by {IP_NAME}_ITD_FLOOR_V
    # Same LSB as U1.8 (1/256 V = 3.90625 mV), range is 0‒0.996 V
    if re.search(r'\bu0\.8\b', dl):
        return f'{round(raw * _VOLT_LSB_MV, 2)} mV'

    # 11 -- Generic 2^N divisor in description (e.g. slope or ratio fuses)
    # "V per 1'C = fuse_value/(2^12)"  →  raw / 4096
    # "denominator is (2^13)"          →  raw / 8192
    # Returns a dimensionless ratio string; unit prefix is kept from description if detectable.
    m: re.Match[str] | None = re.search(r'/\s*\(?\s*2\s*\^\s*(\d+)\s*\)?', dl)
    if m:
        divisor = 2 ** int(m.group(1))
        scaled = raw / divisor
        # Detect unit hint in description
        if 'v/' in dl or 'volt' in dl:
            return f'{scaled:.6f} V/°C'
        elif '1/' in dl or '1 /' in dl:
            return f'{scaled:.6f} /°C'
        else:
            return f'{scaled:.6f}'

    return ''


def _generate_scalar_label(reg_name: str, type_key: str) -> str:
    """Generate a human-readable label for a scalar modifier register."""
    clean: str = re.sub(r'^(fw_fuses_|core_fuse_core_fuse_acode_|core_fuse_)', '',
                   reg_name.lower())
    clean: str = re.sub(r'_', ' ', clean).title()
    prefix_map: dict[str, str] = {
        'p0_override': 'P0',
        'itd_voltage': 'ITD',
        'itd_slope':   'ITD',
        'downbin':     'Downbin',
        'mct_delta':   'MCT',
        'atom_delta':  'Atom Delta',
        'acode_min':   '',
    }
    prefix: str = prefix_map.get(type_key, '')
    if prefix and not clean.upper().startswith(prefix.upper()):
        clean: str = f"{prefix} {clean}"
    return clean.strip()


def auto_discover_scalar_modifiers(all_path_results: dict, cfg: dict) -> int:
    """Discover scalar modifier registers and persist them to vf_domains.json.

    Scalar modifiers are single-value fuse registers that act as overrides or
    corrections on top of VF curves.  Discovered types:

      p0_override  — per-use-case P0 ratio (AVX2, AVX-512, TMUL, AMX, …)
      itd_voltage  — ITD cutoff voltage thresholds
      itd_slope    — ITD slope / coefficient registers
      downbin      — non-FCT core P0 ratio downbin corrections
      mct_delta    — multi-core P0 scaling deltas (2/3/4 active bigcores)
      atom_delta   — atom IA P0 delta
      acode_min    — per-core ACODE minimum ratio

    Results are written to vf_domains.json under the key ``scalar_modifiers``.
    Each entry is keyed by the canonical register name so repeated discovery
    runs are idempotent.

    Returns the number of scalar modifiers written (0 = nothing new found).
    """
    vf_domains_path: Path = SCRIPT_DIR / 'vf_domains.json'
    if not vf_domains_path.exists():
        log.error("    [!] auto_discover_scalar_modifiers: vf_domains.json not found")
        return 0

    with open(vf_domains_path, 'r', encoding='utf-8') as f:
        vf_data = json.load(f)

    existing_domains = vf_data.get('domains', {})
    scalars: dict = {}

    # Iterate ALL categories — the _SCALAR_MOD_PATTERNS matching below is the
    # real filter.  With the refactored VF_KEYWORDS every scalar type now lives
    # in its own category key ('itd_voltage', 'p0_override', 'downbin', …) so a
    # hard-coded whitelist would silently skip them.
    for fuse_path, results_by_cat in all_path_results.items():
        for category, reg_list in results_by_cat.items():
            for reg_info in reg_list:
                if not reg_info.get('accessible'):
                    continue

                name   = reg_info['name']
                name_l = name.lower()

                # Skip if already modelled as a VF array register (vf_voltage / vf_ratio etc.)
                is_vf_array: bool = any(
                    name in dom.get(f, [])
                    for dom in existing_domains.values()
                    for f in ('vf_voltage', 'vf_ratio',
                              'vf_voltage_adder',
                              'vf_voltage_delta_idx1',
                              'vf_voltage_delta_idx2')
                )
                if is_vf_array:
                    continue

                # Skip if already in flatten_freq_ratios of any domain
                is_flatten: bool = any(
                    name in dom.get('flatten_freq_ratios', {}).values()
                    for dom in existing_domains.values()
                )
                if is_flatten:
                    continue

                # Match against scalar patterns
                matched_type     = None
                matched_encoding = None
                for type_key, encoding, patterns in _SCALAR_MOD_PATTERNS:
                    if any(re.search(p, name_l) for p in patterns):
                        matched_type: str     = type_key
                        matched_encoding: str = encoding
                        break

                if matched_type is None:
                    continue

                # Infer conversion factors from description text
                desc  = reg_info.get('description') or ''
                hints = _parse_desc_conversion_hints([desc])

                freq_mult = 100.0
                if matched_encoding == 'ratio_mhz':
                    # Derive sub-domain token for multiplier inference
                    sdk: str = re.sub(r'^[a-z_]*fuse[s]?_', '', name_l)
                    sdk: str = re.sub(
                        r'(_p0_ratio.*|_min_ratio.*|_pn_ratio.*|_p1_ratio.*|_downbin.*|_delta.*)$',
                        '', sdk
                    ).split('_')[0]
                    freq_mult = hints.get('freq_multiplier') or _infer_freq_multiplier(
                        sdk, existing_domains)

                lsb_mv = hints.get('voltage_lsb_mv', 3.90625)

                # Build scalar entry (keyed by register name — deduplication)
                if name not in scalars:
                    entry: dict = {
                        'type':      matched_type,
                        'label':     _generate_scalar_label(name, matched_type),
                        'fuse_path': fuse_path,
                        'register':  name,
                        'encoding':  matched_encoding,
                    }
                    if matched_encoding == 'ratio_mhz':
                        entry['freq_multiplier'] = float(freq_mult)
                    else:
                        entry['voltage_lsb_mv'] = float(lsb_mv)
                    if desc:
                        entry['description'] = desc[:250]
                    scalars[name] = entry

    if not scalars:
        log.info("    [+] No scalar modifiers discovered")
        return 0

    vf_data['scalar_modifiers'] = scalars
    with open(vf_domains_path, 'w', encoding='utf-8') as f:
        json.dump(vf_data, f, indent=2)

    log.info(f"    [+] Discovered {len(scalars)} scalar modifier(s) → vf_domains.json")
    by_type: dict = {}
    for entry in scalars.values():
        by_type.setdefault(entry['type'], []).append(entry['register'])
    for t, regs in sorted(by_type.items()):
        log.info(f"        [{t}]  {len(regs)} register(s)")
    return len(scalars)


def _flatten_key(reg_name: str) -> str | None:
    """Return the flatten_freq_ratios key for a frequency register, or None."""
    name: str = reg_name.lower()
    for key, pats in _FLATTEN_RATIO_PATTERNS:
        if any(p in name for p in pats):
            return key
    return None


def _extract_subdomain_key(reg_name: str, container: str) -> str | None:
    """Extract a fine-grained sub-domain key from a register name + container.

    This replaces the broad categorize_register() semantic grouping so that
    every distinct functional sub-group in a container gets its own domain
    entry.  Examples (WildcatLake punit_fuses):

        fw_fuses_cluster0_bigcore_vf_voltage_0   -> 'cluster0_bigcore'
        fw_fuses_cluster1_atom_vf_voltage_0      -> 'cluster1_atom'
        fw_fuses_de_vf_voltage_0                 -> 'de'
        fw_fuses_nclk_vf_voltage_0               -> 'nclk'
        fw_fuses_sa_qclk_vf_voltage_0            -> 'sa_qclk'
        fw_fuses_gt_vf_voltage_acm_vpg_0         -> 'gt_acm_vpg'
        fw_fuses_media_vf_voltage_0              -> 'media'
        fw_fuses_media_vf_voltage_reg_adder_0    -> 'media'  (field-type, not sub-group)
        fw_fuses_vpu_vf_voltage_0                -> 'vpu'
        fw_fuses_ring_vf_voltage_0               -> 'ring'
        core_fuse_...ia_base...  (core0_fuse)    -> 'core0_bigcore_base_vf'
        core_fuse_...ia_delta... (core1_fuse)    -> 'core1_bigcore_base_vf'
    """
    name: str = reg_name.lower()

    # ── core_fuse registers — checked FIRST so the generic fuse pattern
    # below cannot accidentally extract 'acode_ia_delta_idxN' as a
    # subdomain (which then has no bare vf_voltage entries and gets skipped).
    #
    # Detection is container-based rather than prefix-based because the
    # actual hardware attribute names vary by platform — on WildcatLake the
    # registers inside core0_fuse are named fw_fuses_acode_ia_*_vf_voltage_N,
    # not core_fuse_acode_ia_*.  Using the container + content keywords is
    # the only reliable way to group all acode/ia variants together:
    #
    #   fw_fuses_acode_ia_base_vf_voltage_0   (core0_fuse) → core0_bigcore_base_vf
    #   fw_fuses_acode_ia_delta_idx1_vf_voltage_0           → core0_bigcore_base_vf
    #   fw_fuses_acode_ia_delta_idx4_vf_voltage_0           → core0_bigcore_base_vf
    _cm: re.Match[str] | None = re.search(r'core(\d+)', container)
    if _cm and 'acode' in name and 'ia_' in name:
        return f"core{_cm.group(1)}_bigcore_base_vf"

    # ── Any platform fuse prefix: fw_fuses_, dmu_fuse_, punit_fuse_, etc. ─
    # Pattern: {prefix}_{subdomain}_vf_{voltage|ratio}[_{qualifier}]_{index}
    m: re.Match[str] | None = re.match(r'^[a-z_]*fuse[s]?_(.+?)_vf_(voltage|ratio)', name)
    if m:
        subdomain = m.group(1)   # e.g. cluster0_bigcore, de, gt, media
        # What follows _vf_voltage_ / _vf_ratio_?
        rest: str = name[m.end():].lstrip('_')  # e.g. '0', 'acm_vpg_0', 'reg_adder_0'
        qm: re.Match[str] | None = re.match(r'^([a-z][a-z0-9_]+)_\d+$', rest)
        if qm:
            qualifier = qm.group(1)
            if qualifier not in _VF_FIELD_QUALIFIERS:
                # Non-field-type qualifier = a distinct sub-group
                return f"{subdomain}_{qualifier}"
        return subdomain

    return None


def _vf_field_type(reg_name: str) -> str | None:
    """Infer the vf_domains field type from a register name.

    Returns one of: 'vf_voltage', 'vf_ratio', 'vf_voltage_adder',
    'vf_voltage_delta_idx{N}', or None (unrecognised).
    """
    name: str = reg_name.lower()
    if 'adder' in name:
        return 'vf_voltage_adder'
    m: re.Match[str] | None = re.search(r'delta_idx(\d+)', name)
    if m:
        return f'vf_voltage_delta_idx{m.group(1)}'
    if 'ratio' in name:
        return 'vf_ratio'
    if 'voltage' in name or 'volt' in name:
        # reference_voltage is a scalar baseline, not a working-point entry
        if 'reference_voltage' in name:
            return None
        return 'vf_voltage'
    # core_fuse acode ia_base registers are baseline voltage fuse values
    # (name: core_fuse_core_fuse_acode_ia_base_N) — no 'voltage' token
    if 'acode' in name and 'ia_base' in name:
        return 'vf_voltage'
    return None


def _reg_sort_index(reg_name: str) -> int:
    """Extract the trailing integer index from a register name for sorting."""
    m: re.Match[str] | None = re.search(r'(\d+)$', reg_name)
    return int(m.group(1)) if m else 0


def build_vf_domains_from_discovery(all_path_results: dict, cfg: dict) -> int:
    """Build a complete, fresh vf_domains.json entirely from discovered registers.

    This is the source-of-truth approach: whatever the live hardware exposes
    is exactly what the tool works with.  Unlike the incremental merge in
    auto_merge_to_vf_domains(), this function:

      • Writes a COMPLETE new vf_domains.json on every discovery run.
      • Requires no prior knowledge of register naming conventions.
      • Extracts all fields the VF operations need:
          vf_voltage[]            — voltage working-point registers
          vf_ratio[]              — frequency-ratio working-point registers
          vf_voltage_adder[]      — optional per-core voltage adder registers
          vf_voltage_delta_idxN[] — optional per-core delta registers
          flatten_freq_ratios{}   — P0 / P1 / Pn / min / max ratio registers
          wp_count                — length of vf_voltage list (or live num_of_points)
          freq_multiplier         — inferred from domain type (MHz per ratio unit)
          fuse_path               — specific container path (e.g. cdie.fuses.punit_fuses)
          fuse_ram_path           — parent path for load_fuse_ram (e.g. cdie.fuses)
          label                   — human-readable domain name

    Field mapping from discovery categories:

      'vf_curve'     + 'voltage'/'volt' in name   → vf_voltage
      'vf_curve'     + 'ratio' in name            → vf_ratio (working points only)
      'vf_curve'     + 'adder' in name            → vf_voltage_adder
      'vf_curve'     + 'delta_idxN' in name       → vf_voltage_delta_idxN
      'frequency'    + p0/p1/pn/min/max_ratio     → flatten_freq_ratios key
      'curve_config' + 'num_of_points' in name    → wp_count override
                                                     (uses live hardware value)

    IA-level frequency ratios (containing _ia_) are shared to BOTH bigcore
    and atom domain entries because P0/P1/Pn at the IA level govern both.

    Returns the number of domain entries written (0 on failure).
    """
    vf_domains_path: Path = SCRIPT_DIR / 'vf_domains.json'

    # ── Step 1: collect all registers grouped by (fuse_path, subdomain_key) ─
    # Grouping key is derived from the register name prefix (the part before
    # _vf_voltage / _vf_ratio) so that every distinct functional sub-group
    # gets its own entry — e.g. cluster0_bigcore, cluster1_atom, de, nclk,
    # sa_qclk, gt, gt_acm_vpg, vpu, core0_bigcore_base_vf … are all separate.
    domain_map: dict = {}

    # Separate bucket for frequency registers (distributed as flatten_freq_ratios
    # in Step 2 — only fw_fuses_ia_* from punit_fuses get distributed)
    freq_regs: list = []   # [(flatten_key, reg_name, fuse_path)]

    # wp_count overrides sourced from live num_of_points register values
    wp_count_overrides: dict = {}  # (fuse_path, subdomain_key) -> int

    for fuse_path, results_by_cat in all_path_results.items():
        # Container name — last component of the path (e.g. 'punit_fuses', 'core0_fuse')
        container = fuse_path.split('.')[-1]

        # ── vf_curve registers ───────────────────────────────────────────
        for reg_info in results_by_cat.get('vf_curve', []):
            if not reg_info.get('accessible'):
                continue
            subdomain_key: str | None = _extract_subdomain_key(reg_info['name'], container)
            if subdomain_key is None:
                continue
            ft: str | None = _vf_field_type(reg_info['name'])
            if ft is None:
                continue

            # Key = (fuse_path, subdomain_key) — each fine-grained sub-group
            # in each container becomes its own domain entry.
            key = (fuse_path, subdomain_key)
            if key not in domain_map:
                domain_map[key] = {'_fuse_path': fuse_path,
                                   '_container': container,
                                   '_subdomain': subdomain_key,
                                   '_descriptions': []}
            bucket = domain_map[key]
            # Collect description for conversion hint parsing.
            # Only index descriptions from core VF registers (vf_voltage, vf_ratio).
            # Adder register descriptions say "~4mV resolution" which would
            # incorrectly override the universal U1.8 (3.90625 mV/LSB) encoding
            # of main VF_VOLTAGE registers if mixed into the same pool.
            if ft in ('vf_voltage', 'vf_ratio'):
                desc = reg_info.get('description')
                if desc and desc not in bucket['_descriptions']:
                    bucket['_descriptions'].append(desc)
            if ft not in bucket:
                bucket[ft] = {}
            idx: int = _reg_sort_index(reg_info['name'])
            while idx in bucket[ft]:
                idx += 1
            bucket[ft][idx] = reg_info['name']

        # ── frequency registers → flatten_freq_ratios ────────────────────
        # Only collect fw_fuses_* frequency registers — core_fuse frequency
        # registers are intentionally excluded (core_fuse domains have no
        # flatten_freq_ratios in the reference schema).
        for reg_info in results_by_cat.get('frequency', []):
            if not reg_info.get('accessible'):
                continue
            rname = reg_info['name'].lower()
            if rname.startswith('core_fuse_'):
                continue  # skip core_fuse freq regs — not used in flatten_freq_ratios
            # Exclude per-core downbin deltas and TRL group deltas — these are
            # correction offsets, not standalone flat frequency registers.
            # Examples: fw_fuses_ccp_N_ia_p0_ratio_downbin
            #           fw_fuses_ia_p0_ratio_group0_atom_delta
            if '_downbin' in rname or ('_ratio_group' in rname and '_delta' in rname):
                continue
            fk: str | None = _flatten_key(reg_info['name'])
            if fk is None:
                continue
            freq_regs.append((fk, reg_info['name'], fuse_path))

        # ── curve_config — wp_count from live num_of_points ──────────────
        for reg_info in results_by_cat.get('curve_config', []):
            if 'num_of_points' in reg_info['name'].lower():
                subdomain_key: str | None = _extract_subdomain_key(reg_info['name'], container)
                val = reg_info.get('value')
                if subdomain_key and val is not None and int(val) > 0:
                    key = (fuse_path, subdomain_key)
                    wp_count_overrides[key] = int(val)

    if not domain_map:
        log.error("    [!] build_vf_domains_from_discovery: no classifiable registers found")
        return 0

    # ── Step 2: distribute flatten_freq_ratios to domain entries ─────────
    # Rules:
    #
    #   A) fw_fuses_ia_* registers (IA-level, e.g. WCL punit_fuses / NVL dmu_fuse):
    #      → assigned to ALL bigcore sub-domains in any SYSTEM-LEVEL fuse container
    #        (i.e. containers that are NOT a per-core coreN_fuse)
    #
    #   B) fw_fuses_atom_* registers (Atom-level, NVL dmu_fuse):
    #      → assigned to ALL atom sub-domains in any system-level fuse container
    #
    #   C) Domain-specific fw_fuses_* freq regs (ring_p0_ratio, gt_p0_ratio …):
    #      → assigned to the matching sub-domain only (exact or prefix match)
    #
    #   D) core_fuse_* frequency registers:
    #      → excluded entirely from flatten_freq_ratios (filtered in Step 1)
    ia_freq:   dict = {}   # fk -> reg_name  (fw_fuses_ia_*)
    atom_freq: dict = {}   # fk -> reg_name  (fw_fuses_atom_*)
    dom_freq:  dict = {}   # first-token key -> {fk: reg_name}

    for (fk, reg_name, fp) in freq_regs:
        name = reg_name.lower()
        c = fp.split('.')[-1] if '.' in fp else fp
        if re.match(r'^[a-z_]*fuse[s]?_ia_', name):
            # IA-level register — applies to all bigcore sub-domains
            ia_freq.setdefault(fk, reg_name)
        elif re.match(r'^[a-z_]*fuse[s]?_atom_', name):
            # Atom-level register — applies to all atom sub-domains
            atom_freq.setdefault(fk, reg_name)
        else:
            # Domain-specific (ring, gt, media, sa_qclk …)
            sdk: str | None = _extract_subdomain_key(reg_name, c)
            if not sdk:
                # Fallback: strip any fuse prefix and use first token as key
                sdk: str = re.sub(r'^[a-z_]*fuse[s]?_', '', name).split('_')[0]
            dom_freq.setdefault(sdk, {})[fk] = reg_name

    def _is_system_container(container: str) -> bool:
        """True when the container is a system/platform fuse block
        (punit_fuses, dmu_fuse, fw_fuses …), not a per-core coreN_fuse."""
        return not re.search(r'core\d+_fuse', container)

    # Attach flatten ratios to every matching domain entry
    for key, group in domain_map.items():
        subdomain = group['_subdomain']
        container = group['_container']
        frq: dict = {}

        if _is_system_container(container):
            # IA freq ratios → all bigcore sub-domains in system containers
            if 'bigcore' in subdomain:
                frq.update(ia_freq)
            # Atom freq ratios → all atom sub-domains in system containers
            if 'atom' in subdomain:
                frq.update(atom_freq)

        # Domain-specific freq ratios — exact subdomain match first,
        # then prefix match (handles atom_ccp0 matching 'atom' key, etc.)
        if subdomain in dom_freq:
            frq.update(dom_freq[subdomain])
        else:
            for sdk, sdk_regs in dom_freq.items():
                if subdomain.startswith(sdk) or sdk.startswith(subdomain.split('_')[0]):
                    frq.update(sdk_regs)
                    break

        if frq:
            group['_flatten_freq_ratios'] = frq

    # ── Step 3: build final domain entries ───────────────────────────────
    # JSON key = subdomain_key directly. Each subdomain_key already encodes
    # the cluster/core number (cluster0_bigcore, core1_bigcore_base_vf …)
    # so collisions across containers are already impossible.  We still run
    # a dedup pass to catch any edge cases.
    sem_counts = Counter(group['_subdomain'] for group in domain_map.values())

    new_domains: dict = {}

    for key, group in domain_map.items():
        fuse_path  = group['_fuse_path']
        container  = group['_container']
        subdomain  = group['_subdomain']

        vol_map   = group.get('vf_voltage', {})
        ratio_map = group.get('vf_ratio',   {})

        if not vol_map and not ratio_map:
            log.error(f"    [!] Skipping ({container}, {subdomain}): no voltage or ratio registers")
            continue

        vol_regs   = [vol_map[i]   for i in sorted(vol_map)]
        ratio_regs = [ratio_map[i] for i in sorted(ratio_map)]

        # wp_count: prefer live num_of_points, then register list length
        wp_count = wp_count_overrides.get(key,
                   len(vol_regs) if vol_regs else len(ratio_regs))

        vol_regs   = vol_regs[:wp_count]
        ratio_regs = ratio_regs[:wp_count]

        fuse_ram_path = '.'.join(fuse_path.split('.')[:-1]) if '.' in fuse_path else fuse_path

        # Use subdomain_key directly as JSON key; if two different containers
        # somehow produce the same subdomain name, prefix with container.
        if sem_counts[subdomain] == 1:
            json_key = subdomain
        else:
            json_key: str = f"{container}_{subdomain}"

        # Parse conversion hints — 3-tier priority:
        #   1. spec_conversion_hints in platform_config.json  (spec-authoritative)
        #   2. _parse_desc_conversion_hints() from register descriptions (regex)
        #   3. _infer_freq_multiplier() keyword heuristic (fallback)
        desc_hints = _parse_desc_conversion_hints(group.get('_descriptions', []))
        _spec_conv = cfg.get('spec_conversion_hints', {})
        freq_mult = (
            _spec_conv.get(subdomain, {}).get('freq_multiplier')
            or desc_hints.get('freq_multiplier')
            or _infer_freq_multiplier(subdomain, {})
        )
        voltage_lsb_mv = (
            _spec_conv.get(subdomain, {}).get('voltage_lsb_mv')
            or desc_hints.get('voltage_lsb_mv')
        )  # None → downstream defaults to 3.90625 mV/LSB

        entry: dict = {
            'label':          _generate_domain_label(json_key),
            'freq_multiplier': freq_mult,
            'wp_count':        wp_count,
            'fuse_path':       fuse_path,
            'fuse_ram_path':   fuse_ram_path,
        }
        # Store voltage_lsb_mv only when parsed from description (non-default).
        # Downstream conversions.py uses this if present, else falls back to
        # the standard 3.90625 mV/LSB (= 1/256 V) Intel encoding.
        if voltage_lsb_mv and abs(voltage_lsb_mv - _VOLT_LSB_MV) > 0.001:
            entry['voltage_lsb_mv'] = voltage_lsb_mv
        if vol_regs:
            entry['vf_voltage'] = vol_regs
        if ratio_regs:
            entry['vf_ratio'] = ratio_regs

        for opt_field in ('vf_voltage_adder',
                          'vf_voltage_delta_idx1',
                          'vf_voltage_delta_idx2'):
            opt_map = group.get(opt_field, {})
            if opt_map:
                entry[opt_field] = [opt_map[i] for i in sorted(opt_map)]

        flatten = group.get('_flatten_freq_ratios', {})
        if flatten:
            entry['flatten_freq_ratios'] = dict(sorted(flatten.items()))

        new_domains[json_key] = entry

    if not new_domains:
        log.error("    [!] build_vf_domains_from_discovery: no complete domains built")
        return 0

    # ── Step 4: backup + write ────────────────────────────────────────────
    if vf_domains_path.exists():
        shutil.copy2(vf_domains_path, vf_domains_path.with_suffix('.json.bak'))

    output = {
        '_platform':         detect_platform_name().lower(),
        '_platform_updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        '_generated_by':     'build_vf_domains_from_discovery',
        'domains':           new_domains,
    }

    with open(vf_domains_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

    log.info(f"\n[+] vf_domains.json rebuilt from scratch — {len(new_domains)} domain(s):")
    for json_key, entry in sorted(new_domains.items()):
        vv: int = len(entry.get('vf_voltage', []))
        vr: int = len(entry.get('vf_ratio',   []))
        ff = list(entry.get('flatten_freq_ratios', {}).keys())
        fp = entry.get('fuse_path', '').split('.')[-1]
        log.info(f"    {json_key:<35}  {vv:>3}V  {vr:>3}R  "
              f"flatten={ff if ff else 'none'}  [{fp}]")
    log.info(f"    Backup: {vf_domains_path.with_suffix('.json.bak').name}")

    return len(new_domains)


def run_discovery_pipeline(force: bool = False) -> bool:
    """Run the full VF register discovery pipeline and update vf_domains.json.

    Intended to be called from the tool launchers BEFORE vf_domains.json is
    loaded, so the tool always boots with an up-to-date domain list.

    Steps executed: 0 (detect platform) → 2 (fuse paths) → 3 (load fuse RAM)
                    → 4 (analyse registers) → 4.5 (auto-learn unknowns)
                    → 6.5 (merge into vf_domains.json)

    Step 1 (ITP init) is intentionally skipped — the caller (vf_curve_manager.py
    or vf_curve_manager_cli.py) has already initialised ITP.
    Step 6 (save report files) is skipped to keep startup fast; run the script
    standalone to get full CSV/JSON reports.

    Args:
        force: When False (default) the pipeline is skipped if vf_domains.json
               already contains at least one domain — i.e. it only runs on the
               very first launch on a new platform or when explicitly requested.
               When True the pipeline always runs (use --rediscover on the CLI
               or GUI to trigger this).

    Returns:
        True  — discovery ran and vf_domains.json was (re-)populated.
        False — skipped because the JSON was already populated and force=False.
    """
    vf_domains_path: Path = SCRIPT_DIR / 'vf_domains.json'

    # --- Check whether discovery is actually needed -------------------------
    if not force:
        if vf_domains_path.exists():
            try:
                with open(vf_domains_path, 'r', encoding='utf-8') as _f:
                    _data = json.load(_f)
                domain_count: int = len(_data.get('domains', {}))
                if domain_count > 0:
                    log.info(f"Discovery: vf_domains.json has {domain_count} domain(s) "
                          f"— skipping (pass --rediscover to force)")
                    return False
            except Exception:
                pass  # Corrupted JSON → fall through and re-discover

    # --- Run pipeline -------------------------------------------------------
    log.info("")
    log.info("=" * 80)
    log.info("  AUTO VF REGISTER DISCOVERY")
    log.info("  vf_domains.json will be populated from live hardware")
    log.info("=" * 80)

    # Step 0: detect platform + load config
    log.info("\n[*] Step 0: Detecting platform...")
    platform_name: str = detect_platform_name()
    log.info(f"    Detected: {platform_name}")
    cfg = load_platform_config(platform_name)
    fuse_root = cfg['fuse_root']

    # Step 2: discover fuse paths
    log.info(f"\n[*] Step 2: Discovering fuse paths under {fuse_root}...")
    fuse_paths = discover_fuse_paths(cfg)
    if not fuse_paths:
        log.error("No fuse paths found — discovery aborted. "
              "Check ITP connection and platform_config.json.")
        return False
    log.info(f"Found {len(fuse_paths)} fuse path(s):")
    for p in fuse_paths:
        log.info(f"    - {p}")

    # Step 3: load fuse RAM (required before register reads)
    log.info(f"\n[*] Step 3: Loading fuse RAM (this takes 2-5 min)...")
    if not load_fuse_ram_once(fuse_root):
        log.error("Fuse RAM load failed — register values may be unavailable; "
              "continuing anyway")

    # Register ALL discovered fuse roots so the session guard in
    # hardware_access._LOADED_FUSE_RAM_PATHS covers every root found on
    # this platform (e.g. both 'cdie.fuses' AND 'soc.fuses').  Without this,
    # the UI's open_registers_tab() would re-call load_fuse_ram for sibling
    # roots, triggering a _enable_dcg postcondition write → cold reset.
    try:
        from utils.hardware_access import notify_fuse_ram_loaded
        _seen_roots_pipeline: set = set()
        for _fp in fuse_paths:
            _parts = _fp.split('.')
            if len(_parts) >= 2:
                _root: str = '.'.join(_parts[:2])  # e.g. 'cdie.fuses', 'soc.fuses'
                if _root not in _seen_roots_pipeline:
                    notify_fuse_ram_loaded(_root)
                    _seen_roots_pipeline.add(_root)
                    log.info(f"Session guard: registered fuse root '{_root}' as loaded")
    except Exception as _nfl_err:
        log.warning(f"Could not register sibling fuse roots: {_nfl_err}")

    # Step 4: analyse all fuse paths — with cold-reset resume support
    _n_paths: int = len(fuse_paths)
    log.info(f"\n[*] Step 4: Analysing registers across {_n_paths} fuse path(s)...")
    start_time: float = time.time()
    all_path_results: dict = {}
    _scan_errors: list = []          # non-fatal errors logged for the summary
    for _path_idx, path_str in enumerate(fuse_paths):
        label = path_str.split('.')[-1]
        # ── inline progress bar ───────────────────────────────────────────────
        _done: int     = _path_idx + 1
        _pct      = int(100 * _done / _n_paths)
        _elapsed: float  = time.time() - start_time
        _eta_str: str  = (
            f"ETA {int(_elapsed / _path_idx * (_n_paths - _path_idx))}s"
            if _path_idx > 0 else "ETA --"
        )
        _bar_fill = int(30 * _path_idx / _n_paths)
        _bar: str      = '#' * _bar_fill + '-' * (30 - _bar_fill)
        log.info(f"\r  [{_bar}] {_pct:3d}%  ({_path_idx}/{_n_paths})  {_eta_str}  scanning: {label[:40]:<40}",
              end='', flush=True)
        # ─────────────────────────────────────────────────────────────────────
        try:
            results = analyze_fuse_path(path_str, label, cfg)
        except Exception as _scan_ex:
            _scan_str: str = str(_scan_ex).lower()
            if any(kw in _scan_str for kw in _DISCOVERY_TARGET_DOWN_KW):
                log.info("")
                log.info("!" * 70)
                log.info(f"  [COLD-RESET] Target went down during scan of: {path_str}")
                log.info(f"  Exception   : {_scan_ex}")
                log.info(f"  Progress    : {len(all_path_results)}/{len(fuse_paths)} paths done")
                log.info("!" * 70)
                # Persist whatever we already have so a forced-abort isn't a total loss
                if all_path_results:
                    log.info("Saving partial results to cache before waiting for recovery...")
                    _save_discovery_cache(
                        _all_results_to_flat_records(all_path_results),
                        platform_name,
                        cfg.get('display_name', platform_name),
                    )
                log.info("Waiting for target to reboot (up to 180 s)...")
                try:
                    from utils.hardware_access import wait_for_sut_boot
                    _recovered = wait_for_sut_boot(timeout_seconds=180)
                except Exception as _wb_ex:
                    log.error(f"wait_for_sut_boot raised: {_wb_ex}")
                    _recovered = False
                if _recovered:
                    log.info(f"Target is back — retrying path: {path_str}")
                    try:
                        results = analyze_fuse_path(path_str, label, cfg)
                        log.info(f"Retry succeeded for: {path_str}")
                    except Exception as _retry_ex:
                        log.error(f"Retry also failed for '{path_str}': {_retry_ex}  — skipping.")
                        _scan_errors.append(f"{path_str}: retry failed ({_retry_ex})")
                        results = None
                else:
                    log.error(f"Target did not recover within timeout — skipping '{path_str}'.")
                    _scan_errors.append(f"{path_str}: target did not recover")
                    results = None
            else:
                # Non-hardware error — log and continue
                log.error(f"Error scanning '{path_str}': {_scan_ex}")
                _scan_errors.append(f"{path_str}: {_scan_ex}")
                results = None
        if results:
            all_path_results[path_str] = results
            # Checkpoint every 5 paths so a late crash doesn't lose everything
            if (_path_idx + 1) % 5 == 0:
                _save_discovery_cache(
                    _all_results_to_flat_records(all_path_results),
                    platform_name,
                    cfg.get('display_name', platform_name),
                )
                log.info(f"\n[*] Checkpoint saved after {_path_idx + 1} paths.")
    # Final progress: overwrite the \r line with a completed bar
    _scan_elapsed: float = time.time() - start_time
    log.info(f"\r  [{'#' * 30}] 100%  ({_n_paths}/{_n_paths})  "
          f"done in {_scan_elapsed:.1f}s{' ' * 20}")
    if _scan_errors:
        log.warning(f"\n[WARNING] {len(_scan_errors)} path(s) skipped during Step 4:")
        for _e in _scan_errors:
            log.info(f"  • {_e}")

    # Step 4.5: auto-learn any unknown-domain registers; save patterns to JSON
    auto_learn_unknown_patterns(all_path_results, platform_name, cfg)

    # Save flat snapshot for 'dump-registers' CLI command and GUI Discovered Registers tab
    _save_discovery_cache(
        _all_results_to_flat_records(all_path_results),
        platform_name,
        cfg.get('display_name', platform_name),
    )

    # Step 6.5: build complete fresh vf_domains.json from discovered registers
    # (replaces the old incremental merge — source-of-truth approach)
    log.info(f"\n[*] Step 6.5: Building vf_domains.json from discovery results...")
    changes: int = build_vf_domains_from_discovery(all_path_results, cfg)

    # Step 6.6: discover scalar modifier registers (ITD, downbin, P0 overrides, etc.)
    log.info(f"\n[*] Step 6.6: Discovering scalar modifier registers...")
    scalar_count: int = auto_discover_scalar_modifiers(all_path_results, cfg)

    elapsed: float = time.time() - start_time
    total_active: int = _count_active(all_path_results)

    # Step 6.7: auto-export discovered registers to Excel
    log.info(f"\n[*] Step 6.7: Exporting discovered registers to Excel...")
    try:
        excel_path = export_discovered_registers_to_excel(
            platform_display=cfg.get('display_name', platform_name),
        )
        if excel_path:
            log.info(f"    Register dump  : {excel_path}")
        else:
            log.warning("    Excel export failed — check logs for details.")
    except Exception as _xls_exc:  # noqa: BLE001
        log.warning(f"    Excel export skipped: {_xls_exc}")

    # Step 6.8: auto-export scalar modifiers to Excel
    log.info(f"\n[*] Step 6.8: Exporting scalar modifiers to Excel...")
    try:
        scalar_xl = export_scalar_modifiers_to_excel(
            platform_display=cfg.get('display_name', platform_name),
        )
        if scalar_xl:
            log.info(f"    Scalar dump    : {scalar_xl}")
        else:
            log.warning("    Scalar export skipped — no scalar_modifiers discovered yet.")
    except Exception as _sxl_exc:  # noqa: BLE001
        log.warning(f"    Scalar export skipped: {_sxl_exc}")

    log.info("")
    log.info("=" * 80)
    log.info(f"  DISCOVERY COMPLETE  ({elapsed:.1f}s)")
    log.info("=" * 80)
    log.info(f"  Platform      : {cfg.get('display_name', platform_name)}")
    log.info(f"  Paths scanned : {len(fuse_paths)}")
    log.info(f"  Active regs   : {total_active}")
    log.info(f"  VF JSON changes : {changes}")
    log.info(f"  Scalar modifiers: {scalar_count}")
    log.info("")
    return True

