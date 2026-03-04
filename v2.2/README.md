# VF Curve Manager Tool v2.2

**Project-Agnostic Voltage-Frequency Curve Management for Intel Platforms**

---

## Quick Start

### GUI Mode (Interactive Dashboard)
```bash
run_vf_curve_manager_ui.bat
```

### CLI Mode (Command-Line / Scripting)
```bash
# From root directory
run_cli.bat list
run_cli.bat show --domains cluster0_bigcore ring

# Or from src/ directory
cd src
python vf_curve_manager_cli.py list
python vf_curve_manager_cli.py show --domains cluster0_bigcore ring
```

---

## Documentation

📖 **[USER_GUIDE.md](doc/USER_GUIDE.md)** - Complete user manual for both GUI and CLI modes  
🔧 **[DEVELOPER_REFERENCE.md](doc/DEVELOPER_REFERENCE.md)** - Technical architecture and API reference  
🚀 **[PLATFORM_MIGRATION_GUIDE.md](doc/PLATFORM_MIGRATION_GUIDE.md)** - How to configure for new platforms  
📋 **[QUICK_REFERENCE.txt](doc/QUICK_REFERENCE.txt)** - CLI quick reference card  
🔌 **[INTEGRATION_GUIDE.md](doc/INTEGRATION_GUIDE.md)** - Integration and scripting reference

---

## Two Modes Available

### 🖥️ GUI Mode
- Interactive PyQt5 dashboard
- Visual plots and charts
- Real-time progress tracking
- **Discovered Registers tab** — browse all hardware registers with values, physical units, domain, and category
- Scalar Modifiers dialog — view ITD voltages, P0 overrides, downbin/delta registers
- Perfect for exploration and ad-hoc testing

### 💻 CLI Mode
- Scriptable command-line interface
- Automation-friendly with exit codes
- Remote/headless system support
- `dump-registers` — export all discovered registers to Excel
- `edit-register` — view or write any individual register
- Perfect for CI/CD and batch operations

**Quick CLI Examples:**
```bash
# 1. List all available domains
run_cli.bat list

# 2. Show VF curves (read-only)
run_cli.bat show --domains cluster0_bigcore ring

# 3. Bump voltages up/down (with --yes for automation)
run_cli.bat bump --domains cluster0_bigcore --value 10 --direction up --yes
run_cli.bat bump --domains ring --value 5 --direction down --yes

# 4. Edit specific WP voltages (with --yes for automation)
run_cli.bat edit --domain cluster0_bigcore --wp 0:850 --wp 1:800 --wp 2:750 --yes

# 5. Flatten frequency to P0/P1/Pn (with --yes for automation)
run_cli.bat flatten --domain cluster0_bigcore --target p1 --yes

# 6. Customize frequency points independently (with --yes for automation)
run_cli.bat customize --domain cluster0_bigcore --p0 4500 --p1 1500 --pn 400 --yes

# 7. Export all discovered registers to Excel
run_cli.bat dump-registers
run_cli.bat dump-registers --active-only

# 8. View a specific register (no ITP needed — uses cache)
run_cli.bat edit-register --name fw_fuses_cluster0_bigcore_vf_voltage_reg_adder_0

# 9. Write a register value via ITP
run_cli.bat edit-register --name fw_fuses_cluster0_bigcore_vf_voltage_reg_adder_0 --set-value 12 --yes

# 10. Force re-discovery from hardware (after firmware fuse burn)
run_cli.bat --rediscover list

# 11. Enable SUT verification for automatic boot checks (slower but safer)
#     NOTE: --enable-sut-check must come BEFORE the command name
run_cli.bat --enable-sut-check show --domains ring
run_cli.bat --enable-sut-check bump --domains cluster0_bigcore --value 10 --direction up --yes
```

> **Automation Tip:** Use `--yes` flag to skip confirmations in scripts!  
> **SUT Verification:** Add `--enable-sut-check` BEFORE the command for automatic boot verification (slower but safer)  
> **Force Re-discovery:** Add `--rediscover` BEFORE the command to rebuild the register cache from hardware  
> **Full Documentation:** See [USER_GUIDE.md](doc/USER_GUIDE.md) for detailed documentation

---

## Autonomous Operation

The tool is **fully autonomous** — no manual setup required on any supported platform:

| Launch Scenario | Behaviour |
|---|---|
| **First launch / blank config** | Auto-detects platform, discovers all fuse paths and registers, populates `vf_domains.json` and cache — no user input needed |
| **Subsequent launches** | Dialog asks whether to refresh from hardware (default: skip, use cached data) |
| **`--rediscover` flag** | Bypasses dialog and always runs full re-discovery |
| **Cache missing at runtime** | GUI and CLI fall back to live hardware scan automatically |

---

## Platform Support

Supports **any Intel platform** with zero code changes. Platform detection is fully automatic via 3-tier strategy:
1. `pysv_config.ini [baseaccess] project=` (written by PythonSV itself)
2. Pythonsv project root subdirectory names
3. Installed Python package names

**Pre-configured platforms** in `src/platform_config.json`: WildcatLake, MeteorLake, LunarLake, ArrowLake, PantherLake, NovaLake, plus a `generic` fallback for any unrecognised platform.

**To add a new platform**: add one JSON block to `platform_config.json` — no Python changes needed.  
See [PLATFORM_MIGRATION_GUIDE.md](doc/PLATFORM_MIGRATION_GUIDE.md) for details.

---

## Register Discovery

On first launch the tool automatically:
1. Detects platform from PythonSV
2. Probes fuse root (`cdie.fuses`, `soc.fuses`, etc.) dynamically
3. Discovers all per-core (`coreN_fuse`) and system fuse paths
4. Reads every accessible register — value, hex, description
5. Categorises each register (domain + category) using `platform_config.json` patterns
6. Routes core-local ACODE registers (`acode_ia_*`) to per-core VF curve domains
7. Infers physical units from description text (`850 mV`, `4500 MHz`, `12.5 W`)
8. Saves `vf_discovery_cache.json` and writes discovered domains to `vf_domains.json`
9. Discovers scalar modifiers (ITD voltages, P0 overrides, downbin/delta registers)

Results are available instantly on all subsequent launches from the cache.

---

## Register Categories

The discovery engine classifies every register into one of these categories:

| Category | Description |
|---|---|
| `vf_curve` | VF working-point voltage and ratio registers |
| `frequency` | Standalone P0/P1/Pn/min ratio registers |
| `curve_config` | `wp_count`, `num_of_points` config registers |
| `itd_voltage` | ITD cutoff/floor voltage thresholds |
| `itd_slope` | ITD slope coefficient registers |
| `p0_override` | Per-use-case P0 ratio (AVX2, TMUL, AMX, …) |
| `acode_min` | Per-core ACODE minimum ratio |
| `downbin` | Non-FCT core P0 ratio downbin corrections |
| `mct_delta` | Multi-core P0 scaling deltas |
| `atom_delta` | Atom IA P0 delta registers |
| `voltage` | Platform voltage rails (Vcc, Vnn, SVID, …) |
| `power` | Power limits (PL1, PL2, TDP, …) |
| `thermal` | Thermal thresholds (TCC, Prochot, …) |
| `fivr` | Fully-Integrated Voltage Regulator registers |
| `fw_fuses` | General firmware fuse registers |

---

**Intel BDC CVE Labs** | Anil Kumar Dadi | anil.kumar.dadi@intel.com
