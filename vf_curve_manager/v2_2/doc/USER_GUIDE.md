# VF Curve Manager Tool v2.2 - User Guide

**Developed by**: Intel BDC CVE Labs  
**Author**: Anil Kumar Dadi

---

## 🎯 What is This Tool?

VF Curve Manager Tool is a **project-agnostic** solution for reading and modifying voltage-frequency curves on Intel platforms through direct hardware access. It provides a professional GUI dashboard and a full command-line interface for safe voltage/frequency adjustments with built-in protection mechanisms.

The tool is **fully autonomous** — on first launch it auto-detects the platform, discovers all fuse paths and registers, and populates its own configuration. No manual setup is required.

Supports any Intel platform (WCL, MTL, LNL, ARL, PTL, NVL, and any future platform) via `platform_config.json`. No code changes are ever needed for a new platform.

---

## 🚀 Quick Start

### 1. Launch the Tool

**Windows:**
```bash
run_vf_curve_manager_ui.bat
```

**Python:**
```bash
cd src
python vf_curve_manager.py
```

### 2. Startup Behaviour

| Launch Scenario | What Happens |
|---|---|
| **First launch / blank config** | Platform auto-detected, full register discovery runs (~2–5 min), `vf_domains.json` and register cache populated automatically |
| **Subsequent launches** | Dialog asks: *"Refresh from hardware?"* — press **No / Enter** to use cached data (instant), **Yes** to re-scan hardware |
| **`--rediscover` flag** | Always re-runs full discovery without showing the dialog |

### 3. Basic Workflow

1. **Select Domains**: Click domain buttons in left sidebar (domains loaded from `vf_domains.json`)
2. **Optional: Enable SUT Verification**: Check the checkbox for automatic recovery
3. **Show VF Curve**: Click "📊 Show VF Curve" to view current settings
4. **View Results**: Check tabs for voltage/frequency tables and plots
5. **Modify (optional)**: Use Bump/WP Edit/Flatten buttons
6. **Discovered Registers**: Open the **Discovered Registers** tab to browse all hardware registers
7. **Check Logs**: All data auto-saved to `Logs/` folder

---

## 📊 Available Operations

### 1. Show VF Curve
**What it does**: Reads and displays current voltage-frequency curves from hardware

**Steps**:
1. Select one or more domains
2. Enable/disable "Enable Interpolation" for smooth plots
3. Click "📊 Show VF Curve"
4. View results in tabs

**Output**: Excel file + PNG plot in `Logs/` folder

---

### 2. Bump Voltages
**What it does**: Increases or decreases ALL voltages by specified amount

**Steps**:
1. Select domains
2. Enter value in mV (e.g., 10 for +10mV)
3. Click "⬆ Bump Up" or "⬇ Bump Down"
4. Confirm operation
5. Wait 20-300 seconds (progress dialog)
6. Check verification results

**Output**: Before/After Excel + comparison plot

**⚠️ Warning**: This modifies hardware! Start with small values (5-10mV)

---

### 3. WP Edit - Precise Voltage and Frequency Control
**What it does**: Edit specific working point (WP) voltages **and frequencies** individually

**Steps**:
1. Select **exactly ONE domain**
2. Click "✏ WP Edit"
3. Table shows 6 columns: WP Index, Current Voltage (mV), **New Voltage (mV)**, Current Freq (MHz), **New Freq (MHz)**, Delta
4. Edit **New Voltage (mV)** and/or **New Freq (MHz)** columns for desired WPs
5. Leave cells blank to keep current value unchanged
6. Click "Apply Changes"
7. Confirm and wait for completion

**Output**: Before/After Excel + comparison plot

**Use when**: You need precise per-WP voltage or frequency control instead of blanket changes

---

### 4. Flatten Frequency
**What it does**: Sets P0/P1/Pn frequency ratios to same value (for debugging)

**Steps**:
1. Select **exactly ONE domain** (must support flattening)
2. Click "🔧 Flatten Frequency"
3. Select target ratio (e.g., "Flatten to P0")
4. Confirm operation
5. Wait for completion

**Output**: Before/After frequency ratio comparison

**Use when**: Debugging frequency scaling issues

---

### 5. Discovered Registers Tab
**What it does**: Displays all VF-related hardware registers discovered from the live platform, loaded from the local cache (`vf_discovery_cache.json`).

**Columns shown**:
| Column | Description |
|---|---|
| **Register Name** | Full ITP register name |
| **Value (dec)** | Raw integer value read from hardware |
| **Value (hex)** | Same value in hex |
| **Converted** | Physical unit — e.g. `850 mV`, `4500 MHz`, `12.5 W` (inferred from description) |
| **Active** | `✓` if the register holds a meaningful programmed value |
| **Domain** | Classified domain (bigcore, atom, ring, gt, media, sa, io) |
| **Category** | Register type — `vf_curve`, `frequency`, `curve_config`, `itd_voltage`, `itd_slope`, `p0_override`, `acode_min`, `downbin`, `atom_delta`, `mct_delta`, `voltage`, `power`, `thermal`, `fivr`, `fw_fuses` |
| **Description** | Hardware description string from PythonSV |

**Filter bar**: Type any text to filter by name, domain, or category.

**Active filter**: Registers are marked active when `value ≠ 0`, OR when 0 is a valid programmed state (adder, delta, vf_index, num_of, v_gap, vfloor, vceil register types).

> **Note on WP Display**: Working points where both voltage and frequency are 0 are automatically trimmed from the displayed table. Only active WPs with real data are shown.

**Apply to Hardware button**: Select rows and click to write new values directly to hardware via ITP (load_fuse_ram → write → flush → reset → verify).

**Refresh**: Click **Refresh** to re-scan hardware and rebuild the cache.

**Scalar Modifiers**: The discovery pipeline also auto-detects ITD (Integrated Voltage Regulation Thermal Device), P0 override, downbin, and delta registers, stored as `scalar_modifiers` in `vf_domains.json`. These appear in the Discovered Registers tab under categories `itd_voltage`, `itd_slope`, `p0_override`, `downbin`, `mct_delta`, `atom_delta`, and `acode_min`. They can be viewed and written via the **Apply to Hardware** button, the **Scalar Modifiers dialog** (GUI only), or the `scalars` CLI command.

---

### 6. Scalar Modifiers Dialog (GUI)
**What it does**: Dedicated read/write dialog for all scalar modifier registers — ITD thresholds, P0 frequency overrides, downbin values, voltage delta adjusters, and ACODE minimum limits.

**How to open**: Click the **Scalar Modifiers** button in the toolbar (available after discovery completes).

**Columns**:
| Column | Description |
|---|---|
| **Register** | Full hardware register name |
| **Type** | `itd_voltage`, `itd_slope`, `p0_override`, `downbin`, `mct_delta`, `atom_delta`, `acode_min` |
| **Domain** | Owning domain (bigcore, atom, ring, gt, …) |
| **Current Value** | Raw integer read from cache |
| **Converted** | Physical unit (mV, MHz, raw) |
| **Description** | Hardware description from PythonSV |

**Editing**: Double-click a value cell, enter the new **physical value** (mV for voltage registers, MHz for frequency registers), click **Apply**. The tool converts to the correct raw LSB value automatically before writing.

> **Tip**: Use `run_cli.bat scalars show` for a quick terminal read of all scalar modifiers, or `run_cli.bat scalars edit --key <register> --value <physical_value>` to write without opening the GUI.

---

## ⚙️ Settings & Options

### Enable SUT Verification & Recovery

**Default state**: SUT verification is **ON by default** in the CLI. In the GUI, use the checkbox to control it per session.

**What it does**: 
- Automatically detects power state errors
- Performs ITP recovery if needed
- Pings SUT to verify it's reachable
- Waits for SUT to fully boot after reset

**When to enable**:
- ✅ Production validation
- ✅ Overnight testing
- ✅ Unstable systems
- ✅ First time using tool

**When to disable** (use `--no-sut-check` in CLI):
- ❌ Stable development systems
- ❌ Quick voltage checks
- ❌ Need fast operations (~20s vs 300s)

---

## ⚠️ Cold Reset Protection

### What is a Cold Reset?

When voltage/frequency changes are too aggressive, the system may **power off completely** (cold reset) instead of doing a normal warm reset. This is hardware protecting itself.

### What Happens Automatically

**Hardware Protection (Automatic)**:
1. System powers off (SLP_S5 state)
2. Fuses **automatically revert** to originally programmed values
3. No data corruption occurs
4. System returns to safe state

**Tool Detection**:
- Monitors every **2 seconds** during boot (0-90s critical window)
- Checks every **1 second** during initial 15 seconds
- Shows detailed dialog if cold reset detected

### If Cold Reset Occurs

**The tool will**:
1. Stop the operation immediately
2. Show detailed dialog explaining what happened
3. Attempt to verify fuses reverted correctly
4. Provide recommendations

**What YOU should do**:
- ✅ Try smaller voltage increments (5-10mV instead of 20mV+)
- ✅ Test at lower working points first
- ✅ Use WP Edit for fine-grained control
- ✅ Review hardware specifications
- ❌ Don't retry the same aggressive settings

**This is NORMAL**: Cold reset means you found the stability boundary. The hardware protected itself. Try smaller changes.

---

## 📁 Data Export

### Location
All data automatically saved to: `Logs/` folder

### File Naming
- VF Curve: `vf_curve_dump_<domain>_YYYYMMDD_HHMMSS.xlsx`
- Bump: `vf_curve_bump_<up/down>_<domain>_YYYYMMDD_HHMMSS.xlsx`
- WP Edit: `vf_curve_wp_edit_<domain>_YYYYMMDD_HHMMSS.xlsx`
- Flatten: `flatten_freq_<domain>_YYYYMMDD_HHMMSS.xlsx`
- Plots: Same name with `.png` extension

### Excel Structure
- **Show VF Curve**: Single sheet with WP, Voltage (V), Frequency (MHz)
- **Modifications**: "Before" and "After" sheets for comparison

---

## 🛠️ Troubleshooting

### "ITP Connection Error"
**Problem**: Can't connect to target

**Solutions**:
- Verify target is connected via ITP
- Check target is powered on
- Run `itp.unlock()` in Python console

---

### "Cold Reset Detected"
**Problem**: Dialog shows system powered off completely

**What happened**: Voltage change exceeded hardware limits

**Solutions**:
- This is NORMAL hardware protection
- Fuses automatically reverted to safe values
- Try smaller increments (5-10mV instead)
- Check dialog for detailed voltage comparison
- Use WP Edit for per-WP fine control

---

### "SUT Boot Timeout"
**Problem**: System didn't boot within 5 minutes

**Solutions**:
- Check SUT power state
- Verify ITP connection is stable
- Check network connectivity
- Try manual power cycle
- Disable SUT verification for faster operation

---

### "Progress Dialog Shows No Time"
**Problem**: Dialog only shows "Please wait"

**This is normal**: Time tracking was removed to prevent UI issues. Check console window for detailed progress updates.

---

## 💡 Best Practices

### Safety
1. **Start Small**: Begin with 5-10mV changes, not 20mV+
2. **One Domain at a Time**: Test each domain separately first
3. **Enable Verification**: Use SUT verification for important changes
4. **Check Results**: Always verify operation succeeded
5. **Keep Logs**: Don't delete logs - they provide traceability

### Efficiency
1. **Disable Verification for Speed**: On stable systems when doing quick checks
2. **Use WP Edit**: More precise than global bumps
3. **Check Console**: Real-time progress shown in console window
4. **Monitor Patterns**: If cold resets occur, you're at the limit

### Workflow
1. Show VF Curve (baseline)
2. Make small change (bump or WP edit)
3. Verify success
4. Test stability
5. Repeat if needed

---

## 📋 Supported Domains

### Platform-Specific Configuration

The tool is **project-agnostic** and supports any Intel platform by configuring `src/vf_domains.json` and `src/platform_config.json`.

**Domains are loaded from**: `src/vf_domains.json` (auto-populated by discovery on first launch; do not manually edit unless overriding a specific field)

### Fuse Root Discovery (Not Hardcoded)

The `fuse_root` field in `platform_config.json` (e.g., `"cdie.fuses"`) is **not a hard limit** — it is a fallback hint only. On every discovery run the tool dynamically enumerates **all live fuse roots** visible on the connected platform:

1. **Dynamic inspection** — `dir()` the `namednodes` global namespace; any top-level node (`cdie`, `cdie0`, `cdie1`, `soc`, …) that exposes a `.fuses` attribute is collected automatically. No code change is needed for any naming convention.
2. **Static fallback** — if `namednodes` is unavailable, the tool tries a priority list: `cdie.fuses → soc.fuses → die.fuses → chip.fuses → fuses`. Whichever resolve, all are used.
3. **Config supplement** — the `platform_config.json` `fuse_root` value is added to the list if not already found, so it is always covered.

All discovered roots are scanned and their register paths aggregated into one list. On a platform with both `cdie0.fuses` and `soc.fuses`, registers from **both** are discovered automatically.

**Each domain defines**:
- Label (display name)
- Working point count (3-10 WPs)
- Frequency multiplier (33.33, 50, or 100 MHz)
- Fuse register paths (platform-specific)
- Voltage/frequency register names

**Common domain types**:
- **Core domains**: IA Core, Bigcore, Atom clusters, Ring
- **Graphics domains**: GT, Media, VPU
- **Uncore domains**: NCLK, QCLK, ADM

**To add/modify domains**: Edit `src/vf_domains.json` with your platform's hardware configuration. No code changes required.

---

## ⌨️ Quick Reference

| Operation | Selection | Duration | Modifies Hardware? |
|-----------|-----------|----------|-------------------|
| Show VF Curve | 1+ domains | Instant | No |
| Bump Voltages | 1+ domains | 20-300s | Yes |
| WP Edit (voltage + freq) | 1 domain only | 20-300s | Yes |
| Flatten Freq | 1 domain only | 20-300s | Yes |

**Duration depends on**:
- SUT Verification **Disabled**: ~20 seconds
- SUT Verification **Enabled**: Up to 300 seconds (5 minutes)

---

## 🔒 Important Notes

- **Intel Confidential**: For internal use only
- **Hardware Access**: Directly modifies fuse values
- **No Undo**: Changes require reset to apply, fuses can be read back
- **Cold Reset Safe**: Hardware automatically protects itself
- **Always Test**: Validate changes in safe environment first

---

# 💻 CLI Mode - Command-Line Interface

## Overview

The CLI provides scriptable access to all VF curve operations, perfect for automation, batch processing, remote execution, and CI/CD integration.

**Benefits:**
- **Automation**: Script voltage/frequency changes with `--yes` flag
- **Batch Operations**: Process multiple configurations
- **Remote Use**: Run over SSH/remote terminals  
- **CI/CD Integration**: Automated hardware validation
- **Fast Execution**: No GUI overhead

---

## CLI Quick Start

### Launch CLI

**From root directory:**
```bash
run_cli.bat list
run_cli.bat show --domains cluster0_bigcore
```

**From src/ directory:**
```bash
cd src
python vf_curve_manager_cli.py list
python vf_curve_manager_cli.py show --domains cluster0_bigcore ring
```

### All Available Commands

| Command | Purpose |
|---|---|
| `list` | List all configured VF domains |
| `show` | Read and display VF curves (read-only) |
| `bump` | Bump all WP voltages up or down by a fixed mV amount |
| `edit` | Set a specific working-point voltage and/or frequency |
| `flatten` | Set P0/P1/Pn frequency ratios to the same value |
| `customize` | Set custom P0/P1/Pn frequencies (MHz) |
| `sweep` | Sweep voltage offset from –N to +N mV in steps, recording pass/fail |
| `revert-last` | Undo the most recent voltage/frequency write |
| `scalars` | View or write scalar modifier registers (ITD, P0 override, downbin, deltas) |
| `dump-registers` | Export all discovered hardware registers to Excel |
| `edit-register` | View or write a single discovered register by name |

---

## CLI Commands

### 1. list - List Available Domains
```bash
python vf_curve_manager_cli.py list
```

Shows all configured VF domains with their properties.

---

### 2. show - Display VF Curves
```bash
# Show single domain
python vf_curve_manager_cli.py show --domains cluster0_bigcore

# Show multiple domains
python vf_curve_manager_cli.py show --domains cluster0_bigcore ring cluster1_atom

# Disable plot interpolation
python vf_curve_manager_cli.py show --domains ring --no-interpolate
```

**Output**: Excel files and PNG plots in `Logs/` folder

---

### 3. bump - Bump Voltages Up/Down
```bash
# Bump up by 10mV (interactive)
python vf_curve_manager_cli.py bump --domains cluster0_bigcore --value 10 --direction up

# Bump down by 5mV (automated with --yes)
python vf_curve_manager_cli.py bump --domains ring --value 5 --direction down --yes

# Multiple domains
python vf_curve_manager_cli.py bump --domains cluster0_bigcore ring --value 10 --direction up --yes
```

---

### 4. edit - Edit Specific WP Voltages
```bash
# Edit single WP
python vf_curve_manager_cli.py edit --domain cluster0_bigcore --wp 0:850

# Edit multiple WPs
python vf_curve_manager_cli.py edit --domain cluster0_bigcore --wp 0:850 --wp 1:800 --wp 2:750 --yes
```

**Format**: `--wp WP_INDEX:VOLTAGE_MV`

---

### 5. flatten - Flatten Frequency Ratios
```bash
# Flatten to P1 ratio
python vf_curve_manager_cli.py flatten --domain cluster0_bigcore --target p1 --yes

# Flatten to P0 ratio
python vf_curve_manager_cli.py flatten --domain ring --target p0 --yes
```

---

### 6. customize - Custom Frequency Points
```bash
# Set custom P0/P1/Pn frequencies
python vf_curve_manager_cli.py customize --domain cluster0_bigcore --p0 4500 --p1 1500 --pn 400 --yes
```

---

### 7. sweep - Voltage Sweep Characterisation
```bash
# Sweep from -50mV to +50mV in 10mV steps (will prompt for confirmation)
python vf_curve_manager_cli.py sweep --domain cluster0_bigcore --from -50 --to 50 --step 10

# Automated sweep (no prompts)
python vf_curve_manager_cli.py sweep --domain cluster0_bigcore --from -20 --to 20 --step 5 --yes
```

**Arguments**:
| Argument | Required | Description |
|---|---|---|
| `--domain` | Yes | Single domain to sweep |
| `--from` | Yes | Start offset in mV relative to current baseline (can be negative) |
| `--to` | Yes | End offset in mV relative to current baseline |
| `--step` | Yes | Step size in mV (must be positive integer) |
| `--yes` | No | Skip confirmation prompt |

**What it does**: Steps voltage from `--from` to `--to` in `--step` increments. At each step it applies the offset, resets the target, and records pass/fail. Results are saved to Excel in `Logs/`. Stops early if a cold reset is detected.

**Use when**: Finding the voltage margin boundary for a domain.

---

### 8. revert-last - Undo Last Operation
```bash
# Preview what will be reverted (shows undo log entry)
python vf_curve_manager_cli.py revert-last

# Apply revert without prompting
python vf_curve_manager_cli.py revert-last --yes
```

**What it does**: Reads the undo log written by the last bump/edit/flatten/customize/sweep operation and restores the previous register values via the full hardware write flow (load_fuse_ram → write → flush → reset → verify).

**Use when**: You want to undo a voltage change without having to remember the exact original values.

> **Note**: Only the **most recent** operation is stored in the undo log. A second `revert-last` cannot re-do the original state — use `show` to verify current values first.

---

### 9. scalars - Scalar Modifier Registers
Scalar modifiers are individual hardware registers that adjust behaviour but are **not** part of the WP voltage/frequency curve table. Types discovered automatically:

| Type | Description |
|---|---|
| `itd_voltage` | Integrated VR thermal device voltage threshold |
| `itd_slope` | IVR thermal device slope / intercept |
| `p0_override` | Per-domain P0 turbo ratio override |
| `downbin` | Frequency downbin limit |
| `mct_delta` | MCT (Multi-Core Turbo) voltage delta |
| `atom_delta` | Atom cluster voltage delta |
| `acode_min` | Minimum adaptive code |

#### 9a. scalars show — Read all scalar modifiers
```bash
# Show all scalar modifiers with values and descriptions
python vf_curve_manager_cli.py scalars show

# Filter to a specific type
python vf_curve_manager_cli.py scalars show --type itd_voltage
python vf_curve_manager_cli.py scalars show --type p0_override
python vf_curve_manager_cli.py scalars show --type downbin
python vf_curve_manager_cli.py scalars show --type mct_delta
python vf_curve_manager_cli.py scalars show --type atom_delta
python vf_curve_manager_cli.py scalars show --type acode_min
python vf_curve_manager_cli.py scalars show --type itd_slope
```

**Output**: Table with register name, type, domain, raw value, converted physical value, and hardware description.

#### 9b. scalars edit — Write a scalar modifier
```bash
# Write ITD voltage threshold (specify physical value in mV)
python vf_curve_manager_cli.py scalars edit --key fw_fuses_cluster0_bigcore_itd_cutoff_v --value 850

# Write P0 override ratio (specify physical value in MHz)
python vf_curve_manager_cli.py scalars edit --key fw_fuses_ia_p0_ratio_avx --value 3600

# Write without confirmation (automation)
python vf_curve_manager_cli.py scalars edit --key fw_fuses_itd_cutoff_v_0 --value 900 --yes
```

**Arguments**:
| Argument | Required | Description |
|---|---|---|
| `--key` | Yes | Exact register name from `scalar_modifiers` in `vf_domains.json` |
| `--value` | Yes | Physical value: **mV** for voltage types, **MHz** for ratio/frequency types, raw integer for others |
| `--yes` | No | Skip confirmation prompt |

**Write flow**: load_fuse_ram → convert physical → raw LSB → write → flush → resettarget → verify. Before/after Excel saved to `Logs/`.

> **Tip**: Run `scalars show` first to see exact register names and current values.

---

### 10. dump-registers - Export All Discovered Registers
```bash
# Export all registers to Excel
python vf_curve_manager_cli.py dump-registers

# Export only active registers
python vf_curve_manager_cli.py dump-registers --active-only
```

**Output**: Excel file in `Logs/` — columns: Name, Value (dec), Value (hex), Converted, Active, Domain, Category, Fuse Path, Description

**Active registers** include non-zero value registers AND registers where 0 is a valid programmed state (adder/delta/index types).

If no cache exists, automatically runs full discovery first.

---

### 12. edit-register - View or Write an Individual Register
```bash
# View register info from cache (no ITP needed)
python vf_curve_manager_cli.py edit-register --name ia_vf_voltage_reg_adder_cluster0

# Partial name match also works
python vf_curve_manager_cli.py edit-register --name adder_cluster0

# Write a new value via ITP (full hardware flow)
python vf_curve_manager_cli.py edit-register --name ia_vf_voltage_reg_adder_cluster0 --set-value 12
python vf_curve_manager_cli.py edit-register --name ia_vf_voltage_reg_adder_cluster0 --set-value 0x0c --yes
```

**View output example**:
```
  Register   : ia_vf_voltage_reg_adder_cluster0
  Value (dec): 12
  Value (hex): 0xc
  Converted  : 30 mV
  Active     : Yes
  Domain     : bigcore
  Category   : vf_curve
  Fuse Path  : cdie.fuses.punit_fuses
  Description: Voltage adder register — resolution of ~2.5mV ...
```

**Write flow** (with `--set-value`): `load_fuse_ram → write → flush_fuse_ram → resettarget → verify`

---

### Global Flags

Global flags must be placed **before** the command name.

| Flag | Effect | Default |
|---|---|---|
| `--rediscover` | Force full hardware re-discovery before running the command | Off |
| `--no-sut-check` | Disable SUT boot verification (~20s instead of ~300s) | SUT check is **ON** by default |
| `--mock` | Run in mock mode — no ITP/hardware needed; reads from `vf_discovery_cache.json` | Off |
| `--json` | Emit results as machine-readable JSON to stdout (for CI/CD pipelines) | Off |

> **Note**: `--enable-sut-check` is a legacy alias kept for backward compatibility; SUT verification is **on by default** starting from v2.2. Use `--no-sut-check` to opt out.

```bash
# Re-discover then list
python vf_curve_manager_cli.py --rediscover list

# Re-discover then dump active registers
python vf_curve_manager_cli.py --rediscover dump-registers --active-only

# Skip SUT boot wait (faster, for stable systems)
python vf_curve_manager_cli.py --no-sut-check bump --domains ring --value 10 --direction up --yes

# Mock mode — no hardware required (useful for scripting / CI without a board)
python vf_curve_manager_cli.py --mock list
python vf_curve_manager_cli.py --mock show --domains cluster0_bigcore

# Machine-readable JSON output
python vf_curve_manager_cli.py --json list
python vf_curve_manager_cli.py --json show --domains ring
```

---

## Automation Mode

### Skip Confirmations with `--yes`

All commands that modify hardware require `--yes` flag for automation:

```bash
# WITHOUT --yes (Interactive - prompts "Continue? (yes/no):")
python vf_curve_manager_cli.py bump --domains ring --value 10 --direction up

# WITH --yes (Automated - no prompts)
python vf_curve_manager_cli.py bump --domains ring --value 10 --direction up --yes
```

**Commands supporting `--yes`:**
- `bump --yes`
- `edit --yes`
- `flatten --yes`
- `customize --yes`
- `sweep --yes`
- `revert-last --yes`
- `scalars edit --yes`
- `edit-register --set-value <N> --yes`

> **Note:** `show`, `list`, `dump-registers`, `scalars show`, and `edit-register` (view only, no `--set-value`) are read-only and never require confirmation.

---

## Scripting Examples

### Bash - Voltage Sweep
```bash
#!/bin/bash
cd src
for voltage in 0 10 20 30 40 50; do
    echo "Testing ${voltage}mV bump..."
    python vf_curve_manager_cli.py bump \
        --domains cluster0_bigcore \
        --value $voltage \
        --direction up \
        --yes
    
    if [ $? -ne 0 ]; then
        echo "Failed at ${voltage}mV"
        break
    fi
done
```

### PowerShell - Multiple Domains
```powershell
cd src
$domains = @("cluster0_bigcore", "ring", "cluster1_atom")
foreach ($domain in $domains) {
    python vf_curve_manager_cli.py bump `
        --domains $domain `
        --value 10 `
        --direction up `
        --yes
}
```

### Python - Advanced Automation
```python
#!/usr/bin/env python3
import subprocess
import os

os.chdir('src')

def run_cli(command):
    result = subprocess.run(
        ['python', 'vf_curve_manager_cli.py'] + command,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    return result.returncode

# Apply voltage changes
for v in [5, 10, 15, 20]:
    print(f"\nApplying {v}mV bump...")
    rc = run_cli(['bump', '--domains', 'cluster0_bigcore', 
                  '--value', str(v), '--direction', 'up', '--yes'])
    
    if rc == 2:  # Cold reset detected
        print("Cold reset - stopping")
        break
    elif rc != 0:
        print("Error - stopping")
        break
```

---

## CLI Exit Codes

For scripting and automation:

- **0**: Success
- **1**: Error (invalid arguments, operation failed)
- **2**: Cold reset detected (system powered off)
- **130**: Cancelled by user (Ctrl+C)

**Example:**
```bash
python vf_curve_manager_cli.py bump --domains ring --value 10 --direction up --yes
if [ $? -eq 2 ]; then
    echo "Cold reset detected - fuses auto-reverted"
fi
```

---

## CLI vs GUI Comparison

| Feature | CLI | GUI |
|---------|-----|-----|
| **Speed** | Fast | Moderate |
| **Automation** | ✓ `--yes` flag | ✗ Manual |
| **Remote Use** | ✓ SSH-friendly | ✗ Requires display |
| **Visualization** | Saved plots | Real-time interactive |
| **Batch Ops** | Easy scripting | Manual repetition |
| **Learning Curve** | Command syntax | Point-and-click |
| **Register Browser** | `dump-registers` / `edit-register` | Discovered Registers tab |
| **Scalar Modifiers** | `scalars show` / `scalars edit` | Scalar Modifiers dialog |
| **Voltage Sweep** | `sweep` command | Not available |
| **Undo Last Write** | `revert-last` command | Not available |
| **Mock/Offline mode** | `--mock` flag | Not available |
| **JSON output** | `--json` flag | Not available |
| **Force Re-discover** | `--rediscover` flag | Startup dialog "Yes, Refresh" |
| **Converted Units** | `edit-register` view | Converted column in Registers tab |
| **SUT check toggle** | `--no-sut-check` (off) / default (on) | Enable SUT Verification checkbox |

**Use CLI for:**
- Automation and scripting
- Remote/headless systems
- Batch processing
- CI/CD pipelines

**Use GUI for:**
- Exploration and learning
- Visual analysis
- Ad-hoc testing
- Interactive workflows

---

## 📞 Support

**For issues or questions**:
1. Check this guide's troubleshooting section
2. Review console output for detailed error messages
3. Check `Logs/` folder for operation history
4. Contact: anil.kumar.dadi@intel.com

---

**End of User Guide** - Safe VF Curve Management in GUI and CLI! 🚀
