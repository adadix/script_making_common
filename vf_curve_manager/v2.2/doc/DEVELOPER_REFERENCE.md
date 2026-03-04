# VF Curve Manager Tool v2.2 - Developer Reference

**Technical Documentation for Code Maintenance and Feature Development**

---

## 🌐 Project-Agnostic Design

This tool is designed to work across **all Intel platforms** (WCL, MTL, LNL, ARL, PTL, and any future platform) without code modifications. Platform-specific configuration is driven entirely by `platform_config.json` and auto-discovered `vf_domains.json`.

**Key Design Principles**:
- Hardware abstraction through JSON configuration
- No hardcoded domain names, register paths, or silicon codenames in Python code
- Dynamic UI generation based on configuration
- Autonomous first-launch — zero manual setup required
- Portable across Intel validation environments

**To support a new platform**: Add one JSON block to `platform_config.json` — no Python code changes required.

**Platform detection** uses a 3-tier strategy (most → least authoritative):
1. `pysv_config.ini [baseaccess] project=` — written by PythonSV itself
2. Pythonsv project root subdirectory names matched against platform keys
3. Installed Python package names matched against platform keys

If the platform is not in `platform_config.json`, the `generic` entry is used as a fallback. All discovery, register reading, caching, GUI, and CLI still work fully on `generic`.

**Pre-configured platforms**: `wildcatlake`, `meteorlake`, `lunarlake`, `arrowlake`, `pantherlake`, `novalake`, `generic`

---

## 📐 Architecture Overview

### Module Structure

```
vf_curve_manager_tool_v2.2/
├── src/
│   ├── vf_curve_manager.py           # GUI entry point: ITP init, startup dialog, discovery
│   ├── vf_curve_manager_cli.py       # CLI entry point: argument parsing, command dispatch
│   ├── platform_config.json          # Per-platform fuse paths and domain patterns
│   ├── vf_domains.json               # Auto-generated hardware domain configurations
│   ├── vf_discovery_cache.json       # Auto-generated register value cache
│   ├── core/
│   │   ├── config_loader.py          # vf_domains.json parser + scalar_modifiers accessor
│   │   ├── curve_engine.py           # High-level VF operations, show/bump/edit/flatten
│   │   └── platform_discovery.py    # Legacy platform discovery utilities
│   ├── discovery/                    # ← Discovery sub-package
│   │   ├── auto_discover_vf_registers.py  # Standalone discovery entry point
│   │   ├── startup_discovery.py           # Autonomous bridge (GUI + CLI shared)
│   │   ├── discovery_core.py              # Detection, categorisation, cache I/O
│   │   │                                  #   VF_KEYWORDS, categorize_register,
│   │   │                                  #   get_register_info, analyze_fuse_path,
│   │   │                                  #   _save_discovery_cache, load_discovery_cache
│   │   └── discovery_learn.py             # Learning, domain building, pipeline
│   │                                      #   run_discovery_pipeline,
│   │                                      #   build_vf_domains_from_discovery,
│   │                                      #   auto_learn_unknown_patterns,
│   │                                      #   auto_discover_scalar_modifiers,
│   │                                      #   _extract_subdomain_key, _vf_field_type
│   ├── ui/
│   │   ├── curve_manager_ui.py       # Main PyQt5 window, Discovered Registers tab
│   │   ├── workers.py                # QThread workers for hardware operations
│   │   ├── dialogs/
│   │   │   └── scalar_modifiers.py   # Scalar Modifiers dialog
│   │   ├── mixins/
│   │   │   ├── discovery_mixin.py    # Discovery trigger and result handling
│   │   │   ├── domain_mixin.py       # Domain button management
│   │   │   ├── operations_mixin.py   # Bump/edit/flatten/customize wiring
│   │   │   ├── progress_mixin.py     # Progress dialog management
│   │   │   └── theme_mixin.py        # Dark/light theme toggle
│   │   └── tabs/
│   │       ├── registers_tab.py      # Discovered Registers tab widget
│   │       └── result_tabs.py        # VF curve result tabs
│   └── utils/
│       ├── conversions.py            # Voltage/frequency math
│       ├── hardware_access.py        # ITP/fuse operations, reset logic, scalar reads
│       ├── data_export.py            # Excel/PNG generation
│       ├── fuse_io.py                # Low-level fuse read/write/flush
│       ├── itp_recovery.py           # SUT watchdog and ITP recovery
│       ├── log_setup.py              # Rotating log file configuration
│       ├── mock_backend.py           # ITP mock for offline / unit tests
│       ├── process_utils.py          # OpenIPC termination helpers
│       ├── watchdog.py               # Hardware watchdog thread
│       ├── constants.py              # Shared constants (_VOLT_LSB_MV, etc.)
│       ├── _boot_stats.py            # Boot timing statistics collector
│       └── _simple_dataframe.py      # Lightweight DataFrame substitute
├── tests/                            # pytest test suite (299 pass / 2 skip)
├── Logs/                             # Auto-created runtime output directory
├── requirements.txt
├── run_vf_curve_manager_ui.bat
└── run_cli.bat
```

---

## 🔧 Core Components

### 0a. discovery_core.py — Detection, Categorisation, Cache I/O

**Responsibility**: Platform detection, fuse path probing, register reading,
register categorisation, and cache persistence.  No learning or domain-building
logic lives here.

**Key public symbols**:

```python
# ─ Category keyword map ─────────────────────────────────────────
VF_KEYWORDS: dict  # category → keyword list; used by categorize_register()
# Categories: 'vf_curve', 'frequency', 'curve_config', 'itd_voltage',
#             'itd_slope', 'p0_override', 'acode_min', 'downbin',
#             'atom_delta', 'mct_delta', 'voltage', 'power', 'thermal',
#             'fivr', 'fw_fuses'

DISCOVERY_CACHE_PATH: Path  # src/vf_discovery_cache.json

detect_platform_name() -> str
    # 3-tier: pysv_config.ini -> pythonsv root dirs -> installed packages

load_platform_config(platform_name: str) -> dict
    # Reads platform_config.json; falls back to 'generic' entry

get_vf_registers_in_path(path_str: str) -> (obj, [names])
    # Returns all ITP attribute names matching VF_KEYWORDS on the fuse object

get_register_info(obj, reg_name: str) -> dict
    # Reads value + description for one register
    # Returns: {name, value, hex, description, accessible, active}

categorize_register(reg_name: str, description: str, cfg: dict) -> (category, domain)
    # Classifies using VF_KEYWORDS (category) + cfg domain_patterns (domain)
    # bigcore_fuse_override: promotes core_fuse / ia_ registers to bigcore

analyze_fuse_path(path_str: str, path_label: str, cfg: dict) -> dict
    # Full register scan of one fuse path → {category: [reg_info, …]}

_save_discovery_cache(records, platform_name, platform_display)
    # Writes vf_discovery_cache.json with type coercion (int/str/bool)
    # Prevents ITP custom integer objects from corrupting the file

load_discovery_cache() -> (records, platform_name, platform_display, timestamp)
    # Returns (None, …) if cache missing, corrupt, or has UTF-8 BOM

export_discovered_registers_to_excel(platform_display, records) -> str
    # Writes Excel: Name, Value, Hex, Converted, Active,
    #               Domain, Category, Fuse Path, Description

_infer_conversion_from_description(reg_name, description, value) -> str
    # Converts raw register value to physical unit string
    # e.g. '850 mV', '4500 MHz', '12.5 W'
```

**Active flag logic** (`_is_zero_valid`):
```python
_ZERO_VALID_PATTERNS = ('adder', 'delta', 'vf_index', 'num_of', 'v_gap', 'vfloor', 'vceil')
# Most VF registers: active = (value != 0)
# Adder/delta/index registers legitimately hold 0 ("no offset", "first index")
info['active'] = (int(value) != 0) or _is_zero_valid(reg_name)
```

**Fuse root discovery — dynamic + static, not hardcoded**:
```python
# Step 1 — namednodes dynamic inspection (most authoritative)
# dir() the live namednodes namespace; any node with a .fuses attribute is collected:
#   cdie, cdie0, cdie1, soc, die, chip, ...  → all discovered automatically
_enumerate_fuse_roots() -> list[str]
    # Returns e.g. ['cdie.fuses', 'cdie0.fuses'] on a dual-die platform.
    # Falls back to Step 2 when namednodes is unavailable.

# Step 2 — static fallback candidates (in priority order)
_FUSE_ROOT_CANDIDATES = ['cdie.fuses', 'soc.fuses', 'die.fuses', 'chip.fuses', 'fuses']

# Step 3 — platform_config.json fuse_root hint is always included
# (inserted at index 0 if not already found by Steps 1/2)

discover_fuse_paths(cfg: dict) -> list[str]
    # Top-level entry point called by run_discovery_pipeline().
    # 1. Calls _enumerate_fuse_roots() to get ALL live roots.
    # 2. Ensures cfg['fuse_root'] (from platform_config.json) is in the list.
    # 3. For each root: _enumerate_containers_under_root() → classifies
    #    [system] / [core] / [other] containers.
    # 4. Falls back to _discover_fuse_paths_from_config() if dir() gives nothing.
    # 5. Returns aggregated, de-duplicated path list from all roots.
    # cfg['fuse_root'] is updated to the first live root found (used by
    # load_fuse_ram_once() and downstream report steps).
```

> **Key point**: `platform_config.json` `"fuse_root"` is a **fallback hint only**,
> not a ceiling. On a platform with `cdie0.fuses` and `soc.fuses`, both are
> discovered and scanned without any config change.

---

### 0b. discovery_learn.py — Learning, Domain Building, Pipeline

**Responsibility**: Pattern learning, VF domain construction, scalar modifier
discovery, and the top-level `run_discovery_pipeline()`. Imports from
`discovery_core` for all register reading and categorisation.

**Key public functions**:

```python
run_discovery_pipeline(force: bool = False) -> bool
    # Full pipeline:
    #   detect → config → fuse paths → fuse RAM →
    #   register reads (discovery_core) →
    #   build_vf_domains_from_discovery →
    #   auto_learn_unknown_patterns →
    #   auto_discover_scalar_modifiers →
    #   write vf_domains.json + vf_discovery_cache.json
    # Skips automatically if vf_domains.json already populated (unless force=True)

build_vf_domains_from_discovery(all_path_results: dict, cfg: dict) -> int
    # Rebuilds vf_domains.json from scratch using discovered vf_curve registers
    # Parses fw_fuses_DOMAIN_vf_{voltage,ratio}_N patterns
    # Routes core_fuse acode_ia_* registers to coreN_bigcore_base_vf domains

auto_learn_unknown_patterns(all_path_results, platform_name, cfg) -> int
    # Scores unknown-domain vf_curve registers against _AUTO_DOMAIN_KEYWORDS
    # Persists new patterns to platform_config.json (zero unknowns on future runs)
    # Only operates on category='vf_curve' registers

auto_discover_scalar_modifiers(all_path_results: dict, cfg: dict) -> int
    # Discovers ITD, P0 override, downbin, delta registers
    # Writes to vf_domains.json['scalar_modifiers'] (idempotent, keyed by name)
    # Types: itd_voltage, itd_slope, p0_override, downbin, mct_delta,
    #        atom_delta, acode_min

_extract_subdomain_key(reg_name: str, container: str) -> str | None
    # Parses subdomain from register name or coreN_fuse container
    # Container-based detection fires FIRST (acode ia_ inside coreN_fuse)

_vf_field_type(reg_name: str) -> str | None
    # Returns 'vf_voltage', 'vf_ratio', 'vf_voltage_adder',
    #         'vf_voltage_delta_idxN', or None
```

---

### 0c. startup_discovery.py — Autonomous Bridge

**Responsibility**: Called by both `vf_curve_manager.py` (GUI) and `vf_curve_manager_cli.py` (CLI) after ITP init, before `vf_domains.json` is loaded.

```python
def maybe_run_discovery(force: bool = False) -> bool:
    # force=False: skips if vf_domains.json already has >= 1 domain
    # force=True:  always runs (--rediscover mode)
    # Exception-safe: any error is caught, tool continues with existing data
```

**Startup flow (GUI)**:
```
vf_curve_manager.py
  └→ terminate_openipc()
  └→ ITP init (ipc, itp, itp.unlock())
  └→ QApplication created
  └→ startup dialog (if domains already exist):
       "Refresh from hardware?" Yes/No (default No)
  └→ startup_discovery.maybe_run_discovery(force=user_choice)
  └→ ConfigLoader, CurveEngine, CurveManagerUI launched
```

**Startup flow (CLI)**:
```
vf_curve_manager_cli.py
  └→ terminate_openipc()
  └→ init_itp()
  └→ startup_discovery.maybe_run_discovery(force=True)  # always fresh in CLI
  └→ setup_modules() -> CurveEngine, ConfigLoader
  └→ dispatch command
```

> **Note on SUT check**: Starting from v2.2 the CLI has SUT verification **ON by
> default** (`enable_sut = not args.no_sut_check`). Use `--no-sut-check` to opt
> out for fast / stable-system use. `--enable-sut-check` is kept as a legacy
> no-op alias.

---

### 0d. platform_config.json — Platform Registry

**Responsibility**: All platform-specific fuse roots, domain classification patterns, and microarchitecture codenames. Editing this file is the only action needed to support a new platform.

**Schema per platform entry**:
```json
{
  "wildcatlake": {
    "display_name": "WildcatLake (WCL)",
    "fuse_root": "cdie.fuses",   // FALLBACK HINT only — dynamic discovery
                                  // always tries all namednodes.*.fuses roots first
    "core_fuse_pattern": "core{n}_fuse",
    "system_fuse_names": ["punit_fuses"],
    "extra_fuse_names": ["cpu0_fuse", "cpu1_fuse", ...],
    "bigcore_fuse_override": "core_fuse",
    "spec_conversion_hints": {           // 3-tier priority: spec > regex > heuristic
      "cluster0_bigcore": {
        "voltage_lsb_mv": 3.90625,       // U1.8 Intel universal (3.90625 mV/LSB)
        "freq_multiplier": 100
      }
    },
    "domain_patterns": { ... }
  }
}
```

---

### 0e. vf_discovery_cache.json — Register Cache

**Responsibility**: Persists discovered register data so the GUI/CLI loads instantly on subsequent runs without re-scanning hardware.

**Schema**:
```json
{
  "platform_name": "wildcatlake",
  "platform_display": "WildcatLake (WCL) A1",
  "timestamp": "2026-02-19T18:41:06",
  "records": [
    {
      "name": "ia_vf_voltage_reg_adder_cluster0",
      "value": 12,
      "hex": "0xc",
      "converted": "30 mV",
      "active": true,
      "accessible": true,
      "domain": "bigcore",
      "category": "vf_curve",
      "fuse_path": "cdie.fuses.punit_fuses",
      "description": "Voltage adder register — resolution of ~2.5mV ..."
    },
    ...
  ]
}
```

**Corruption prevention**: `_save_discovery_cache()` coerces all values with `int()`, `str()`, `bool()` before `json.dump`. This prevents ITP custom integer objects (which are not JSON-serialisable) from truncating the file on write.

---

### 1. hardware_access.py - Hardware Interface Layer

**Responsibility**: All ITP and fuse register interactions

**Key Functions**:

```python
# Initialization
def init_hardware(ipc, itp, enable_sut_check=False)
    # Sets global ipc, itp, ENABLE_SUT_VERIFICATION
    # Call once at startup

# Fuse Operations
def load_fuse_ram(domain_info) -> bool
    # Loads fuse RAM state from hardware
    # Must call before reading fuse values

def flush_fuse_ram(domain_info) -> bool
    # Writes fuse RAM changes to hardware
    # Must call after modifying fuse values

def read_voltage_frequency(domain_info, wp_index) -> (float, float)
    # Returns (voltage_volts, frequency_mhz) for specific WP
    # frequency_mhz is None when WP index >= len(vf_ratio) (e.g. NVL upper WPs)

def write_voltage(domain_info, wp_index, millivolts) -> bool
    # Writes voltage in mV to specific WP

def write_frequency(domain_info, wp_index, new_freq_mhz) -> bool
    # Writes frequency in MHz to specific WP via vf_ratio register
    # Returns False (with error message) if wp_index >= len(vf_ratio)

# Reset & Boot
def reset_target(wait_for_boot=None, boot_timeout=300) -> dict
    # Resets target with optional boot wait
    # Returns: {'reset_success', 'boot_success', 'cold_reset_detected', ...}

def wait_for_sut_boot(timeout=300, check_interval=2, min_boot_time=15) -> bool
    # Waits for SUT to boot with cold reset monitoring
    # Check interval: 2s (1s during 0-15s critical period)
    # Monitors for cold reset every 2s during 0-90s window

# Cold Reset Detection
def detect_cold_reset(wait_time=5) -> dict
    # Returns: {'is_cold_reset', 'indicators', 'confidence', 'timestamp'}
    # Keywords: SLP_S5, Device Gone, CPU : Off, Power Lost

def check_power_state() -> dict
    # Returns: {'powered_on', 'state', 'cold_reset_indicator'}

# Recovery
def recover_from_deep_sleep(bypass_cooldown=False) -> bool
    # ITP recovery: baseaccess -> forcereconfig -> unlock
    # Includes SUT reachability check via ping

# Discovered Register Write
def apply_discovered_register_edits(edits: list) -> dict
    # Writes one or more discovered registers to hardware
    # edit entry: {'fuse_path': str, 'reg_name': str, 'new_value': int}
    # Flow: load_fuse_ram -> write -> flush_fuse_ram -> resettarget -> verify
    # Returns: {'success', 'message', 'written': [{'reg_name', 'before',
    #           'after', 'verified'}], 'cold_reset': bool}
```

**Global State**:
```python
ipc = None                           # IPC instance
itp = None                           # ITP instance
ENABLE_SUT_VERIFICATION = False      # Boot check toggle
_recovery_in_progress = False        # Recovery lock
_last_recovery_time = 0              # Cooldown tracking
```

**Important Notes**:
- ⚠️ NO progress callbacks (removed to prevent UI deadlock)
- Cold reset detection runs every 2 seconds (1s during critical 0-15s)
- All cold reset logs include ISO timestamps
- Hardware auto-reverts fuses on cold reset - tool only verifies

---

### 2. curve_engine.py - Business Logic Layer

**Responsibility**: High-level VF curve operations

**Key Methods**:

```python
class CurveEngine:
    def show_vf_curves(domain_names, interp_enabled=True) -> dict
        # Reads VF curves, generates Excel + PNG
        # Returns: {'dataframes', 'excel_paths', 'png_paths', ...}
    
    def bump_voltages(domain_names, bump_mv, direction) -> dict
        # Bumps all voltages up/down by bump_mv
        # Saves before_data, applies changes, resets, verifies
        # Returns: {'success', 'before_data', 'after_data', ...}
        # On cold reset: {'error': 'COLD_RESET', 'auto_revert_verified', ...}
    
    def edit_voltages(domain_name, voltage_changes, freq_changes=None) -> dict
        # Edits specific WP voltages and/or frequencies
        # voltage_changes: {wp_index: new_mv, ...}  (None or {} to skip)
        # freq_changes:    {wp_index: new_mhz, ...} (None or {} to skip)
        # Similar return as bump_voltages
    
    def flatten_frequency(domain_name) -> dict
        # Sets P0/P1/Pn ratios to same value
        # Similar return structure
    
    def _verify_automatic_revert(domain_names, before_data, unique_fuse_rams) -> (bool, str)
        # Called after cold reset detection
        # Waits for cold boot (180s timeout)
        # Multiple ITP recovery attempts (3x with backoff)
        # Retry fuse load (2x per path)
        # Compares current values vs before_data
        # Returns: (success_bool, detailed_message_str)
```

**Cold Reset Handling Flow**:
```python
1. Save before_data (pre-modification values)
2. Apply changes to fuses
3. Flush to hardware
4. reset_target() with boot wait
5. If cold reset detected:
   a. _verify_automatic_revert() called
   b. Wait for system cold boot (180s)
   c. Attempt ITP recovery (3 tries)
   d. Load fuse RAM (2 tries per path)
   e. Read current values
   f. Compare with before_data
   g. Return detailed comparison
6. Return {'error': 'COLD_RESET', 'revert_details': details, 'auto_revert_verified': bool}
```

---

### 3. curve_manager_ui.py - Presentation Layer

**Responsibility**: PyQt5 GUI and user interaction

**Key Classes**:

```python
class BumpWorkerThread(QThread):
    # Runs bump_voltages() in background
    # Signals: finished(dict), error(str)

class FlattenWorkerThread(QThread):
    # Runs flatten_frequency() in background
    # Signals: finished(dict, str, dict), error(str)

class CurveManagerUI(QWidget):
    # Main dashboard window
```

**Important UI Methods**:

```python
def _show_progress_dialog_for_bump/flatten/wp_edit(...)
    # Creates simple progress dialog
    # NO time tracking (prevents UI deadlock)
    # Shows: "Processing operation... Please wait"
    # Indeterminate progress (no progress bar)
    # No cancel button during reset

def _cleanup_progress_dialog(progress)
    # Properly closes dialog
    # Forces UI repaint: QApplication.processEvents() + self.repaint()

def _handle_cold_reset_error(results, operation_name)
    # Shows structured cold reset dialog
    # Visual separators (━━━)
    # Sections: What Happened, Hardware Protection, Next Steps, Technical
    # Displays revert_details if verification succeeded

def open_registers_tab()
    # Opens Discovered Registers tab
    # Loads from vf_discovery_cache.json via load_discovery_cache()
    # Falls back to _live_scan_registers() if no cache exists
    # Persist-back: writes edits back via _save_discovery_cache() (type-safe)

def _live_scan_registers()
    # Fallback when no cache exists
    # Runs full discovery pipeline inline (auto_discover_vf_registers)
    # Saves result to vf_discovery_cache.json
    # Returns (records, platform_display, timestamp)
```

**Worker Thread Pattern**:
```python
1. Create progress dialog (indeterminate)
2. Create worker thread
3. Connect signals: worker.finished.connect(on_finish)
4. Start worker: worker.start()
5. Start timeout monitor: QTimer.singleShot(1000, check_timeout)
6. Worker emits finished/error
7. Cleanup dialog: _cleanup_progress_dialog()
```

**⚠️ Critical UI Rules**:
- NEVER call `QApplication.processEvents()` from worker thread
- NEVER use progress callbacks from worker thread
- Use `_cleanup_progress_dialog()` to prevent white screen
- Keep UI operations on main thread only

---

### 4. config_loader.py - Configuration Parser

**Responsibility**: Parse and validate vf_domains.json

```python
class ConfigLoader:
    def get_domain(domain_name) -> dict
        # Returns domain config dict
    
    def get_all_domain_names() -> list
        # Returns list of domain keys
    
    def validate_configuration() -> tuple
        # Returns (is_valid, error_list)
```

**Domain Configuration Schema**:
```json
{
  "domain_name": {
    "label": "Display Name",              // UI-friendly name
    "freq_multiplier": 100,               // 33.33, 50, or 100 MHz
    "wp_count": 9,                        // Number of working points
    "fuse_path": "path.to.fuse_obj",      // Platform-specific fuse path
    "fuse_ram_path": "path.to.fuse_ram",  // Optional, defaults to fuse_path
    "vf_voltage": ["reg0", "reg1", ...],  // Voltage register names (count = wp_count)
    "vf_ratio": ["reg0", "reg1", ...],    // OPTIONAL — frequency ratio registers
                                          // May have fewer entries than wp_count
                                          // (e.g. NVL: 12 ratio regs for 24 WPs)
                                          // Delta domains have no vf_ratio at all
    "flatten_freq_ratios": {              // Optional — for flatten operation
      "p0": "p0_reg_name",               // Max turbo ratio
      "p1": "p1_reg_name",               // Guaranteed (base) ratio
      "pn": "pn_reg_name",               // Min efficiency ratio
      "min": "min_reg_name",             // Optional: min ratio
      "fmax_vmin": "fmax_reg_name"        // Optional: NVL most-efficient-point ratio
    },
    "voltage_lsb_mv": 3.90625            // Optional, defaults to 3.90625 mV/LSB
  }
}
```

**Platform-Specific Fields**:
- `fuse_path`: ITP path to fuse object (e.g., `"cdie.fuses.punit_fuses"` for WCL, `"cdie.fuses.dmu_fuse"` for ARL)
- `vf_voltage`/`vf_ratio`: Register names vary by platform and domain
- `domain_name`: Can be platform-specific (e.g., `ia_core` vs `cluster0_bigcore`)

**Tool reads this file at startup** - changes take effect on next launch.

---

## 🔄 Key Workflows

### Voltage Modification Workflow

```
1. UI: User clicks Bump/WP Edit button
2. UI: Show progress dialog, create worker thread
3. Worker: curve_engine.bump_voltages() / edit_voltages()
4. Engine: Save before_data (current values)
5. Engine: load_fuse_ram() for all domains
6. Engine: Apply voltage changes
7. Engine: flush_fuse_ram() to hardware
8. Engine: reset_target(wait_for_boot=True)
9. HW Access: itp.resettarget()
10. HW Access: wait_for_sut_boot() - monitors for cold reset every 2s
11a. Normal path: Boot succeeds, verify values, return success
11b. Cold reset path: 
     - wait_for_sut_boot() detects cold reset → returns False
     - reset_target() calls detect_cold_reset()
     - reset_target() returns {'cold_reset_detected': True}
     - curve_engine calls _verify_automatic_revert()
     - Wait for cold boot (180s)
     - ITP recovery (3 attempts)
     - Load fuses (2 attempts)
     - Read and compare values
     - Return {'error': 'COLD_RESET', 'revert_details': ...}
12. Worker: Emit finished/error signal
13. UI: _handle_cold_reset_error() or _after_bump()
14. UI: Show results dialog
```

---

### Cold Reset Detection Workflow

```
Trigger Points (all during wait_for_sut_boot):
├── Initial monitoring (0-15s): Check every 1 second
│   ├── check_power_state() - look for cold_reset_indicator
│   ├── Check if target went offline (SUT ping)
│   └── Log with timestamp if detected
│
├── Critical monitoring (15-90s): Check every 2 seconds
│   ├── check_power_state() - SLP_S5, power off indicators
│   ├── Exception handling - scan for keywords
│   └── Log every 5 checks during this period
│
└── Post-critical (90s+): Continue 2s checks until timeout

Detection Methods:
1. power_check['cold_reset_indicator'] == True
   └── Keywords in power state: SLP_S5, Device Gone, CPU : Off
2. power_check['powered_on'] == False
   └── Target powered off mid-boot
3. Exception message keywords
   └── 'slp_s5', 'power lost', 'target power lost', etc.

All detections include:
- ISO timestamp
- Elapsed time since reset
- Detailed state information
- Visual warnings (⚠️)
```

---

## 📊 Data Structures

### Reset Result Dict
```python
{
    'reset_success': bool,
    'boot_success': bool,            # If verification enabled
    'boot_time': float,              # Seconds
    'cold_reset_detected': bool,
    'cold_reset_details': {
        'is_cold_reset': bool,
        'indicators': ['reason1', ...],
        'confidence': 'high|medium|low',
        'timestamp': '2025-11-07T23:45:12.123456'
    },
    'message': str
}
```

### Operation Result Dict
```python
# Success case
{
    'success': True,
    'before_data': {domain: [(v, f), ...]},
    'after_data': {domain: [(v, f), ...]},
    'excel_paths': {domain: path},
    'png_paths': {domain: path},
    'verification': {
        'all_within_tolerance': bool,
        'failures': [...]
    }
}

# Cold reset case
{
    'error': 'COLD_RESET',
    'before_data': {...},
    'cold_reset_details': {...},
    'auto_revert_verified': bool,
    'revert_details': str,           # Detailed voltage comparison
    'message': str
}
```

---

## 🔢 Important Constants

### Voltage/Frequency Conversion
```python
# hardware_access.py
VOLTAGE_STEP_MV = 2.56              # Hardware resolution
VOLTAGE_TOLERANCE_MV = 5.7          # ±2 register steps

# conversions.py
def voltage_to_volts(raw_value):
    return raw_value / 256.0

def voltage_to_millivolts(raw_value):
    return raw_value * 2.56

def millivolts_to_raw(millivolts):
    return int(round(millivolts / 2.56))

def ratio_to_frequency(ratio, multiplier):
    return ratio * multiplier
```

### Timing Constants
```python
# hardware_access.py
_recovery_cooldown = 5               # Seconds between recovery attempts

# wait_for_sut_boot parameters
check_interval = 2                   # Seconds between checks (default)
min_boot_time = 15                   # Minimum wait before declaring boot success
timeout_seconds = 300                # 5 minute default timeout

# Cold reset verification
boot_timeout = 180                   # 3 minutes for cold boot
max_recovery_attempts = 3
recovery_backoff = [15, 30]          # 15s, 30s between attempts
```

---

## 🐛 Common Pitfalls & Solutions

### 1. UI Deadlock
**Problem**: Progress dialog freezes, white screen

**Cause**: Calling `QApplication.processEvents()` from worker thread via callback

**Solution**: 
- ✅ NO progress callbacks
- ✅ Simple static dialogs
- ✅ Use `_cleanup_progress_dialog()` method

### 2. Cold Reset Not Detected
**Problem**: Cold reset happens but tool doesn't catch it

**Causes**:
- Check interval too slow
- Not monitoring during critical window
- Missing keywords in detection

**Solutions**:
- ✅ 2-second check interval (1s during 0-15s)
- ✅ Monitor entire 0-300s boot window
- ✅ Check keywords: SLP_S5, Device Gone, CPU : Off, Power Lost

### 3. Fuse Read Fails After Cold Reset
**Problem**: Can't read fuses after cold reset detected

**Cause**: System needs time to cold boot and stabilize

**Solution**:
- ✅ Wait for boot (180s timeout)
- ✅ Multiple ITP recovery attempts (3x)
- ✅ Retry fuse load (2x per path)
- ✅ Proper backoff between attempts

### 4. Incorrect Auto-Revert Assumption
**Problem**: Trying to manually revert fuses after cold reset

**Solution**:
- ✅ Hardware automatically reverts on cold reset
- ✅ Tool should VERIFY, not REVERT
- ✅ Compare current vs before_data
- ✅ Show comparison to user

---

## 🔬 Testing Guidelines

### Unit Testing Focus Areas

1. **Voltage Conversions**
   ```python
   # Test boundary cases
   assert millivolts_to_raw(0) == 0
   assert millivolts_to_raw(2.56) == 1
   assert millivolts_to_raw(655.36) == 256
   ```

2. **Cold Reset Detection**
   ```python
   # Mock power states
   test_cases = [
       {'state': 'SLP_S5', 'expect_cold_reset': True},
       {'state': 'Normal', 'expect_cold_reset': False},
       {'state': 'Device Gone', 'expect_cold_reset': True}
   ]
   ```

3. **Configuration Validation**
   ```python
   # Invalid configs should fail
   assert not config_loader.validate_configuration()[0]
   ```

### Integration Testing Scenarios

1. **Normal Bump Operation**
   - Small bump (10mV)
   - Should complete successfully
   - Verify values within tolerance

2. **Cold Reset Scenario**
   - Large bump (50mV+)
   - Should trigger cold reset
   - Should detect within 2-3 seconds
   - Should show detailed dialog
   - Fuses should revert

3. **Recovery After Cold Reset**
   - After cold reset detection
   - System should cold boot
   - ITP recovery should succeed
   - Fuse read should succeed
   - Verification should complete

---

## 📝 Code Style Guidelines

### Logging Format
```python
# Include timestamps for cold reset events
from datetime import datetime
timestamp = datetime.now().isoformat()

# Use visual indicators
print(f"[WARNING] ⚠️ COLD RESET DETECTED [{timestamp}] ⚠️")
print(f"[SUCCESS] ✓ Operation completed [{timestamp}]")
print(f"[INFO] Status information")
print(f"[ERROR] Error details")
print(f"[DEBUG] Debug information")
```

### Function Documentation
```python
def function_name(param1, param2) -> return_type:
    """
    Brief description.
    
    Longer description if needed.
    
    Args:
        param1: Description
        param2: Description
        
    Returns:
        Description of return value
        
    Raises:
        ExceptionType: When it's raised
    """
```

### Error Handling
```python
try:
    # Operation
    result = risky_operation()
except SpecificException as ex:
    print(f"[ERROR] Specific error: {ex}")
    # Handle gracefully
except Exception as ex:
    print(f"[ERROR] Unexpected error: {ex}")
    import traceback
    traceback.print_exc()
    # Return safe default
```

---

## 🚀 Adding New Features

### Adding a New Domain
1. Add entry to `vf_domains.json` with platform-specific register paths
2. Verify register paths in ITP for your platform
3. Test with Show VF Curve operation
4. Tool automatically detects and displays the new domain - no code changes needed

### Adding a New Platform
1. Add a new key block to `src/platform_config.json` with `fuse_root`, `core_fuse_pattern`, `domain_patterns`, and `desc_hints`
2. Add microarchitecture codenames to the relevant domain pattern lists
3. Launch tool — discovery runs automatically, `vf_domains.json` is populated
4. No Python changes needed

**If platform is not added to `platform_config.json`**: falls back to `generic` entry. Discovery, caching, GUI, CLI all still work — only ambiguous register classification may be slightly coarser.

### Adding a New Operation
1. Add method to `CurveEngine` class
2. Follow pattern: load → modify → flush → reset → verify
3. Add worker thread in UI (inherit from QThread)
4. Add progress dialog wrapper
5. Handle cold reset case
6. Add UI button and connect to method

### Modifying Cold Reset Detection
1. Update keywords in `detect_cold_reset()`
2. Adjust check intervals in `wait_for_sut_boot()`
3. Test with known cold reset scenarios
4. Update logging format consistently

---

## 📚 Dependencies

### Required Intel Modules
```python
import itpii              # ITP interface
import pysvtools          # SV tools
import ipccli             # IPC interface
import namednodes         # Named node access
```

### Python Standard Library
```python
import sys, os, time
import json
from datetime import datetime
```

### External Packages
```python
PyQt5>=5.15               # GUI framework
pandas                    # Data processing
matplotlib                # Plotting
openpyxl                  # Excel export
numpy                     # Numerical ops
scipy                     # Interpolation
tabulate                  # Table formatting
colorama                  # Console colors
```

---

## 🔐 Security Considerations

- Tool has direct hardware access via ITP
- No network communication
- All data stored locally in Logs/
- Configuration is local JSON file
- Intel Confidential - internal use only

---

## 📊 Performance Characteristics

- **Memory**: ~100-150 MB
- **CPU**: ~2-5% during operations
- **Storage**: ~500KB-2MB per operation (Excel + PNG)
- **Operation Time**: 
  - Show VF Curve: <1 second
  - Modifications: 20s (no SUT check) to 300s (with SUT check)

---

**End of Developer Reference** - Technical Implementation Guide
