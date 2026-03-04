# Integration Guide

## Using VF Curve Manager Tool v2.2 on Any Intel Platform

This guide explains how the tool achieves platform-agnostic operation and how to
integrate or extend it for new projects.

---

## Table of Contents

1. [How Auto-Discovery Works](#how-auto-discovery-works)
2. [First Launch](#first-launch)
3. [Forcing Re-Discovery](#forcing-re-discovery)
4. [Platform Profiles via platform_config.json](#platform-profiles)
5. [vf_domains.json Schema](#vf_domainsjson-schema)
6. [Adding a New Platform](#adding-a-new-platform)
7. [Architecture Overview](#architecture-overview)

---

## How Auto-Discovery Works

The tool is **fully autonomous**.  On first launch (or when `--rediscover` is
used) the discovery pipeline executes automatically:

```
┌───────────────────────────────────────────────────────────────┐
│ 1. PLATFORM DETECTION                                            │
│    detect_platform_name() → reads pysv_config.ini / dirs /       │
│    installed packages                                            │
└───────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────────┐
│ 2. CONFIG LOAD                                                   │
│    load_platform_config() → reads platform_config.json           │
│    (falls back to 'generic' profile if platform unknown)         │
└───────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────────┐
│ 3. FUSE PATH DISCOVERY & REGISTER SCAN  (see detail below)      │
│    - _enumerate_fuse_roots(): dynamic namednodes inspection +    │
│      static fallback + platform_config.json hint                 │
│    - Scans ALL live fuse roots (cdie.fuses, cdie0.fuses, …)      │
│    - analyse_fuse_path() reads every matching register           │
│    - categorize_register() assigns (category, domain)            │
└───────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────────┐
│ 4. DOMAIN BUILDING                                               │
│    build_vf_domains_from_discovery()                             │
│    - Parses fw_fuses_DOMAIN_vf_{voltage,ratio}_N patterns        │
│    - Routes core_fuse ia_ registers to coreN_bigcore domains     │
│    - Writes vf_domains.json (all domain entries)                 │
└───────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────────┐
│ 5. PATTERN LEARNING                                              │
│    auto_learn_unknown_patterns()                                 │
│    - Scores registers whose domain wasn't matched by config      │
│    - Persists new patterns to platform_config.json               │
│    - Zero unknowns on subsequent runs                            │
└───────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────────┐
│ 6. SCALAR MODIFIER DISCOVERY                                     │
│    auto_discover_scalar_modifiers()                              │
│    - Discovers ITD, P0 override, downbin, delta registers        │
│    - Writes vf_domains.json['scalar_modifiers'] (idempotent)     │
│    - Types: itd_voltage, itd_slope, p0_override, downbin,        │
│              mct_delta, atom_delta, acode_min                    │
└───────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────────┐
│ 7. CACHE WRITE                                                   │
│    _save_discovery_cache()                                       │
│    - Writes vf_discovery_cache.json                              │
│    - Coerces ITP custom integer objects to plain int/str         │
│    - Subsequent launches skip hardware scan (use cache)          │
└───────────────────────────────────────────────────────────────┘
```

### Fuse Root Discovery Detail

Discovery finds **all** live fuse roots dynamically — `platform_config.json` `fuse_root` is a fallback hint, not a ceiling:

1. **namednodes dynamic inspection** (Step 1, most authoritative) — `dir()`s the live `namednodes` namespace; any top-level node (`cdie`, `cdie0`, `cdie1`, `soc`, `die`, …) that has a `.fuses` attribute is collected automatically. Works for any naming convention without code changes.
2. **Static fallback candidates** (Step 2) — used when `namednodes` is unavailable: `cdie.fuses → soc.fuses → die.fuses → chip.fuses → fuses` (tried in order; all that resolve are included).
3. **Config hint** (Step 3) — `platform_config.json` `fuse_root` value is always added even when not found by Steps 1–2.

All found roots are scanned. Their container paths (system, coreN, other) are aggregated into one de-duplicated list that feeds `analyse_fuse_path()`. On a platform with `cdie0.fuses` **and** `soc.fuses`, both are discovered and scanned without any configuration change.

---

## First Launch

No manual steps are required. On first launch:

1. Connect to the target via ITP as normal.
2. Run the tool (`run_vf_curve_manager_ui.bat` or `run_cli.bat list`).
3. If `vf_domains.json` is empty/blank, the discovery pipeline runs automatically (~2–5 minutes).
4. `vf_domains.json` and `vf_discovery_cache.json` are populated.
5. The tool loads and is ready to use.

---

## Forcing Re-Discovery

Re-run the full pipeline at any time with the `--rediscover` flag:

**GUI:**
```bash
# Startup dialog — click "Yes, Refresh from hardware"
run_vf_curve_manager_ui.bat
```

**CLI:**
```bash
run_cli.bat --rediscover list
run_cli.bat --rediscover dump-registers
```

**Python (programmatic):**
```python
from discovery.discovery_learn import run_discovery_pipeline
run_discovery_pipeline(force=True)
```

Re-discovery overwrites `vf_domains.json` and `vf_discovery_cache.json` completely.

---

## Platform Profiles

All platform-specific tuning lives in `src/platform_config.json`.  The tool
never needs code changes to support a new Intel platform.

### Structure

```json
{
  "platforms": {
    "wcl": {
      "display_name": "Wildcat Lake",
      "fuse_root": "cdie.fuses",
      "system_fuse_containers": ["punit_fuses"],
      "domain_patterns": { "bigcore": ["cluster.*bigcore", "ia.*core"] },
      "desc_hints": {},
      "bigcore_fuse_override": true
    },
    "nvl": {
      "display_name": "Nova Lake",
      "fuse_root": "cdie.fuses",
      "system_fuse_containers": ["dmu_fuse"],
      "domain_patterns": { "bigcore": ["bigcore_base_vf", "ia.*core0"] },
      "desc_hints": {},
      "bigcore_fuse_override": false
    },
    "generic": {
      "display_name": "Generic Platform",
      "fuse_root": "cdie.fuses",
      "system_fuse_containers": ["punit_fuses", "dmu_fuse"],
      "domain_patterns": {},
      "desc_hints": {},
      "bigcore_fuse_override": false
    }
  }
}
```

### Key Fields

| Field | Description |
|---|---|
| `fuse_root` | Fallback hint for the ITP fuse root path. Discovery dynamically enumerates all live roots via `namednodes` inspection first; this value is only used when `namednodes` is unavailable or doesn’t find the root. |
| `system_fuse_containers` | Fuse container objects under the fuse root to scan |
| `domain_patterns` | Regex patterns mapping register names to domain keys |
| `bigcore_fuse_override` | `true` promotes `ia_` registers from `core_fuse` paths to `bigcore` domain |

### Adding a New Platform Profile

1. Identify the fuse root via ITP (`namednodes.cdie.fuses.<TAB>`).
2. Add an entry to `platform_config.json` using the platform's package/directory name as the key.
3. Run `--rediscover` — pattern learning (`auto_learn_unknown_patterns`) fills any gaps.
4. Commit the updated `platform_config.json`.

---

## vf_domains.json Schema

`vf_domains.json` is **generated automatically** by the discovery pipeline and
should not be manually edited in normal use.  The schema is documented here for
reference and for advanced manual overrides.

### Top-Level Structure

```json
{
  "domains": {
    "<domain_key>": { ... }
  },
  "scalar_modifiers": {
    "<register_name>": { ... }
  }
}
```

### Domain Entry Schema

```json
{
  "label": "Bigcore",
  "freq_multiplier": 100,
  "wp_count": 10,
  "fuse_path": "cdie.fuses.punit_fuses",
  "fuse_ram_path": "cdie.fuses",
  "vf_voltage": [
    "fw_fuses_cluster0_bigcore_vf_voltage_0",
    "..."
  ],
  "vf_ratio": [
    "fw_fuses_cluster0_bigcore_vf_ratio_0",
    "..."
  ],
  "flatten_freq_ratios": {
    "p0": "fw_fuses_ia_p0_ratio",
    "p1": "fw_fuses_ia_p1_ratio",
    "pn": "fw_fuses_ia_pn_ratio"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `label` | Yes | Display name in UI |
| `freq_multiplier` | Yes | 33.33 / 50 / 100 MHz |
| `wp_count` | Yes | Working-point count (length of `vf_voltage`) |
| `fuse_path` | Yes | ITP path to fuse object |
| `fuse_ram_path` | No | Defaults to `fuse_path` |
| `vf_voltage` | Yes | Voltage register name array |
| `vf_ratio` | No | Ratio register name array (may be shorter than `wp_count`) |
| `flatten_freq_ratios` | No | Key → register map for Flatten Frequency operation |

### Scalar Modifier Entry Schema

```json
{
  "register_name": "fw_fuses_cluster0_bigcore_itd_voltage",
  "type": "itd_voltage",
  "domain": "bigcore",
  "fuse_path": "cdie.fuses.punit_fuses",
  "value": 12,
  "hex": "0xc",
  "converted": "30 mV",
  "description": "ITD voltage threshold"
}
```

| `type` value | Meaning |
|---|---|
| `itd_voltage` | IVR thermal device voltage threshold |
| `itd_slope` | IVR thermal device slope |
| `p0_override` | P0 frequency override |
| `downbin` | Frequency downbin |
| `mct_delta` | MCT voltage delta |
| `atom_delta` | Atom voltage delta |
| `acode_min` | Minimum acode |

---

## Adding a New Platform

### Quick Path (Recommended)

1. Add a `platform_config.json` entry for the new platform (copy `generic` as base).
2. Connect to hardware via ITP.
3. Run `run_cli.bat --rediscover list` (or launch the GUI and choose "Yes, Refresh").
4. Verify domains are discovered correctly.
5. Commit `platform_config.json` and the generated `vf_domains.json`.

### If Auto-Discovery Misclassifies Registers

1. Check `vf_discovery_cache.json` — look for registers in `category='vf_curve'`
   with `domain='unknown'`.
2. Add regex patterns to `platform_config.json` `domain_patterns` for the new platform.
3. Re-run discovery.  `auto_learn_unknown_patterns()` will also add patterns automatically.

---

## Architecture Overview

```
startup_discovery.py          ← called by GUI + CLI at startup
  └ run_discovery_pipeline()   ← discovery_learn.py
       ├ detect_platform_name() ← discovery_core.py
       ├ load_platform_config() ← discovery_core.py
       ├ analyze_fuse_path()    ← discovery_core.py  (per path)
       ├ build_vf_domains_from_discovery()  ← discovery_learn.py
       ├ auto_learn_unknown_patterns()       ← discovery_learn.py
       ├ auto_discover_scalar_modifiers()    ← discovery_learn.py
       └ _save_discovery_cache()             ← discovery_core.py

vf_domains.json      ← written by discovery, read by GUI/CLI
vf_discovery_cache.json  ← written by discovery, read by Registers tab
platform_config.json ← static per-platform tuning (version-controlled)
```

### Key Design Principles

1. **Zero code changes for new platforms** — only `platform_config.json` needs updating.
2. **Cache-first** — hardware scans only run when needed; subsequent launches are instant.
3. **Idempotent writes** — `auto_discover_scalar_modifiers` is keyed by register name; re-running never duplicates entries.
4. **Self-learning** — `auto_learn_unknown_patterns` persists new domain patterns back to `platform_config.json`, so each discovery run improves future runs.
5. **Type-safe cache** — ITP custom integer objects are coerced to plain Python types before writing JSON to prevent corruption.

---

## Summary

| Scenario | Action |
|----------|--------|
| First time on a platform | Just launch the tool — discovery runs automatically |
| Hardware fuses changed | Use `--rediscover` flag |
| New platform not yet in `platform_config.json` | Add profile entry, then `--rediscover` |
| Browse all hardware registers | `run_cli.bat dump-registers` or Discovered Registers tab |
| Inspect a single register | `run_cli.bat edit-register --name <partial_name>` |
