# Platform Migration Guide

**How to Configure VF Curve Manager Tool v2.2 for Your Intel Platform**

---

## Overview

This guide shows you how to configure the VF Curve Manager Tool for a new Intel platform. The tool is **100% project-agnostic** — for most platforms, configuration is **fully automatic** via the built-in discovery pipeline.

**Recommended path**: Connect to hardware via ITP, then run `run_cli.bat --rediscover list` (or launch the GUI and choose "Yes, Refresh"). The tool detects the platform, scans fuse paths, discovers all registers, and populates `vf_domains.json` automatically. No manual steps are required.

**Manual configuration**: Use this guide only if you need to override auto-discovered values, understand the JSON schema, or configure a platform where auto-discovery produces incomplete results.

**Time to configure (manual)**: 30–60 minutes per platform  
**Required knowledge**: ITP usage, basic JSON editing  
**Required tools**: ITP connection to target platform

---

## Quick Migration Checklist

**Automatic (Recommended)**:
- [ ] Add platform entry to `src/platform_config.json` (copy `generic` as base if needed)
- [ ] Connect to target via ITP
- [ ] Run `run_cli.bat --rediscover list` (or launch GUI → "Yes, Refresh")
- [ ] Verify domains appear and values are correct
- [ ] Commit `platform_config.json` (and optionally the generated `vf_domains.json`)

**Manual override** (if auto-discovery is incomplete):
- [ ] Identify voltage-frequency domains on your platform
- [ ] Connect to target via ITP
- [ ] Extract fuse paths for each domain
- [ ] Extract voltage register names
- [ ] Extract frequency ratio register names
- [ ] Determine working point count
- [ ] Determine frequency multiplier
- [ ] Create/update `vf_domains.json`
- [ ] Validate configuration
- [ ] Test with Show VF Curve operation

---

## Step 1: Identify Your Platform Domains

### Common Domain Types

Most Intel platforms have similar domain categories:

**Core Domains**:
- IA Core / Bigcore / Performance cores
- Atom / Efficient cores / Low-power cores
- Ring interconnect

**Graphics Domains**:
- GT (Graphics Technology)
- Media engine
- VPU (Vision Processing Unit)

**Uncore Domains**:
- NCLK (North clock)
- QCLK / SA_QCLK (System Agent)
- ADM (Audio/Display Module)

**Platform-Specific**:
- Check your platform's PUnit documentation
- Review ITP fuse maps
- Consult platform architecture specifications

### Find Domains via ITP

```python
# In ITP Python console
import namednodes
itp.unlock()

# Navigate to fuse object
fuses = namednodes.cdie.fuses.punit_fuses  # Example path - varies by platform

# List available registers (use tab completion)
fuses.<TAB>  # Shows all available registers

# Look for patterns like:
# fw_fuses_<domain>_vf_voltage_0
# fw_fuses_<domain>_vf_ratio_0
```

---

## Step 2: Extract Fuse Paths

### Locate Fuse Object Path

The fuse path points to the ITP object containing voltage/frequency registers.

**Common patterns**:
```python
# WCL example
"fuse_path": "cdie.fuses.punit_fuses"

# ARL example
"fuse_path": "cdie.fuses.dmu_fuse"

# Generic pattern
"fuse_path": "<die>.<fuse_container>.<punit_object>"
```

### How to Find Your Path

```python
# In ITP console
import namednodes

# Try common paths
try:
    fuses = namednodes.cdie.fuses.punit_fuses
    print("Found: cdie.fuses.punit_fuses")
except:
    pass

try:
    fuses = namednodes.cdie.fuses.dmu_fuse
    print("Found: cdie.fuses.dmu_fuse")
except:
    pass

# Explore namednodes structure
namednodes.cdie.<TAB>
namednodes.cdie.fuses.<TAB>
```

### Fuse RAM Path (Optional)

Some platforms require separate `fuse_ram_path`:

```json
{
  "fuse_path": "cdie.fuses.punit_fuses",
  "fuse_ram_path": "cdie.fuses"
}
```

If not specified, defaults to `fuse_path`.

---

## Step 3: Extract Register Names

### Voltage Registers

Voltage registers typically follow this pattern:
```
fw_fuses_<domain>_vf_voltage_<index>
```

**Example for IA Core domain**:
```python
# In ITP
fuses = namednodes.cdie.fuses.punit_fuses

# Check if voltage registers exist
print(fuses.fw_fuses_ia_core_vf_voltage_0)
print(fuses.fw_fuses_ia_core_vf_voltage_1)
# ... continue for all working points
```

**Counting working points**:
```python
# Find how many voltage registers exist
wp_count = 0
for i in range(20):  # Try up to 20
    try:
        reg_name = f"fw_fuses_ia_core_vf_voltage_{i}"
        reg = getattr(fuses, reg_name)
        wp_count = i + 1
    except:
        break

print(f"Working points: {wp_count}")
```

### Frequency Ratio Registers

Frequency ratio registers typically follow:
```
fw_fuses_<domain>_vf_ratio_<index>
```

**Example**:
```python
# In ITP
print(fuses.fw_fuses_ia_core_vf_ratio_0)
print(fuses.fw_fuses_ia_core_vf_ratio_1)
# ... continue for all working points
```

**Note**: On most platforms voltage and ratio register counts match. On some platforms (e.g., NVL) there may be fewer ratio registers than voltage working points — the tool handles this automatically, treating missing WPs as frequency-unknown.

---

## Step 4: Determine Frequency Multiplier

The frequency multiplier converts ratio values to MHz.

**Common values**:
- `100` - For core domains (IA Core, Ring, Atom)
- `50` - For graphics/media domains (GT, Media, VPU)
- `33.33` - For some uncore domains (QCLK)

### How to Determine

```python
# In ITP - read a known frequency ratio
fuses = namednodes.cdie.fuses.punit_fuses
ratio = fuses.fw_fuses_ia_core_vf_ratio_0.read()

# Check platform specs for expected P0 frequency
# If P0 should be 3000 MHz and ratio is 30, multiplier = 3000/30 = 100
# If P0 should be 1500 MHz and ratio is 30, multiplier = 1500/30 = 50
```

**Quick reference**:
- If ratios are in range 10-40 and frequencies are GHz range → multiplier = 100
- If ratios are in range 20-40 and frequencies are MHz range → multiplier = 50
- For QCLK domains → usually 33.33

---

## Step 5: Create vf_domains.json Entry

### Template

```json
{
  "domains": {
    "domain_name": {
      "label": "Display Name",
      "freq_multiplier": 100,
      "wp_count": 10,
      "fuse_path": "cdie.fuses.punit_fuses",
      "fuse_ram_path": "cdie.fuses",
      "vf_voltage": [
        "fw_fuses_domain_vf_voltage_0",
        "fw_fuses_domain_vf_voltage_1",
        "fw_fuses_domain_vf_voltage_2",
        "fw_fuses_domain_vf_voltage_3",
        "fw_fuses_domain_vf_voltage_4",
        "fw_fuses_domain_vf_voltage_5",
        "fw_fuses_domain_vf_voltage_6",
        "fw_fuses_domain_vf_voltage_7",
        "fw_fuses_domain_vf_voltage_8",
        "fw_fuses_domain_vf_voltage_9"
      ],
      "vf_ratio": [
        "fw_fuses_domain_vf_ratio_0",
        "fw_fuses_domain_vf_ratio_1",
        "fw_fuses_domain_vf_ratio_2",
        "fw_fuses_domain_vf_ratio_3",
        "fw_fuses_domain_vf_ratio_4",
        "fw_fuses_domain_vf_ratio_5",
        "fw_fuses_domain_vf_ratio_6",
        "fw_fuses_domain_vf_ratio_7",
        "fw_fuses_domain_vf_ratio_8",
        "fw_fuses_domain_vf_ratio_9"
      ],
      "flatten_freq_ratios": {
        "p0": "fw_fuses_domain_p0_ratio",
        "p1": "fw_fuses_domain_p1_ratio",
        "pn": "fw_fuses_domain_pn_ratio"
      }
    }
  }
}
```

### Field Descriptions

| Field | Required | Description |
|-------|----------|-------------|
| `label` | Yes | UI display name (e.g., "IA Core", "Bigcore") |
| `freq_multiplier` | Yes | 33.33, 50, or 100 MHz |
| `wp_count` | Yes | Number of working points (must match array lengths) |
| `fuse_path` | Yes | ITP path to fuse object |
| `fuse_ram_path` | No | Defaults to `fuse_path` if not specified |
| `vf_voltage` | Yes | Array of voltage register names (length = wp_count) |
| `vf_ratio` | No* | Array of frequency ratio register names — omit entirely for delta/voltage-only domains |
| `flatten_freq_ratios` | No | Optional - for flatten frequency operation |

*`vf_ratio` may also have **fewer entries than `wp_count`** (e.g., NVL bigcore: 12 ratio vs 24 voltage WPs). The tool uses `vf_voltage` length as authoritative for `wp_count`.

---

## Step 6: Real-World Examples

### Example 1: ARL Platform (Arrow Lake)

```json
{
  "domains": {
    "ia_core": {
      "label": "IA Core",
      "freq_multiplier": 100,
      "wp_count": 9,
      "fuse_path": "cdie.fuses.dmu_fuse",
      "fuse_ram_path": "cdie.fuses",
      "vf_voltage": [
        "fw_fuses_ia_vf_voltage_0",
        "fw_fuses_ia_vf_voltage_1",
        "fw_fuses_ia_vf_voltage_2",
        "fw_fuses_ia_vf_voltage_3",
        "fw_fuses_ia_vf_voltage_4",
        "fw_fuses_ia_vf_voltage_5",
        "fw_fuses_ia_vf_voltage_6",
        "fw_fuses_ia_vf_voltage_7",
        "fw_fuses_ia_vf_voltage_8"
      ],
      "vf_ratio": [
        "fw_fuses_ia_vf_ratio_0",
        "fw_fuses_ia_vf_ratio_1",
        "fw_fuses_ia_vf_ratio_2",
        "fw_fuses_ia_vf_ratio_3",
        "fw_fuses_ia_vf_ratio_4",
        "fw_fuses_ia_vf_ratio_5",
        "fw_fuses_ia_vf_ratio_6",
        "fw_fuses_ia_vf_ratio_7",
        "fw_fuses_ia_vf_ratio_8"
      ],
      "flatten_freq_ratios": {
        "p0": "fw_fuses_ia_p0_ratio",
        "p1": "fw_fuses_ia_p1_ratio",
        "pn": "fw_fuses_ia_pn_ratio"
      }
    }
  }
}
```

### Example 2: WCL Platform (Wildcat Lake)

```json
{
  "domains": {
    "cluster0_bigcore": {
      "label": "Bigcore",
      "freq_multiplier": 100,
      "wp_count": 10,
      "fuse_path": "cdie.fuses.punit_fuses",
      "fuse_ram_path": "cdie.fuses",
      "vf_voltage": [
        "fw_fuses_cluster0_bigcore_vf_voltage_0",
        "fw_fuses_cluster0_bigcore_vf_voltage_1",
        "fw_fuses_cluster0_bigcore_vf_voltage_2",
        "fw_fuses_cluster0_bigcore_vf_voltage_3",
        "fw_fuses_cluster0_bigcore_vf_voltage_4",
        "fw_fuses_cluster0_bigcore_vf_voltage_5",
        "fw_fuses_cluster0_bigcore_vf_voltage_6",
        "fw_fuses_cluster0_bigcore_vf_voltage_7",
        "fw_fuses_cluster0_bigcore_vf_voltage_8",
        "fw_fuses_cluster0_bigcore_vf_voltage_9"
      ],
      "vf_ratio": [
        "fw_fuses_cluster0_bigcore_vf_ratio_0",
        "fw_fuses_cluster0_bigcore_vf_ratio_1",
        "fw_fuses_cluster0_bigcore_vf_ratio_2",
        "fw_fuses_cluster0_bigcore_vf_ratio_3",
        "fw_fuses_cluster0_bigcore_vf_ratio_4",
        "fw_fuses_cluster0_bigcore_vf_ratio_5",
        "fw_fuses_cluster0_bigcore_vf_ratio_6",
        "fw_fuses_cluster0_bigcore_vf_ratio_7",
        "fw_fuses_cluster0_bigcore_vf_ratio_8",
        "fw_fuses_cluster0_bigcore_vf_ratio_9"
      ],
      "flatten_freq_ratios": {
        "p0": "fw_fuses_ia_p0_ratio",
        "p1": "fw_fuses_ia_p1_ratio",
        "pn": "fw_fuses_ia_pn_ratio"
      }
    }
  }
}
```

### Key Differences Between ARL and WCL

| Aspect | ARL | WCL |
|--------|-----|-----|
| Fuse path | `cdie.fuses.dmu_fuse` | `cdie.fuses.punit_fuses` |
| Domain name | `ia_core` | `cluster0_bigcore` |
| Register prefix | `fw_fuses_ia_` | `fw_fuses_cluster0_bigcore_` |
| Working points | 9 | 10 |

---

### Example 3: NVL Platform (Nova Lake)

NVL has several unique characteristics compared to ARL/WCL:
- Fuse container is `dmu_fuse` (not `punit_fuses`)
- Bigcore has **24 voltage WPs but only 12 ratio WPs** — ratio array is shorter than voltage array
- Delta (voltage-only) domains omit `vf_ratio` entirely
- Flatten keys use `fmax_at_vmin_ratio` / `atom_fmax_at_vmin_ratio` (not `pN_ratio`)

```json
{
  "domains": {
    "core0_fuse_core0_bigcore_base_vf": {
      "label": "Bigcore Base VF",
      "freq_multiplier": 100,
      "wp_count": 24,
      "fuse_path": "cdie.fuses.dmu_fuse",
      "fuse_ram_path": "cdie.fuses",
      "vf_voltage": [
        "fw_fuses_ia_core0_bigcore_base_vf_voltage_0",
        "fw_fuses_ia_core0_bigcore_base_vf_voltage_1"
      ],
      "vf_ratio": [
        "fw_fuses_ia_core0_bigcore_base_vf_ratio_0",
        "fw_fuses_ia_core0_bigcore_base_vf_ratio_1"
      ],
      "flatten_freq_ratios": {
        "fmax_vmin": "fw_fuses_ia_fmax_at_vmin_ratio"
      }
    },
    "dmu_fuse_atom_ccp0_delta": {
      "label": "Atom CCP0 Delta",
      "freq_multiplier": 100,
      "wp_count": 8,
      "fuse_path": "cdie.fuses.dmu_fuse",
      "fuse_ram_path": "cdie.fuses",
      "vf_voltage": [
        "fw_fuses_atom_ccp0_delta_vf_voltage_0",
        "fw_fuses_atom_ccp0_delta_vf_voltage_1"
      ]
    }
  }
}
```

> **Note**: `dmu_fuse_atom_ccp0_delta` has no `vf_ratio` — this is correct for delta domains (voltage adjustment only).

### Key Differences: ARL vs WCL vs NVL

| Aspect | ARL | WCL | NVL |
|--------|-----|-----|-----|
| Fuse path | `cdie.fuses.dmu_fuse` | `cdie.fuses.punit_fuses` | `cdie.fuses.dmu_fuse` |
| Register prefix | `fw_fuses_ia_` | `fw_fuses_cluster0_bigcore_` | `fw_fuses_ia_core0_bigcore_` |
| Working points (core) | 9 | 10 | 24 voltage / 12 ratio |
| Delta domains | No | No | Yes (no `vf_ratio`) |
| Flatten key name | `pN_ratio` | `pN_ratio` | `fmax_at_vmin_ratio` |

---

## Step 7: Validation

### Validate JSON Syntax

```bash
# Use Python to validate JSON
python -m json.tool src/vf_domains.json
```

If valid, it will pretty-print the JSON. If invalid, it will show syntax errors.

### Validate Configuration in Tool

1. Launch the tool:
   ```bash
   run_vf_curve_manager_ui.bat
   ```

2. Check console output:
   ```
   [1] Loading hardware configuration...
       ✓ Loaded X domains successfully
   ```

3. Verify domains appear in UI sidebar

### Test with Show VF Curve

1. Select a single domain
2. Click **Show VF Curve**
3. Check output for errors
4. Verify Excel file is created
5. Verify voltage/frequency values are reasonable

**Expected values**:
- Voltages: Typically 0.5V - 1.5V
- Frequencies: Typically 500 MHz - 6000 MHz for cores
- If values are 0 or unreasonable, check register names

---

## Step 8: Common Issues & Solutions

### Issue: Domain Doesn't Appear in UI

**Cause**: JSON syntax error or configuration validation failed

**Solution**:
1. Check console output for error messages
2. Validate JSON syntax: `python -m json.tool src/vf_domains.json`
3. Verify all required fields are present
4. Check wp_count matches array lengths

### Issue: Voltages Show as 0.0V

**Cause**: Incorrect voltage register names

**Solution**:
1. Verify register names in ITP:
   ```python
   fuses = namednodes.cdie.fuses.punit_fuses
   print(fuses.fw_fuses_<domain>_vf_voltage_0.read())
   ```
2. Update `vf_voltage` array in JSON
3. Restart tool

### Issue: Frequencies Are Wrong (e.g., 30 MHz instead of 3000 MHz)

**Cause**: Incorrect frequency multiplier

**Solution**:
1. Calculate correct multiplier: `expected_freq_mhz / ratio_value`
2. Common values: 100 (cores), 50 (graphics), 33.33 (uncore)
3. Update `freq_multiplier` in JSON
4. Restart tool

### Issue: Bump/WP Edit Fails with "Fuse Write Error"

**Cause**: Incorrect fuse_path or permissions

**Solution**:
1. Verify fuse_path in ITP:
   ```python
   fuses = namednodes.cdie.fuses.punit_fuses
   fuses.fw_fuses_<domain>_vf_voltage_0.write(100)
   ```
2. Ensure ITP is unlocked: `itp.unlock()`
3. Check fuse_ram_path is correct

### Issue: Tool Crashes on Startup

**Cause**: Malformed JSON

**Solution**:
1. Validate JSON: `python -m json.tool src/vf_domains.json`
2. Check for:
   - Missing commas
   - Extra commas
   - Mismatched brackets
   - Missing quotes

---

## Step 9: Adding Multiple Domains

### Complete Platform Configuration

Once you've validated one domain, add the rest:

```json
{
  "domains": {
    "ia_core": { /* ... */ },
    "ring": { /* ... */ },
    "atom": { /* ... */ },
    "gt": { /* ... */ },
    "media": { /* ... */ },
    "vpu": { /* ... */ }
  }
}
```

**Tips**:
- Copy-paste the validated domain as a template
- Update domain name, label, and register names
- Validate after each domain addition
- Test each domain individually with Show VF Curve

---

## Migration Workflow Summary

```
1. Research Platform
   ├── Review PUnit documentation
   ├── Check architecture specs
   └── List expected domains

2. ITP Exploration
   ├── Connect to target
   ├── Find fuse paths
   ├── Discover register names
   └── Count working points

3. Create JSON Entry
   ├── Start with one domain
   ├── Fill in all fields
   └── Validate syntax

4. Test Single Domain
   ├── Launch tool
   ├── Verify domain appears
   ├── Run Show VF Curve
   └── Check output values

5. Expand Configuration
   ├── Add remaining domains
   ├── Test each domain
   └── Document any quirks

6. Final Validation
   ├── Test all operations
   ├── Verify multi-domain operations
   └── Test on multiple SUTs
```

---

## Platform-Specific Notes

### ARL (Arrow Lake)
- Uses `dmu_fuse` instead of `punit_fuses`
- Domain names: `ia_core`, `ring`, `atom`, `adm`, etc.
- Typically 9 working points for cores

### WCL (Wildcat Lake)
- Uses `punit_fuses`
- Domain names include cluster prefixes: `cluster0_bigcore`, `cluster1_atom`
- Typically 10 working points for cores
- Has `sa_qclk` with 33.33 MHz multiplier

### NVL (Nova Lake)
- Uses `dmu_fuse` (same as ARL) — **not** `punit_fuses`
- Bigcore domains: 24 voltage WPs but only 12 ratio registers — tool handles the mismatch automatically
- Delta domains (e.g., `*_delta`): **omit `vf_ratio` entirely** — these are voltage-offset-only domains
- Flatten keys use `fmax_at_vmin_ratio` (bigcore) and `atom_fmax_at_vmin_ratio` (atom) instead of the usual `pN_ratio` names
- Auto-discovery (`discovery_core.py`) uses `_is_system_container()` to correctly route IA/atom frequencies from the `dmu_fuse` container
- Approximately 2860 active registers across ~80 paths

### MTL (Meteor Lake)
- Check specific MTL PUnit documentation
- May have different cluster naming
- Verify working point counts per domain

### LNL (Lunar Lake)
- Consult platform-specific documentation
- May have new domain types
- Verify register naming conventions

---

## Best Practices

### Configuration Management

1. **Version Control**: Keep `vf_domains.json` in version control
2. **Backup**: Save working configurations before modifications
3. **Documentation**: Comment unusual configurations
4. **Testing**: Always test after changes

### Register Name Discovery

1. **Use ITP Tab Completion**: Explore `namednodes` with `<TAB>`
2. **Pattern Recognition**: Look for `fw_fuses_<domain>_vf_*` patterns
3. **Cross-Reference**: Check PUnit register maps
4. **Verify**: Read register values to confirm they make sense

### Troubleshooting Strategy

1. **Start Simple**: Configure one domain first
2. **Incremental Testing**: Test after each change
3. **Check Console**: Always review console output
4. **Validate Data**: Verify voltage/frequency ranges are reasonable
5. **Compare Platforms**: Use working configs as reference

---

## Support

**Having trouble configuring your platform?**

1. Check this guide's troubleshooting section
2. Compare with existing platform configurations in `vf_domains.json`
3. Review [DEVELOPER_REFERENCE.md](DEVELOPER_REFERENCE.md) for JSON schema details
4. Contact: anil.kumar.dadi@intel.com with:
   - Platform name
   - ITP fuse path you're using
   - Error messages from console
   - Sample register names you discovered

---

**End of Platform Migration Guide** - Happy Configuring! 🚀
