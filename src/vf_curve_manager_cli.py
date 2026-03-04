"""
VF Curve Manager Tool v2.2 - Command Line Interface

Professional CLI for voltage-frequency curve management on Intel platforms.
Provides all GUI features through command-line interface.

Usage Examples:
    # Show VF curves for domains
    python vf_curve_manager_cli.py show --domains cluster0_bigcore ring

    # Bump voltages up by 10mV
    python vf_curve_manager_cli.py bump --domains cluster0_bigcore --value 10 --direction up

    # Edit specific WP voltages
    python vf_curve_manager_cli.py edit --domain cluster0_bigcore --wp 0:850 --wp 1:800 --wp 2:750

    # Flatten frequency to P1 ratio
    python vf_curve_manager_cli.py flatten --domain cluster0_bigcore --target p1

    # List available domains
    python vf_curve_manager_cli.py list

    # Enable SUT verification (boot checks)
    python vf_curve_manager_cli.py show --domains ring --enable-sut-check
"""

import logging
import sys
import os
import argparse
import json
import subprocess
import time
import traceback
from datetime import datetime

# Script is now in src/ directory, so current directory is already src/
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

# ── Detect mock mode BEFORE any Intel toolchain imports ─────────────────────────────
# This check runs before any import so that machines without the Intel stack
# can still run  run_cli.bat --mock <command>  without ImportError.
_MOCK_MODE = '--mock' in sys.argv

from utils.process_utils import terminate_openipc

log = logging.getLogger(__name__)


# ── Conditional Intel toolchain imports ────────────────────────────────────────────────
if not _MOCK_MODE:
    terminate_openipc()
    try:
        from pysvtools.pmext.services.regs import *
    except Exception as _regs_err:
        # Non-fatal on platforms where regs.py core-topology init fails
        # (e.g. Novalake/PantherCove TypeError in _update_devices_list).
        log.warning(f"pysvtools.pmext.services.regs import skipped: {_regs_err}")
    try:
        import namednodes
        import itpii
        import ipccli
    except ImportError as _ie:
        log.error(f"Intel toolchain import failed: {_ie}")
        log.info("        Use --mock flag to run without hardware.")
        sys.exit(1)
else:
    log.debug("Mock mode detected — skipping OpenIPC cleanup and ITP imports")
    # Define null stubs so references in this module don\'t raise NameError
    ipccli = None  # noqa: F841
    itpii = None   # noqa: F841

# Global ITP instances
ipc = None
itp = None

# Transient-error exit code (distinguishable from success=0, cold_reset=2, fatal=1)
EXIT_TRANSIENT_ERROR = 3


def init_itp():
    """Initialize ITP connection."""
    global ipc, itp

    # In mock mode ITP is not needed
    if _MOCK_MODE:
        log.debug("ITP initialization skipped")
        return True

    log.info("")
    log.info("=" * 80)
    log.info("  Intel® CVE VF Curve Manager Tool v2.2 - CLI Mode")
    log.info("=" * 80)
    log.info("")
    
    log.info("[1] Initializing ITP connection...")
    try:
        ipc = ipccli.baseaccess()
        itp = itpii.baseaccess(True)
        itp.unlock()
        log.info("    [+] ITP initialized successfully\n")
        return True
    except Exception as ex:
        log.info(f"    [x] ITP initialization failed: {ex}")
        return False


def setup_modules(enable_sut_check=True, mock_mode=False):
    """Setup and initialize all required modules."""
    try:
        from utils import hardware_access
        from core.config_loader import ConfigLoader
        from core.curve_engine import CurveEngine

        # Initialize hardware access module; pass globals() so get_fuse_object
        # resolves ITP root objects (cdie, soc, etc.) from this module's namespace
        # rather than relying on __main__, making it test-friendly.
        hardware_access.init_hardware(
            ipc, itp,
            enable_sut_check=enable_sut_check,
            namespace=globals(),
            mock_mode=mock_mode,
        )
        
        # Load configuration (in same directory)
        config_path = os.path.join(_current_dir, 'vf_domains.json')
        config_loader = ConfigLoader(config_path)

        # Drop domains whose fuse_path does not exist on this platform
        # (e.g. WildcatLake punit_fuses entries when running on Novalake).
        # Must be called after init_hardware() so the ITP namespace is live.
        if not mock_mode:
            config_loader.filter_unreachable_domains()

            # If filtering left zero domains this is a first run on a new platform —
            # auto-trigger discovery so the user doesn't have to pass --rediscover.
            if not config_loader.get_domain_list():
                log.info("No domains found for this platform — running auto-discovery...")
                from discovery.startup_discovery import maybe_run_discovery
                maybe_run_discovery(force=True)
                # Reload the freshly populated config
                config_loader = ConfigLoader(config_path)
                config_loader.filter_unreachable_domains()

            # Drop domains whose every vf_voltage WP reads as 0 — these are
            # unprogrammed / irrelevant for the current platform and should not
            # appear in the domain selector or CLI commands.
            config_loader.filter_zero_wp_domains()  # safe in CLI — no event loop to block

        # Validate configuration
        is_valid, msg = config_loader.validate_config()
        if not is_valid:
            log.error(f"Configuration validation failed: {msg}")
            return None, None
        
        # Create curve engine
        curve_engine = CurveEngine(config_loader)
        
        return curve_engine, config_loader
        
    except ImportError as ex:
        log.error(f"Failed to import modules: {ex}")
        log.info("Ensure all source files are present in src/ directory")
        return None, None
    except Exception as ex:
        log.error(f"Module setup failed: {ex}")
        return None, None


def cmd_list(args, config_loader):
    """List all available domains."""
    domains = config_loader.get_all_domains()

    # ── JSON output mode ────────────────────────────────────────────────────────────
    if getattr(args, 'json_output', False):
        output = {}
        for domain_name, domain_info in domains.items():
            output[domain_name] = {
                'label':       domain_info.get('label', domain_name.upper()),
                'wp_count':    domain_info.get('wp_count', 0),
                'fuse_path':   domain_info.get('fuse_path', 'N/A'),
                'has_flatten': config_loader.has_flatten_support(domain_name),
                'has_adder':   'vf_voltage_adder' in domain_info,
            }
        log.info(json.dumps({'status': 'ok', 'domains': output}, indent=2))
        return 0

    # ── Human-readable output ─────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 80)
    log.info("  Available VF Domains")
    log.info("=" * 80)
    log.info("")
    
    for domain_name, domain_info in domains.items():
        label = domain_info.get('label', domain_name.upper())
        wp_count = domain_info.get('wp_count', 0)
        fuse_path = domain_info.get('fuse_path', 'N/A')
        has_flatten = config_loader.has_flatten_support(domain_name)
        has_adder  = 'vf_voltage_adder' in domain_info
        has_delta  = 'vf_voltage_delta_idx1' in domain_info
        
        log.info(f"  Domain: {domain_name}")
        log.info(f"    Label:        {label}")
        log.info(f"    WP Count:     {wp_count}")
        log.info(f"    Fuse Path:    {fuse_path}")
        log.info(f"    Flatten:      {'Yes' if has_flatten else 'No'}")
        if has_adder:
            adder_count = len(domain_info['vf_voltage_adder'])
            log.info(f"    Voltage Adder: Yes ({adder_count} regs) — effective voltage = base + adder")
        if has_delta:
            log.info(f"    Delta VF:      Yes (idx1 + idx2 correction columns)")
        log.info("")
    
    log.info(f"Total: {len(domains)} domains")
    log.info("")


def cmd_revert_last(args, curve_engine):
    """Revert the last voltage operation recorded in the undo log."""
    result = curve_engine.revert_from_undo_log()

    if 'error' in result:
        if getattr(args, 'json_output', False):
            log.info(json.dumps({'status': 'error', 'message': result['error']}))
        else:
            log.error(f"{result['error']}")
        return 1

    if getattr(args, 'json_output', False):
        log.info(json.dumps({
            'status': 'ok',
            'reverted_op': result.get('reverted_op'),
            'domains': result.get('domains', []),
            'entries_remaining': result.get('entries_remaining', 0),
        }, indent=2))
        return 0

    log.info("")
    log.info("=" * 80)
    log.info("  Revert Last Operation")
    log.info("=" * 80)
    log.info(f"  Operation reverted : {result.get('reverted_op', 'unknown')}")
    log.info(f"  Domains            : {', '.join(result.get('domains', []))}")
    log.info(f"  Undo log remaining : {result.get('entries_remaining', 0)} entries")
    log.info("")
    log.info("[SUCCESS] Last operation reverted successfully")
    return 0


def cmd_sweep(args, curve_engine, config_loader):
    """Sweep voltage across a range and record stability at each step."""
    domain_names = [args.domain]

    # Validate domain
    all_domains = config_loader.get_domain_list()
    if args.domain not in all_domains:
        log.error(f"Invalid domain: {args.domain}")
        log.info(f"Available domains: {', '.join(all_domains)}")
        return 1

    if not args.yes:
        log.info("")
        log.info("=" * 80)
        log.info("  VOLTAGE SWEEP OPERATION")
        log.info("=" * 80)
        log.info(f"  Domain   : {args.domain}")
        log.info(f"  Range    : {args.from_mv:+d} mV  to  {args.to_mv:+d} mV")
        log.info(f"  Step     : {args.step} mV")
        steps_count = abs(args.to_mv - args.from_mv) // args.step + 1
        log.info(f"  Steps    : ~{steps_count}")
        log.info("=" * 80)
        log.info("")
        log.info("⚠️  WARNING: This will modify hardware voltages repeatedly!")
        log.info("")
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm != 'yes':
            log.info("Operation cancelled")
            return 0

    log.info("")
    log.info(f"Starting voltage sweep: {args.domain}  {args.from_mv:+d}mV -> {args.to_mv:+d}mV  step={args.step}mV")
    log.info("")

    results = curve_engine.sweep_voltages(domain_names, args.from_mv, args.to_mv, args.step)

    if 'error' in results:
        log.error(f"{results['error']}")
        return 1

    if getattr(args, 'json_output', False):
        log.info(json.dumps({
            'status': 'ok',
            'passed': results['passed'],
            'total': results['total'],
            'stopped_early': results['stopped_early'],
            'excel_path': results.get('excel_path'),
            'steps': results['steps'],
        }, indent=2))
        return 0 if not results['stopped_early'] else 2

    log.info("")
    log.info("=" * 80)
    log.info(f"  Sweep Results  —  {results['passed']}/{results['total']} steps PASS")
    log.info("=" * 80)
    icons = {'pass': '✓ PASS', 'cold_reset': '❌ COLD RESET', 'fail': '✗ FAIL'}
    for step in results['steps']:
        icon = icons.get(step['status'], step['status'].upper())
        log.info(f"  {step['offset_mv']:+5d} mV  ->  {icon}")
    log.info("")
    if results.get('excel_path'):
        log.info(f"  Report: {results['excel_path']}")
    if results['stopped_early']:
        log.error("  [!] Sweep stopped early due to cold reset or error")
    log.info("")
    return 0 if not results['stopped_early'] else 2


def cmd_show(args, curve_engine, config_loader):
    """Show VF curves for selected domains."""
    domain_names = args.domains
    
    # Validate domains
    all_domains = config_loader.get_domain_list()
    invalid = [d for d in domain_names if d not in all_domains]
    if invalid:
        log.error(f"Invalid domain(s): {', '.join(invalid)}")
        log.info(f"Available domains: {', '.join(all_domains)}")
        return 1
    
    log.info("")
    log.info(f"Showing VF curves for: {', '.join(domain_names)}")
    log.info("")
    
    # Show curves
    results = curve_engine.show_vf_curves(domain_names, interp_enabled=args.interpolate)
    
    if 'error' in results:
        log.error(f"{results['error']}")
        return 1
    
    # Display results
    log.info("")
    log.info("=" * 80)
    log.info("  Results")
    log.info("=" * 80)
    log.info("")
    
    for domain_name in domain_names:
        df = results['dataframes'][domain_name]
        excel_path = results['excel_paths'][domain_name]
        png_path = results['png_paths'][domain_name]
        
        label = config_loader.get_domain(domain_name).get('label', domain_name.upper())
        
        log.info(f"Domain: {label} ({domain_name})")
        log.info("-" * 80)
        log.info(df.to_string(index=False))
        log.info("")
        log.info(f"  Excel: {excel_path}")
        log.info(f"  Plot:  {png_path}")
        log.info("")
    
    if 'cumulative_excel' in results:
        log.info(f"Cumulative Excel: {results['cumulative_excel']}")
        log.info(f"Cumulative Plot:  {results['cumulative_png']}")
    
    log.info("")
    log.info("[SUCCESS] VF curves displayed successfully")
    return 0


def cmd_bump(args, curve_engine, config_loader):
    """Bump voltages for selected domains."""
    domain_names = args.domains
    bump_mv = args.value
    direction = args.direction
    
    # Validate domains
    all_domains = config_loader.get_domain_list()
    invalid = [d for d in domain_names if d not in all_domains]
    if invalid:
        log.error(f"Invalid domain(s): {', '.join(invalid)}")
        return 1
    
    # Confirm operation
    if not args.yes:
        log.info("")
        log.info("=" * 80)
        log.info("  VOLTAGE BUMP OPERATION")
        log.info("=" * 80)
        log.info(f"  Domains:   {', '.join(domain_names)}")
        log.info(f"  Direction: {direction.upper()}")
        log.info(f"  Amount:    {bump_mv} mV")
        log.info("=" * 80)
        log.info("")
        log.info("⚠️  WARNING: This will modify hardware voltages!")
        log.info("")
        
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm != 'yes':
            log.info("Operation cancelled")
            return 0
    
    log.info("")
    log.info(f"Bumping voltages {direction} by {bump_mv}mV...")
    log.info("")
    
    # Execute bump
    results = curve_engine.bump_voltages(domain_names, bump_mv, direction)
    
    if 'error' in results:
        if results.get('error') == 'COLD_RESET':
            log.info("")
            log.info("=" * 80)
            log.info("  ⚠️  COLD RESET DETECTED")
            log.info("=" * 80)
            log.info("")
            log.info(results['message'])
            log.info("")
            return 2
        else:
            log.error(f"{results['error']}")
            return 1
    
    # Display results
    log.info("")
    log.info("=" * 80)
    log.info("  Bump Operation Results")
    log.info("=" * 80)
    log.info("")
    
    # Display results for each domain
    for domain_name in domain_names:
        if domain_name in results['excel_paths']:
            excel_path = results['excel_paths'][domain_name]
            png_path = results['png_paths'][domain_name]
            verification = results['verification'][domain_name]
            
            log.info(f"Domain: {domain_name}")
            log.info(f"  Excel:  {excel_path}")
            log.info(f"  Plot:   {png_path}")
            
            if verification['success']:
                log.info(f"  Status: ✓ Voltages bumped and verified successfully!")
            else:
                log.info(f"  Status: ⚠ Some voltages outside tolerance:")
                for detail in verification['details']:
                    log.info(f"    WP{detail['wp']}: Expected {detail['expected_v']:.4f}V, Got {detail['after_v']:.4f}V (diff: {detail['diff_mv']:.2f}mV)")
            log.info("")
    
    log.info("[SUCCESS] Bump operation completed for all domains")
    log.info("")
    return 0


def cmd_edit(args, curve_engine, config_loader):
    """Edit specific WP voltages for a domain."""
    domain_name = args.domain
    
    # Validate domain
    if domain_name not in config_loader.get_domain_list():
        log.error(f"Invalid domain: {domain_name}")
        return 1
    
    # Parse WP voltage changes
    voltage_changes = {}
    try:
        for wp_spec in args.wp:
            wp_idx, voltage_mv = wp_spec.split(':')
            voltage_changes[int(wp_idx)] = int(voltage_mv)
    except ValueError:
        log.error("Invalid WP specification. Use format: --wp WP_INDEX:VOLTAGE_MV")
        log.info("Example: --wp 0:850 --wp 1:800")
        return 1
    
    if not voltage_changes:
        log.error("No WP voltage changes specified")
        return 1
    
    # Confirm operation
    if not args.yes:
        log.info("")
        log.info("=" * 80)
        log.info("  WP VOLTAGE EDIT OPERATION")
        log.info("=" * 80)
        log.info(f"  Domain: {domain_name}")
        log.info(f"  Changes:")
        for wp_idx, voltage_mv in voltage_changes.items():
            log.info(f"    WP{wp_idx} -> {voltage_mv} mV")
        log.info("=" * 80)
        log.info("")
        log.info("⚠️  WARNING: This will modify hardware voltages!")
        log.info("")
        
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm != 'yes':
            log.info("Operation cancelled")
            return 0
    
    log.info("")
    log.info(f"Editing WP voltages for {domain_name}...")
    log.info("")
    
    # Execute edit
    results = curve_engine.edit_voltages(domain_name, voltage_changes)
    
    if 'error' in results:
        if results.get('error') == 'COLD_RESET':
            log.info("")
            log.info("=" * 80)
            log.info("  ⚠️  COLD RESET DETECTED")
            log.info("=" * 80)
            log.info("")
            log.info(results['message'])
            log.info("")
            return 2
        else:
            log.error(f"{results['error']}")
            return 1
    
    # Display results
    log.info("")
    log.info("=" * 80)
    log.info("  WP Edit Results")
    log.info("=" * 80)
    log.info("")
    log.info(f"Excel:  {results['excel_path']}")
    log.info(f"Plot:   {results['png_path']}")
    log.info("")
    
    if results['verification']['success']:
        log.info("[SUCCESS] ✓ Voltages edited and verified successfully!")
    else:
        log.warning("Some voltages outside tolerance:")
        for detail in results['verification']['details']:
            if not detail['within_tolerance']:
                log.info(f"  WP{detail['wp']}: Expected {detail['expected_v']:.4f}V, Got {detail['after_v']:.4f}V")
    
    log.info("")
    return 0


def cmd_flatten(args, curve_engine, config_loader):
    """Flatten frequency ratios to a single value."""
    from utils.hardware_access import load_fuse_ram, read_frequency_ratios
    domain_name = args.domain
    target = args.target.lower()
    
    # Validate domain
    if domain_name not in config_loader.get_domain_list():
        log.error(f"Invalid domain: {domain_name}")
        return 1
    
    # Check flatten support
    if not config_loader.has_flatten_support(domain_name):
        log.error(f"Domain '{domain_name}' does not support frequency flattening")
        return 1
    
    # Validate target
    if target not in ['p0', 'p1', 'pn']:
        log.error(f"Invalid target: {target}. Must be 'p0', 'p1', or 'pn'")
        return 1
    
    # Get domain info to show current frequencies
    domain_info = config_loader.get_domain(domain_name)
    
    # Confirm operation
    if not args.yes:
        log.info("")
        log.info("=" * 80)
        log.info("  FLATTEN FREQUENCY OPERATION")
        log.info("=" * 80)
        log.info(f"  Domain: {domain_name}")
        log.info(f"  Target: {target.upper()}")
        log.info("=" * 80)
        log.info("")
        log.info("⚠️  WARNING: This will modify frequency ratios!")
        log.info("⚠️  System may become unstable if frequency is too low/high!")
        log.info("")
        
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm != 'yes':
            log.info("Operation cancelled")
            return 0
    
    log.info("")
    log.info(f"Flattening frequencies to {target.upper()} ratio...")
    log.info("")
    
    # Need to get target ratio value - read current ratios first
    load_fuse_ram(domain_info)
    current_ratios = read_frequency_ratios(domain_info)
    
    if current_ratios is None or target not in current_ratios:
        log.error(f"Could not read current frequency ratios")
        return 1
    
    target_ratio = current_ratios[target]
    
    # Execute flatten
    results = curve_engine.flatten_frequency(domain_name, target_ratio)
    
    if 'error' in results:
        if results.get('error') == 'COLD_RESET':
            log.info("")
            log.info("=" * 80)
            log.info("  ⚠️  COLD RESET DETECTED")
            log.info("=" * 80)
            log.info("")
            log.info(results['message'])
            log.info("")
            return 2
        else:
            log.error(f"{results['error']}")
            return 1
    
    # Display results
    log.info("")
    log.info("=" * 80)
    log.info("  Flatten Frequency Results")
    log.info("=" * 80)
    log.info("")
    
    df = results['dataframe']
    log.info(df.to_string(index=False))
    log.info("")
    log.info(f"Excel:  {results['excel_path']}")
    log.info("")
    log.info("[SUCCESS] ✓ Frequencies flattened successfully!")
    log.info("")
    
    return 0


def cmd_customize(args, curve_engine, config_loader):
    """Customize frequency values for P0, P1, and Pn."""
    domain_name = args.domain
    
    # Validate domain
    if domain_name not in config_loader.get_domain_list():
        log.error(f"Invalid domain: {domain_name}")
        return 1
    
    # Check flatten support (customize uses same infrastructure)
    if not config_loader.has_flatten_support(domain_name):
        log.error(f"Domain '{domain_name}' does not support frequency customization")
        return 1
    
    # Build custom frequencies dict from provided arguments
    custom_frequencies = {}
    if args.p0 is not None:
        custom_frequencies['p0'] = args.p0
    if args.p1 is not None:
        custom_frequencies['p1'] = args.p1
    if args.pn is not None:
        custom_frequencies['pn'] = args.pn
    
    if not custom_frequencies:
        log.error("No custom frequencies specified. Use --p0, --p1, and/or --pn")
        log.info("Example: --p0 4500 --p1 1500 --pn 400")
        return 1
    
    # Get domain info to show current frequencies
    domain_info = config_loader.get_domain(domain_name)
    
    # Confirm operation
    if not args.yes:
        log.info("")
        log.info("=" * 80)
        log.info("  CUSTOMIZE FREQUENCY OPERATION")
        log.info("=" * 80)
        log.info(f"  Domain: {domain_name}")
        log.info(f"  Custom Frequencies:")
        for key, freq in custom_frequencies.items():
            log.info(f"    {key.upper()} -> {freq} MHz")
        log.info("=" * 80)
        log.info("")
        log.info("⚠️  WARNING: This will modify frequency ratios!")
        log.info("⚠️  System may become unstable if frequencies are too low/high!")
        log.info("")
        
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm != 'yes':
            log.info("Operation cancelled")
            return 0
    
    log.info("")
    log.info(f"Setting custom frequencies...")
    log.info("")
    
    # Execute customize
    results = curve_engine.customize_frequency(domain_name, custom_frequencies)
    
    if 'error' in results:
        if results.get('error') == 'COLD_RESET':
            log.info("")
            log.info("=" * 80)
            log.info("  ⚠️  COLD RESET DETECTED")
            log.info("=" * 80)
            log.info("")
            log.info(results['message'])
            log.info("")
            return 2
        else:
            log.error(f"{results['error']}")
            return 1
    
    # Display results
    log.info("")
    log.info("=" * 80)
    log.info("  Customize Frequency Results")
    log.info("=" * 80)
    log.info("")
    
    df = results['dataframe']
    log.info(df.to_string(index=False))
    log.info("")
    log.info(f"Excel:  {results['excel_path']}")
    log.info("")
    log.info("[SUCCESS] ✓ Frequencies customized successfully!")
    log.info("")
    
    return 0


def cmd_dump_registers(args):
    """Export all discovered registers (name, value, hex, description…) to Excel.

    Reads from the discovery cache written by the last discovery run.
    If no cache exists the full discovery pipeline is run automatically.
    """
    try:
        from discovery.auto_discover_vf_registers import (
            load_discovery_cache,
            export_discovered_registers_to_excel,
        )
    except ImportError as exc:
        log.error(f"Discovery module not available: {exc}")
        return 1

    records, _pname, platform_display, timestamp = load_discovery_cache()

    if records is None:
        log.info("No discovery cache found — running full discovery pipeline...")
        log.info("    (fuse RAM loading takes 2-5 min — please wait)")
        from discovery.startup_discovery import maybe_run_discovery
        maybe_run_discovery(force=True)
        records, _pname, platform_display, timestamp = load_discovery_cache()
        if records is None:
            log.error("Discovery produced no results. "
                  "Check ITP connection and try again.")
            return 1

    if args.active_only:
        before  = len(records)
        records = [r for r in records if r.get('active')]
        log.info(f"Active-only filter: {len(records)}/{before} registers")

    log.info("")
    log.info(f"Exporting {len(records)} register(s) to Excel...")
    if timestamp:
        log.info(f"    Cache timestamp : {timestamp}")
    if platform_display:
        log.info(f"    Platform        : {platform_display}")

    filepath = export_discovered_registers_to_excel(
        platform_display=platform_display,
        records=records,
    )

    if filepath is None:
        log.error("Export failed.")
        return 1

    active_cnt = sum(1 for r in records if r.get('active'))
    log.info("")
    log.info("=" * 70)
    log.info("  REGISTER DUMP COMPLETE")
    log.info("=" * 70)
    log.info(f"  Platform    : {platform_display}")
    log.info(f"  Total       : {len(records)}")
    log.info(f"  Active      : {active_cnt}")
    log.info(f"  Inactive    : {len(records) - active_cnt}")
    log.info(f"  File        : {filepath}")
    log.info("")
    return 0


def cmd_edit_register(args):
    """View or write a discovered register value via ITP.

    Without --set-value: shows current cached info for the register.
    With    --set-value: runs the full hardware write flow:
        load_fuse_ram → write → flush_fuse_ram → itp.resettarget → verify
    """
    try:
        from discovery.auto_discover_vf_registers import load_discovery_cache
    except ImportError as exc:
        log.error(f"Discovery module not available: {exc}")
        return 1

    records, _, _, _ = load_discovery_cache()
    if records is None:
        log.error("No discovery cache found.")
        log.info("    Run with --rediscover first to populate the cache.")
        return 1

    # Exact match first, then fuzzy (substring)
    exact = [r for r in records if r['name'] == args.name]
    if not exact:
        fuzzy = [r for r in records if args.name.lower() in r['name'].lower()]
        if not fuzzy:
            log.error(f"No register matching '{args.name}' found in cache.")
            log.info("    Use 'dump-registers' to see all register names.")
            return 1
        if len(fuzzy) > 1:
            log.error(f"'{args.name}' matches {len(fuzzy)} registers — be more specific:")
            for r in fuzzy[:15]:
                log.info(f"    - {r['name']}")
            if len(fuzzy) > 15:
                log.info(f"    ... and {len(fuzzy) - 15} more")
            return 1
        exact = fuzzy

    reg = exact[0]

    # ── Read-only view ────────────────────────────────────────────────────
    if args.set_value is None:
        # Build converted value string (same logic as GUI Converted column)
        converted = reg.get('converted', '')
        if not converted:
            try:
                from discovery.auto_discover_vf_registers import _infer_conversion_from_description
                converted = _infer_conversion_from_description(
                    reg.get('name', ''),
                    reg.get('description', ''),
                    reg.get('value'),
                )
            except Exception:
                converted = ''
        log.info("")
        log.info(f"  Register   : {reg['name']}")
        log.info(f"  Value (dec): {reg.get('value')}")
        log.info(f"  Value (hex): {reg.get('hex', '')}")
        if converted:
            log.info(f"  Converted  : {converted}")
        log.info(f"  Active     : {'Yes' if reg.get('active') else 'No'}")
        log.info(f"  Domain     : {reg.get('domain', 'unknown')}")
        log.info(f"  Category   : {reg.get('category', 'other')}")
        log.info(f"  Fuse Path  : {reg.get('fuse_path', '')}")
        desc = (reg.get('description') or '').strip()
        log.info(f"  Description: {desc[:180]}{'...' if len(desc) > 180 else ''}")
        log.info("")
        log.info("  To write a new value: add --set-value <int>")
        return 0

    # ── Hardware write flow ───────────────────────────────────────────────
    try:
        new_val = int(args.set_value, 0)   # supports 0x hex notation
    except ValueError:
        log.error(f"--set-value must be an integer (decimal or 0x hex): {args.set_value!r}")
        return 1

    fuse_path = reg.get('fuse_path', '')
    if not fuse_path:
        log.error(f"Register '{reg['name']}' has no fuse_path in cache.")
        return 1

    log.info("")
    log.info(f"  Register  : {reg['name']}")
    log.info(f"  Fuse path : {fuse_path}")
    log.info(f"  Current   : {reg.get('value')} ({reg.get('hex', '')})")
    log.info(f"  New value : {new_val} (0x{new_val:x})")
    log.info("")

    if not getattr(args, 'yes', False):
        confirm = input("  Apply this change? [y/N]: ").strip().lower()
        if confirm != 'y':
            log.info("  Cancelled.")
            return 0

    try:
        from utils.hardware_access import apply_discovered_register_edits
    except ImportError as exc:
        log.error(f"hardware_access not available: {exc}")
        return 1

    result = apply_discovered_register_edits([{
        'fuse_path': fuse_path,
        'reg_name':  reg['name'],
        'new_value': new_val,
    }])

    log.info("")
    if result['success']:
        log.info(f"{result['message']}")
        for w in result['written']:
            status = '\u2713' if w['verified'] else '\u2717 MISMATCH'
            log.info(f"    [{status}] {w['reg_name']}: {w['before']} -> {w['after']}")
        # Before/after Excel (same format as bump/flatten exports)
        try:
            from discovery.discovery_core import export_register_change_to_excel
            _xl = export_register_change_to_excel(result['written'])
            if _xl:
                log.info(f"  Before/After Excel : {_xl}")
        except Exception as _exc:  # noqa: BLE001
            log.warning(f"  Excel export skipped: {_exc}")
        return 0
    else:
        log.error(f"{result['message']}")
        if result.get('cold_reset'):
            log.info("  Cold reset detected — check hardware state.")
        return 1


# ---------------------------------------------------------------------------
# Scalar modifier commands
# ---------------------------------------------------------------------------

def cmd_scalars(args, curve_engine, config_loader):
    """Show or edit scalar modifier registers."""
    action = getattr(args, 'scalar_action', 'show')

    if action == 'show':
        type_filter = getattr(args, 'type_filter', None)
        report = curve_engine.show_scalar_modifiers(type_filter=type_filter)
        if not report['ok']:
            return 1
        scalars = config_loader.get_scalar_modifiers()
        if not scalars:
            log.info("No scalar modifiers in config.  "
                  "Re-run auto-discovery to populate them.")
        return 0

    elif action == 'edit':
        register_name   = args.scalar_key
        physical_value  = args.scalar_value
        result = curve_engine.edit_scalar_modifier(register_name, physical_value)
        if result['ok']:
            log.info(f"{result['message']}")
            # Before/after Excel for this scalar write
            try:
                from discovery.discovery_core import export_scalar_change_to_excel
                _info = config_loader.get_scalar_modifiers().get(register_name, {})
                _xl = export_scalar_change_to_excel(
                    register_name,
                    result['before'],
                    result['after'],
                    _info,
                )
                if _xl:
                    log.info(f"  Before/After Excel : {_xl}")
            except Exception as _exc:  # noqa: BLE001
                log.warning(f"  Excel export skipped: {_exc}")
            return 0
        else:
            log.error(f"{result['message']}")
            return 1

    else:
        log.error(f"Unknown scalars action: '{action}'")
        return 1


# ---------------------------------------------------------------------------
# probe-platform command  — lists all namednodes and live fuse roots/containers
# ---------------------------------------------------------------------------

def cmd_probe_platform(args):
    """List every namednode visible in the live pythonsv namespace and report
    which ones expose .fuses and what containers sit below each fuse root."""
    from discovery.discovery_core import probe_namednodes

    data = probe_namednodes()

    if not data['namednodes_available']:
        log.error(f"namednodes not available: {data.get('error', 'unknown')}")
        log.info("  (Is pythonsv / ITP initialised?  Is namednodes installed?)")
        return 1

    if data.get('error'):
        log.warning(f"probe_namednodes warning: {data['error']}")

    all_nodes = data['all_nodes']
    fuse_roots = data['fuse_roots']
    node_attrs = data['node_attrs']
    containers = data['fuse_containers']

    log.info("")
    log.info("=" * 60)
    log.info("  Platform Node Probe")
    log.info("=" * 60)

    # ── All nodes ──────────────────────────────────────────────────
    log.info(f"\n[*] namednodes top-level nodes ({len(all_nodes)} found):")
    if all_nodes:
        for node in all_nodes:
            attrs = node_attrs.get(node, [])
            has_fuses = '\u2713 .fuses' if f"{node}.fuses" in fuse_roots else ''
            attr_summary = ', '.join(attrs[:8])
            if len(attrs) > 8:
                attr_summary += f', … (+{len(attrs) - 8} more)'
            log.info(f"    {node:<20}  {has_fuses:<10}  attrs: [{attr_summary}]")
    else:
        log.info("    (none found — namednodes namespace is empty)")

    # ── Fuse roots ──────────────────────────────────────────────────
    log.info(f"\n[+] Live fuse roots ({len(fuse_roots)} found):")
    if fuse_roots:
        for root in fuse_roots:
            clist = containers.get(root, [])
            log.info(f"\n    {root}  ({len(clist)} containers):")
            for c in clist:
                log.info(f"        • {root}.{c}")
    else:
        log.info("    (none found — no node exposes a .fuses attribute)")
        log.info("    Try: run_cli.bat --rediscover list  to trigger auto-discovery")

    log.info("")

    # JSON output
    if getattr(args, 'json_output', False):
        import json, sys
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write('\n')

    return 0


def main():
    """Main CLI entry point."""
    # ── Structured logging ────────────────────────────────────────────────
    try:
        from utils.log_setup import setup_logging
        setup_logging()
    except Exception:
        pass  # logging is best-effort — don't abort if Logs/ can't be created

    parser = argparse.ArgumentParser(
        description='VF Curve Manager Tool v2.2 - Command Line Interface',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all available domains
  %(prog)s list

  # Show VF curves for specific domains
  %(prog)s show --domains cluster0_bigcore ring

  # Bump voltages up by 10mV (with confirmation)
  %(prog)s bump --domains cluster0_bigcore --value 10 --direction up

  # Bump voltages down by 5mV (skip confirmation)
  %(prog)s bump --domains ring --value 5 --direction down --yes

  # Edit specific WP voltages
  %(prog)s edit --domain cluster0_bigcore --wp 0:850 --wp 1:800 --wp 2:750

  # Flatten frequency to P1 ratio
  %(prog)s flatten --domain cluster0_bigcore --target p1 --yes

  # Customize frequencies for P0/P1/Pn
  %(prog)s customize --domain cluster0_bigcore --p0 4500 --p1 1500 --pn 400

  # Sweep voltages from -50mV to +50mV in 10mV steps
  %(prog)s sweep --domain cluster0_bigcore --from -50 --to 50 --step 10 --yes

  # Revert the last voltage operation
  %(prog)s revert-last --yes

  # Mock mode — no hardware needed, reads from discovery cache
  %(prog)s --mock list
  %(prog)s --mock show --domains cluster0_bigcore

  # Machine-readable JSON output
  %(prog)s --json list
  %(prog)s --json show --domains ring

  # Disable SUT boot verification (fast mode)
  %(prog)s --no-sut-check bump --domains ring --value 5 --direction up --yes

For more information, see USER_GUIDE.md
        """
    )

    # ── Global flags ──────────────────────────────────────────────────────
    parser.add_argument('--mock', action='store_true',
                        help='Run in mock mode \u2014 no hardware access; reads from '
                             'vf_discovery_cache.json instead of live registers. '
                             'Useful for script development and CI without a board.')
    parser.add_argument('--no-sut-check', dest='no_sut_check', action='store_true',
                        help='Disable SUT boot verification and recovery checks (faster; '
                             'use when you know the hardware is stable).')
    parser.add_argument('--enable-sut-check', dest='enable_sut_check', action='store_true',
                        help='[Legacy alias — SUT check is now ON by default. '
                             'Use --no-sut-check to disable it.]')
    parser.add_argument('--rediscover', action='store_true',
                        help='Force a full VF register re-discovery and update vf_domains.json '
                             'before running the command (use after new firmware fuses are burned).')
    parser.add_argument('--json', dest='json_output', action='store_true',
                        help='Emit results as machine-readable JSON to stdout instead of '
                             'human-readable tables.  Suitable for CI/CD pipelines.')

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    subparsers.required = True

    # List command
    subparsers.add_parser('list', help='List all available VF domains')

    # Probe-platform command
    subparsers.add_parser(
        'probe-platform',
        help='List all namednodes visible in the live pythonsv namespace, '
             'which ones expose .fuses, and what containers sit below each fuse root')
    # Show command
    parser_show = subparsers.add_parser('show', help='Display VF curves for domains')
    parser_show.add_argument('--domains', nargs='+', required=True,
                             help='Domain names to display (space-separated)')
    parser_show.add_argument('--no-interpolate', dest='interpolate', action='store_false',
                             help='Disable interpolation in plots')

    # Bump command
    parser_bump = subparsers.add_parser('bump', help='Bump voltages up or down')
    parser_bump.add_argument('--domains', nargs='+', required=True)
    parser_bump.add_argument('--value', type=int, required=True,
                             help='Voltage bump amount in millivolts (e.g., 10)')
    parser_bump.add_argument('--direction', choices=['up', 'down'], required=True)
    parser_bump.add_argument('--yes', action='store_true', help='Skip confirmation prompt')

    # Edit command
    parser_edit = subparsers.add_parser('edit', help='Edit specific WP voltages')
    parser_edit.add_argument('--domain', required=True)
    parser_edit.add_argument('--wp', action='append', required=True,
                             help='WP_INDEX:VOLTAGE_MV  (repeatable)')
    parser_edit.add_argument('--yes', action='store_true')

    # Flatten command
    parser_flatten = subparsers.add_parser('flatten', help='Flatten frequency ratios')
    parser_flatten.add_argument('--domain', required=True)
    parser_flatten.add_argument('--target', choices=['p0', 'p1', 'pn'], required=True)
    parser_flatten.add_argument('--yes', action='store_true')

    # Customize frequency command
    parser_customize = subparsers.add_parser('customize', help='Set custom P0/P1/Pn frequencies')
    parser_customize.add_argument('--domain', required=True)
    parser_customize.add_argument('--p0', type=int, help='P0 frequency in MHz')
    parser_customize.add_argument('--p1', type=int, help='P1 frequency in MHz')
    parser_customize.add_argument('--pn', type=int, help='Pn frequency in MHz')
    parser_customize.add_argument('--yes', action='store_true')

    # Sweep command  ──────────────────────────────────────────────────────
    parser_sweep = subparsers.add_parser(
        'sweep',
        help='Sweep voltage from --from to --to in --step increments; records pass/fail at each step')
    parser_sweep.add_argument('--domain', required=True,
                              help='Domain to sweep (single domain)')
    parser_sweep.add_argument('--from', dest='from_mv', type=int, required=True,
                              help='Start offset in mV relative to current baseline (e.g. -50)')
    parser_sweep.add_argument('--to', dest='to_mv', type=int, required=True,
                              help='End offset in mV relative to current baseline (e.g. +50)')
    parser_sweep.add_argument('--step', type=int, required=True,
                              help='Step size in mV (e.g. 10)')
    parser_sweep.add_argument('--yes', action='store_true', help='Skip confirmation prompt')

    # Revert-last command  ────────────────────────────────────────────────
    parser_revert = subparsers.add_parser(
        'revert-last',
        help='Revert the most recent voltage/frequency operation from the undo log')
    parser_revert.add_argument('--yes', action='store_true', help='Skip confirmation prompt')

    # Dump registers command
    parser_dump = subparsers.add_parser(
        'dump-registers',
        help='Export all discovered registers with values and descriptions to Excel')
    parser_dump.add_argument('--active-only', dest='active_only', action='store_true')

    # Edit register command
    parser_edit_reg = subparsers.add_parser(
        'edit-register',
        help='View or write a specific register from the discovery cache')
    parser_edit_reg.add_argument('--name', required=True)
    parser_edit_reg.add_argument('--set-value', dest='set_value', default=None, metavar='INT')
    parser_edit_reg.add_argument('--yes', action='store_true')

    # Scalars command  ─────────────────────────────────────────────────────
    parser_scalars = subparsers.add_parser(
        'scalars',
        help='View or edit discovered scalar modifier registers '
             '(ITD, P0 overrides, downbin, MCT delta, ACODE min)')
    scalar_sub = parser_scalars.add_subparsers(dest='scalar_action')
    scalar_sub.required = True

    scalar_show = scalar_sub.add_parser('show', help='Read and display all scalar modifiers')
    scalar_show.add_argument(
        '--type', dest='type_filter', default=None,
        help='Limit output to a specific modifier type '
             '(p0_override, itd_voltage, itd_slope, downbin, mct_delta, atom_delta, acode_min)')

    scalar_edit = scalar_sub.add_parser('edit', help='Write a new value to a scalar modifier')
    scalar_edit.add_argument('--key',   dest='scalar_key',   required=True,
                             metavar='REGISTER',
                             help='Register name from scalar_modifiers config')
    scalar_edit.add_argument('--value', dest='scalar_value', required=True, type=float,
                             metavar='VALUE',
                             help='Physical value to write '
                                  '(MHz for ratio_mhz, mV for voltage_mv, raw int otherwise)')
    scalar_edit.add_argument('--yes', action='store_true', help='Skip confirmation prompt')

    args = parser.parse_args()

    # ── Early validation ──────────────────────────────────────────────────
    if args.command == 'customize' and args.p0 is None and args.p1 is None and args.pn is None:
        parser.error("customize requires at least one of --p0, --p1, --pn")

    if args.command == 'sweep' and args.step <= 0:
        parser.error("--step must be a positive integer")

    # edit-register view (no --set-value) skips ITP entirely
    if args.command == 'edit-register' and args.set_value is None:
        return cmd_edit_register(args)

    # ── SUT verification default ──────────────────────────────────────────
    # Default is ON (safe).  --no-sut-check opts out.  --enable-sut-check
    # is kept as a legacy alias.
    enable_sut = not args.no_sut_check

    # ── Mock mode: skip ITP, load cache ──────────────────────────────────
    mock_mode = args.mock or _MOCK_MODE

    # ── Initialize ITP (skipped in mock mode) ─────────────────────────────
    if not init_itp():
        return 1

    # probe-platform only needs ITP up — runs before discovery / setup_modules
    if args.command == 'probe-platform':
        return cmd_probe_platform(args)
    # ── Autonomous discovery ──────────────────────────────────────────────
    if not mock_mode:
        from discovery.startup_discovery import maybe_run_discovery
        # Always force a fresh discovery so the cache is never stale across
        # different hardware setups.  --rediscover kept for back-compat.
        maybe_run_discovery(force=True)

    # ── Setup modules ─────────────────────────────────────────────────────
    curve_engine, config_loader = setup_modules(
        enable_sut_check=enable_sut,
        mock_mode=mock_mode,
    )
    if curve_engine is None:
        return 1

    # ── Health watchdog (real hardware only) ─────────────────────────────
    _wdog = None
    if not mock_mode:
        try:
            from utils.watchdog import HealthWatchdog
            from utils import hardware_access as _ha

            def _itp_probe():
                if _ha.itp is None:
                    return True  # not yet fully initialised
                try:
                    return bool(_ha.itp.cv.isconnected())
                except Exception:
                    return False

            _wdog = HealthWatchdog(
                probe_fn=_itp_probe,
                interval=30,
                on_fault=lambda r: log.info(f"\n[WATCHDOG] \u26a0  ITP fault detected: {r}\n"
                                         "[WATCHDOG]    The tool will retry on the next command."),
                on_recover=lambda: log.info("\n[WATCHDOG] \u2713 ITP connection restored\n"),
            )
            _wdog.start()
        except Exception as _wdog_ex:
            log.debug("[WATCHDOG] Could not start watchdog: %s", _wdog_ex)

    # ── Command dispatch with transient-error retry ───────────────────────
    MAX_RETRIES = 2

    def _dispatch():
        if args.command == 'list':
            return cmd_list(args, config_loader)
        elif args.command == 'show':
            return cmd_show(args, curve_engine, config_loader)
        elif args.command == 'bump':
            return cmd_bump(args, curve_engine, config_loader)
        elif args.command == 'edit':
            return cmd_edit(args, curve_engine, config_loader)
        elif args.command == 'flatten':
            return cmd_flatten(args, curve_engine, config_loader)
        elif args.command == 'customize':
            return cmd_customize(args, curve_engine, config_loader)
        elif args.command == 'sweep':
            return cmd_sweep(args, curve_engine, config_loader)
        elif args.command == 'revert-last':
            return cmd_revert_last(args, curve_engine)
        elif args.command == 'dump-registers':
            return cmd_dump_registers(args)
        elif args.command == 'edit-register':
            return cmd_edit_register(args)
        elif args.command == 'scalars':
            return cmd_scalars(args, curve_engine, config_loader)
        elif args.command == 'probe-platform':
            return cmd_probe_platform(args)   # safety fallback (normally early-exited)
        else:
            log.error(f"Unknown command: {args.command}")
            return 1

    result = 1
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = _dispatch()
        except KeyboardInterrupt:
            log.info("")
            log.info("Operation cancelled by user")
            return 130
        except Exception as ex:
            log.info("")
            log.error(f"Unexpected error: {ex}")
            traceback.print_exc()
            result = 1

        if result != EXIT_TRANSIENT_ERROR:
            break

        if attempt < MAX_RETRIES and not mock_mode:
            log.info(f"\n[RETRY] Transient ITP error \u2014 retrying ({attempt + 1}/{MAX_RETRIES})...")
            try:
                from utils import hardware_access
                hardware_access.reinitialize_ipc_itp()
                time.sleep(2)
            except Exception as reinit_ex:
                log.info(f"[RETRY] Reinitialization failed: {reinit_ex}")
                break

    if _wdog is not None:
        try:
            _wdog.stop()
        except Exception:
            pass

    return result


if __name__ == '__main__':
    sys.exit(main())
