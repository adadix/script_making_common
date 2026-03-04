"""
VF Curve Engine - Core operations for voltage-frequency curve management.

Provides high-level operations:
- Show VF curve
- Bump voltages up/down
- Edit individual WPs
- Flatten frequency ratios
- Sweep voltages across a range
- Revert last operation from undo log
"""

import logging
import json
import pathlib
import sys
import os
import time
from datetime import datetime
from matplotlib.spines import Spine  # noqa: F401 – imported for side-effects
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# Undo log — stored in project-root Logs/ so it persists across reboots (unlike tempdir).
# src/core/curve_engine.py  →  src/core/  →  src/  →  project-root/  →  Logs/
_UNDO_LOG_PATH: pathlib.Path = pathlib.Path(__file__).parent.parent.parent / 'Logs' / 'vf_curve_manager_undo_log.json'
_UNDO_LOG_PATH.parent.mkdir(exist_ok=True)

# Add parent directory to path for imports
_parent_dir: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from utils.hardware_access import (
    read_all_wps, read_delta_voltages, read_adder_voltages,
    load_fuse_ram, flush_fuse_ram, reset_target,
    bump_all_voltages, read_frequency_ratios, write_frequency_ratios,
    recover_from_deep_sleep, wait_for_sut_boot, verify_post_fuse_update,
    write_voltage, write_frequency,
    ENABLE_SUT_VERIFICATION,
)
from utils.data_export import (
    export_dataframe_to_excel, export_multiple_sheets,
    plot_vf_curve, plot_cumulative_curves, plot_before_after,
    create_timestamped_filename
)

log = logging.getLogger(__name__)


class CurveEngine:
    """
    Core engine for VF curve operations.
    
    Handles all curve management operations with hardware integration.
    """
    
    def __init__(self, config_loader) -> None:
        """
        Initialize curve engine.
        
        Args:
            config_loader: ConfigLoader instance with domain configuration
        """
        self.config_loader: Any = config_loader

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _unique_fuse_ram_map(self, domain_names: list) -> dict:
        """Return {fuse_ram_path: domain_info} for each unique fuse RAM path.

        Multiple domains (e.g. every punit_fuses domain) share the same parent
        fuse RAM path (cdie.fuses).  Collecting unique paths avoids redundant
        load_fuse_ram / flush_fuse_ram calls in batch operations.
        """
        result = {}
        for name in domain_names:
            info = self.config_loader.get_domain(name)
            frp  = info.get('fuse_ram_path', info['fuse_path'])
            if frp not in result:
                result[frp] = info
        return result

    def show_vf_curves(self, domain_names, interp_enabled=True):
        """
        Display VF curves for selected domains.
        
        Args:
            domain_names: List of domain names to display
            interp_enabled: Enable interpolation for plots
            
        Returns:
            dict: {
                'dataframes': {domain: df},
                'excel_paths': {domain: path},
                'png_paths': {domain: path},
                'cumulative_excel': path,
                'cumulative_png': path
            }
        """
        if not domain_names:
            return {'error': 'No domains selected'}
        
        # Only check SUT state if verification is enabled
        if ENABLE_SUT_VERIFICATION:
            log.info("Checking SUT state before reading VF curves...")
            recover_from_deep_sleep()
        
        unique_fuse_rams = self._unique_fuse_ram_map(domain_names)
        for domain_info in unique_fuse_rams.values():
            load_fuse_ram(domain_info)

        # Calculate max WP count across all domains using EFFECTIVE (non-zero) count
        # so trailing all-zero WPs (e.g. WP10/WP11 on a 10-WP platform) are hidden.
        max_wp = max(
            self._effective_wp_count(self.config_loader.get_domain(d))
            for d in domain_names
        )
        
        # Collect data for each domain
        results = {
            'dataframes': {},
            'excel_paths': {},
            'png_paths': {}
        }
        
        for domain_name in domain_names:
            domain_info = self.config_loader.get_domain(domain_name)
            label = domain_info.get('label', domain_name.upper())
            
            # Read VF curve
            df = self._make_vf_dataframe(domain_info, max_wp, label)
            results['dataframes'][domain_name] = df
            
            # Export to Excel
            excel_path: str = create_timestamped_filename(f'vf_curve_dump_{domain_name}', 'xlsx')
            export_dataframe_to_excel(df, excel_path, 'VF Curve')
            results['excel_paths'][domain_name] = excel_path
            
            # Generate plot
            png_path: str = create_timestamped_filename(f'vf_curve_{domain_name}', 'png')
            plot_vf_curve(df, label, png_path, interp_enabled)
            results['png_paths'][domain_name] = png_path
        
        # Generate cumulative view if multiple domains
        if len(domain_names) > 1:
            cumulative_df = self._make_cumulative_dataframe(domain_names, max_wp)
            cumulative_excel: str = create_timestamped_filename('vf_curve_dump_CUMULATIVE', 'xlsx')
            export_dataframe_to_excel(cumulative_df, cumulative_excel, 'VF Curve Cumulative')
            results['cumulative_excel'] = cumulative_excel
            
            cumulative_png: str = create_timestamped_filename('vf_curve_CUMULATIVE', 'png')
            dfs = [results['dataframes'][d] for d in domain_names]
            labels = [self.config_loader.get_domain(d).get('label', d.upper()) for d in domain_names]
            plot_cumulative_curves(dfs, labels, cumulative_png, interp_enabled)
            results['cumulative_png'] = cumulative_png
        
        return results
    
    def bump_voltages(self, domain_names, bump_mv, direction='up'):
        """
        Bump voltages for selected domains.
        
        Args:
            domain_names: List of domain names
            bump_mv: Amount to bump in millivolts
            direction: 'up' or 'down'
            
        Returns:
            dict: Before/after data and verification results
        """
        if not domain_names:
            return {'error': 'No domains selected'}
        
        unique_fuse_rams = self._unique_fuse_ram_map(domain_names)
        for domain_info in unique_fuse_rams.values():
            load_fuse_ram(domain_info)

        # Read before values (CRITICAL: Store these for potential revert)
        before_data = {}
        for domain_name in domain_names:
            domain_info = self.config_loader.get_domain(domain_name)
            before_data[domain_name] = read_all_wps(domain_info)
            log.info(f"[BACKUP] Saved original voltages for {domain_name} (for potential revert)")
        
        # Persist before_data to the undo log (append-only, survives process crash)
        try:
            _history = []
            if _UNDO_LOG_PATH.exists():
                with open(_UNDO_LOG_PATH, 'r') as _rf:
                    _history = json.load(_rf)
            _history.append({
                'timestamp': datetime.now().isoformat(),
                'operation': 'bump',
                'direction': direction,
                'bump_mv': bump_mv,
                'domains': {d: [[v, f] for v, f in before_data[d]]
                            for d in before_data},
            })
            with open(_UNDO_LOG_PATH, 'w') as _wf:
                json.dump(_history, _wf, indent=2)
            log.info(f"[BACKUP] Undo log updated: {_UNDO_LOG_PATH} ({len(_history)} entries)")
        except Exception as _bk_ex:
            log.warning(f"Could not update undo log: {_bk_ex}")
        
        # Apply bump to all domains
        for domain_name in domain_names:
            domain_info = self.config_loader.get_domain(domain_name)
            bump_all_voltages(domain_info, bump_mv, direction)
        
        # Flush fuse RAM once per unique path
        for fuse_ram_path, domain_info in unique_fuse_rams.items():
            flush_fuse_ram(domain_info)
        
        # Reset target (verification only if enabled)
        log.info("Resetting target...")
        reset_result = reset_target()
        
        if not reset_result['reset_success']:
            return {'error': f"Target reset failed: {reset_result['message']}"}
        
        # CHECK FOR COLD RESET (POWER OFF)
        if reset_result.get('cold_reset_detected', False):
            return self._handle_cold_reset_voltage_op(
                domain_names, before_data, unique_fuse_rams,
                reset_result.get('cold_reset_details', {}), 'voltage bump',
            )

        # Only check boot/verification if enabled
        verification_result = None
        if ENABLE_SUT_VERIFICATION:
            if not reset_result.get('boot_success', True):
                log.warning(f"{reset_result['message']}")
            
            # Verify SUT is functional after fuse update
            verification_result = verify_post_fuse_update()
            if not verification_result['success']:
                return {'error': f"SUT verification failed: {verification_result['message']}"}
            
            log.info(f"[SUCCESS] SUT verified functional after voltage bump")
        
        # Load fuse RAM once per unique path after reset
        for fuse_ram_path, domain_info in unique_fuse_rams.items():
            load_fuse_ram(domain_info)
        
        # Read after values
        after_data = {}
        for domain_name in domain_names:
            domain_info = self.config_loader.get_domain(domain_name)
            after_data[domain_name] = read_all_wps(domain_info)
        
        # Generate before/after reports — use effective WP count to hide trailing empty rows
        max_wp = max(
            self._effective_wp_count(self.config_loader.get_domain(d))
            for d in domain_names
        )
        
        results = {
            'before_dataframes': {},
            'after_dataframes': {},
            'excel_paths': {},
            'png_paths': {},
            'verification': {},
            'reset_info': reset_result,
            'cold_reset_detected': False
        }
        
        # Add SUT verification result only if it was actually run
        if verification_result is not None:
            results['sut_verification'] = verification_result
        
        for domain_name in domain_names:
            domain_info = self.config_loader.get_domain(domain_name)
            label = domain_info.get('label', domain_name.upper())
            
            # Create before/after DataFrames
            df_before = self._make_vf_dataframe_from_data(domain_info, before_data[domain_name], max_wp, label)
            df_after = self._make_vf_dataframe_from_data(domain_info, after_data[domain_name], max_wp, label)
            
            results['before_dataframes'][domain_name] = df_before
            results['after_dataframes'][domain_name] = df_after
            
            # Export to Excel (both sheets)
            excel_path: str = create_timestamped_filename(f'vf_curve_bump_{direction}_{domain_name}', 'xlsx')
            export_multiple_sheets({'Before': df_before, 'After': df_after}, excel_path)
            results['excel_paths'][domain_name] = excel_path
            
            # Generate plot
            png_path: str = create_timestamped_filename(f'vf_curve_bump_{direction}_{domain_name}', 'png')
            plot_before_after(df_before, df_after, label, png_path)
            results['png_paths'][domain_name] = png_path
            
            # Verify bump
            verification = self._verify_bump(before_data[domain_name], after_data[domain_name], bump_mv, direction)
            results['verification'][domain_name] = verification
        
        return results

    def _read_ratios_with_retry(self, domain_info: dict, context_label: str = '') -> dict:
        """Load fuse RAM and read frequency ratios with up to 3 retry attempts.

        Args:
            domain_info:    Domain configuration dictionary.
            context_label:  Human-readable label used in log messages (e.g. 'flatten').

        Returns:
            dict: {'ratios': dict}  on success, or
                  {'error': str}   on failure after all retries.
        """
        max_retries = 3
        retry_delay = 10   # generous wait: a full target power-cycle takes ~5s
        after_ratios = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    log.info(f"Retry attempt {attempt}/{max_retries - 1}...")
                    time.sleep(retry_delay)
                    log.info("Performing ITP recovery before retry...")
                    recover_from_deep_sleep(bypass_cooldown=True)

                load_success: None | bool = load_fuse_ram(domain_info)
                if not load_success:
                    if attempt < max_retries - 1:
                        log.warning("load_fuse_ram failed, will retry...")
                        continue
                    return {'error': 'Failed to load fuse RAM after multiple attempts. Hardware may be in unstable state.'}

                after_ratios = read_frequency_ratios(domain_info)
                if after_ratios is None:
                    if attempt < max_retries - 1:
                        log.warning("read_frequency_ratios returned None, will retry...")
                        continue
                    return {'error': 'Failed to read frequency ratios after multiple attempts.'}

                log.info(f"[SUCCESS] Successfully read frequency ratios after {context_label}")
                break

            except Exception as ex:
                if attempt < max_retries - 1:
                    log.warning(f"Error reading after ratios: {ex}, will retry...")
                    continue
                return {'error': f'Failed to read after ratios: {ex}'}

        if after_ratios is None:
            return {'error': f'Failed to read frequency ratios after {context_label} operation'}
        return {'ratios': after_ratios}

    def flatten_frequency(self, domain_name, target_ratio):
        """
        Flatten frequency ratios to a single value.
        
        Args:
            domain_name: Domain name
            target_ratio: Target ratio value to set for all (P0, P1, Pn)
            
        Returns:
            dict: Before/after data and results
        """
        domain_info = self.config_loader.get_domain(domain_name)
        
        if not self.config_loader.has_flatten_support(domain_name):
            return {'error': f'Domain {domain_name} does not support flattening'}
        
        # Load fuse RAM and read before ratios - CRITICAL: Store for potential revert
        load_fuse_ram(domain_info)
        before_ratios = read_frequency_ratios(domain_info)

        # Store ACTUAL frequency values before making changes (for display)
        # Guard: skip non-numeric values that mock mode may return for unknown registers.
        freq_mult = domain_info.get('freq_multiplier', 100)
        before_frequencies = {
            key: (value * freq_mult if isinstance(value, (int, float)) else None)
            for key, value in before_ratios.items()
        }

        log.info(f"[BACKUP] Saved original frequency ratios: {before_ratios}")
        log.info(f"[BACKUP] Current frequencies before flatten: {before_frequencies}")
        
        # Set all ratios to target
        new_ratios = {}
        for key in before_ratios.keys():
            new_ratios[key] = target_ratio
        
        write_frequency_ratios(domain_info, new_ratios)
        
        # Flush and reset
        flush_fuse_ram(domain_info)
        log.info("Resetting target...")
        reset_result = reset_target()
        
        if not reset_result['reset_success']:
            return {'error': f"Target reset failed: {reset_result['message']}"}
        
        # CHECK FOR COLD RESET (POWER OFF)
        if reset_result.get('cold_reset_detected', False):
            log.info(f"[CRITICAL] COLD RESET DETECTED - SUT powered off!")
            log.info(f"Frequency flatten caused system instability")
            log.info(f"Fuses automatically reverted to original values on cold reset")
            cold_reset_details = reset_result.get('cold_reset_details', {})
            return self._handle_cold_reset_freq_op(
                domain_info, before_ratios, cold_reset_details, 'frequency flatten'
            )
        
        # Only verify if enabled
        if ENABLE_SUT_VERIFICATION:
            if not reset_result.get('boot_success', True):
                log.warning(f"{reset_result['message']}")
            
            # Verify SUT is functional after frequency flatten
            verification_result = verify_post_fuse_update()
            if not verification_result['success']:
                return {'error': f"SUT verification failed: {verification_result['message']}"}
            
            log.info(f"[SUCCESS] SUT verified functional after frequency flatten")
        
        # Read after ratios with retry logic
        log.info("Reading frequency ratios after flatten operation...")
        _retry_result = self._read_ratios_with_retry(domain_info, 'flatten')
        if 'error' in _retry_result:
            return _retry_result
        after_ratios = _retry_result['ratios']
        
        after_frequencies = {key: (value * freq_mult if isinstance(value, (int, float)) else None)
                             for key, value in after_ratios.items()}
        
        # Generate report
        label = domain_info.get('label', domain_name.upper())
        
        # Use the stored BEFORE frequencies (not the current ones which are already flattened)
        df = pd.DataFrame({
            'WP': list(before_ratios.keys()),
            f'{label} Ratio (raw) Before': list(before_ratios.values()),
            f'{label} Ratio (raw) After': list(after_ratios.values()),
            f'{label} Freq (MHz) Before': [before_frequencies[key] for key in before_ratios.keys()],
            f'{label} Freq (MHz) After': [after_frequencies[key] for key in after_ratios.keys()]
        })

        excel_path = create_timestamped_filename(f'flatten_freq_{domain_name}', 'xlsx')
        export_multiple_sheets({'Before': df[['WP', f'{label} Ratio (raw) Before', f'{label} Freq (MHz) Before']],
                                'After': df[['WP', f'{label} Ratio (raw) After', f'{label} Freq (MHz) After']],
                                'Comparison': df}, excel_path)
        
        return {
            'dataframe': df,
            'excel_path': excel_path,
            'before_ratios': before_ratios,
            'after_ratios': after_ratios,
            'cold_reset_detected': False
        }
    
    def customize_frequency(self, domain_name, custom_frequencies):
        """
        Set custom frequency values for P0, P1, and Pn.
        
        Args:
            domain_name: Domain name
            custom_frequencies: Dict of {ratio_name: frequency_mhz}
                               e.g., {'p0': 4500, 'p1': 1500, 'pn': 400}
            
        Returns:
            dict: Before/after data and results
        """
        domain_info = self.config_loader.get_domain(domain_name)
        
        if not self.config_loader.has_flatten_support(domain_name):
            return {'error': f'Domain {domain_name} does not support frequency customization'}
        
        # Load fuse RAM and read before ratios
        load_fuse_ram(domain_info)
        before_ratios = read_frequency_ratios(domain_info)

        # Store ACTUAL frequency values before making changes (for display)
        # Guard: skip non-numeric values that mock mode may return for unknown registers.
        freq_mult = domain_info.get('freq_multiplier', 100)
        before_frequencies = {
            key: (value * freq_mult if isinstance(value, (int, float)) else None)
            for key, value in before_ratios.items()
        }

        log.info(f"[BACKUP] Saved original frequency ratios: {before_ratios}")
        log.info(f"[BACKUP] Current frequencies before customize: {before_frequencies}")
        log.info(f"[BACKUP] Target custom frequencies: {custom_frequencies}")
        
        # Convert custom frequencies (MHz) to ratios
        new_ratios = {}
        for key, freq_mhz in custom_frequencies.items():
            if key not in before_ratios:
                return {'error': f"Invalid ratio key: {key}. Must be one of {list(before_ratios.keys())}"}
            # Convert MHz to ratio value
            ratio_value = int(freq_mhz / freq_mult)
            new_ratios[key] = ratio_value
            log.info(f"{key.upper()}: {freq_mhz} MHz → ratio {ratio_value}")
        
        # Fill in any missing ratios with current values
        for key in before_ratios.keys():
            if key not in new_ratios:
                new_ratios[key] = before_ratios[key]
                log.info(f"{key.upper()}: Keeping current ratio {before_ratios[key]}")
        
        write_frequency_ratios(domain_info, new_ratios)
        
        # Flush and reset
        flush_fuse_ram(domain_info)
        log.info("Resetting target...")
        reset_result = reset_target()
        
        if not reset_result['reset_success']:
            return {'error': f"Target reset failed: {reset_result['message']}"}
        
        # CHECK FOR COLD RESET (POWER OFF)
        if reset_result.get('cold_reset_detected', False):
            log.info(f"[CRITICAL] COLD RESET DETECTED - SUT powered off!")
            log.info(f"Custom frequency caused system instability")
            log.info(f"Fuses automatically reverted to original values on cold reset")
            cold_reset_details = reset_result.get('cold_reset_details', {})
            return self._handle_cold_reset_freq_op(
                domain_info, before_ratios, cold_reset_details, 'custom frequency'
            )
        
        # Only verify if enabled
        if ENABLE_SUT_VERIFICATION:
            if not reset_result.get('boot_success', True):
                log.warning(f"{reset_result['message']}")
            
            # Verify SUT is functional after custom frequency
            verification_result = verify_post_fuse_update()
            if not verification_result['success']:
                return {'error': f"SUT verification failed: {verification_result['message']}"}
            
            log.info(f"[SUCCESS] SUT verified functional after custom frequency")
        
        # Read after ratios with retry logic
        log.info("Reading frequency ratios after customize operation...")
        _retry_result = self._read_ratios_with_retry(domain_info, 'customize')
        if 'error' in _retry_result:
            return _retry_result
        after_ratios = _retry_result['ratios']
        
        after_frequencies = {key: (value * freq_mult if isinstance(value, (int, float)) else None)
                             for key, value in after_ratios.items()}
        
        # Generate report
        label = domain_info.get('label', domain_name.upper())
        
        # Use the stored BEFORE frequencies
        df = pd.DataFrame({
            'WP': list(before_ratios.keys()),
            f'{label} Ratio (raw) Before': list(before_ratios.values()),
            f'{label} Ratio (raw) After': list(after_ratios.values()),
            f'{label} Freq (MHz) Before': [before_frequencies[key] for key in before_ratios.keys()],
            f'{label} Freq (MHz) After': [after_frequencies[key] for key in after_ratios.keys()]
        })

        excel_path = create_timestamped_filename(f'customize_freq_{domain_name}', 'xlsx')
        export_multiple_sheets({'Before': df[['WP', f'{label} Ratio (raw) Before', f'{label} Freq (MHz) Before']],
                                'After': df[['WP', f'{label} Ratio (raw) After', f'{label} Freq (MHz) After']],
                                'Comparison': df}, excel_path)
        
        return {
            'dataframe': df,
            'excel_path': excel_path,
            'before_ratios': before_ratios,
            'after_ratios': after_ratios,
            'before_frequencies': before_frequencies,
            'after_frequencies': after_frequencies,
            'cold_reset_detected': False
        }
    
    def edit_voltages(self, domain_name, voltage_changes, freq_changes=None):
        """
        Edit specific working point voltages and/or frequencies.

        Args:
            domain_name: Domain name
            voltage_changes: Dict of {wp_index: new_voltage_mv}  (may be empty)
            freq_changes:    Dict of {wp_index: new_freq_mhz}    (optional)

        Returns:
            dict: Before/after data and verification results
        """
        freq_changes = freq_changes or {}
        if not voltage_changes and not freq_changes:
            return {'error': 'No voltage or frequency changes specified'}
        
        domain_info = self.config_loader.get_domain(domain_name)
        wp_count = domain_info['wp_count']
        label = domain_info.get('label', domain_name.upper())
        
        # Log the changes being applied
        log.info(f"\n[INFO] ========== WP Edit Operation for {label} ==========")
        if voltage_changes:
            log.info(f"Voltage changes requested:")
            for wp_idx, new_voltage_mv in voltage_changes.items():
                log.info(f"  WP{wp_idx}: Set to {new_voltage_mv} mV")
        if freq_changes:
            log.info(f"Frequency changes requested:")
            for wp_idx, new_freq_mhz in freq_changes.items():
                log.info(f"  WP{wp_idx}: Set to {new_freq_mhz} MHz")
        
        # Validate WP indices
        for wp_idx in list(voltage_changes.keys()) + list(freq_changes.keys()):
            if wp_idx < 0 or wp_idx >= wp_count:
                return {'error': f'Invalid WP index {wp_idx}. Valid range: 0-{wp_count-1}'}
        
        # NOTE: Fuse RAM should already be loaded by the caller (wp_edit dialog)
        # Do NOT reload here as it would overwrite the current state with stale hardware values
        
        # Read before values (from already-loaded fuse RAM) - CRITICAL: Store for potential revert
        log.info(f"Reading current voltages from loaded fuse RAM...")
        before_data = read_all_wps(domain_info)
        log.info(f"[BACKUP] Saved original voltages for {label} (for potential revert)")
        for wp_idx in voltage_changes.keys():
            before_v, _ = before_data[wp_idx]
            before_mv = before_v * 1000 if before_v is not None else None
            _v_str: str   = f"{before_mv:.2f} mV ({before_v:.4f} V)" if before_v is not None else "N/A"
            log.info(f"  WP{wp_idx} current: {_v_str}")
        
        # Apply voltage changes
        if voltage_changes:
            log.info(f"Writing new voltages...")
            for wp_idx, new_voltage_mv in voltage_changes.items():
                log.info(f"  Writing WP{wp_idx}: {new_voltage_mv} mV...")
                success: bool = write_voltage(domain_info, wp_idx, new_voltage_mv)
                if not success:
                    return {'error': f'Failed to write voltage for WP{wp_idx}'}

        # Apply frequency changes
        if freq_changes:
            log.info(f"Writing new frequencies...")
            for wp_idx, new_freq_mhz in freq_changes.items():
                log.info(f"  Writing WP{wp_idx}: {new_freq_mhz} MHz...")
                success: bool = write_frequency(domain_info, wp_idx, new_freq_mhz)
                if not success:
                    return {'error': f'Failed to write frequency for WP{wp_idx}'}

        # Flush fuse RAM
        log.info(f"Flushing fuse RAM to hardware...")
        flush_fuse_ram(domain_info)
        log.info(f"================================================\n")
        
        # Reset target
        log.info("Resetting target...")
        reset_result = reset_target()
        
        if not reset_result['reset_success']:
            return {'error': f"Target reset failed: {reset_result['message']}"}
        
        # CHECK FOR COLD RESET (POWER OFF)
        if reset_result.get('cold_reset_detected', False):
            _frp = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
            return self._handle_cold_reset_voltage_op(
                [domain_name], {domain_name: before_data}, {_frp: domain_info},
                reset_result.get('cold_reset_details', {}), 'WP voltage edit',
            )

        # Only verify if enabled
        if ENABLE_SUT_VERIFICATION:
            if not reset_result.get('boot_success', True):
                log.warning(f"{reset_result['message']}")
            
            # Verify SUT is functional after voltage edit
            verification_result = verify_post_fuse_update()
            if not verification_result['success']:
                return {'error': f"SUT verification failed: {verification_result['message']}"}
            
            log.info(f"[SUCCESS] SUT verified functional after voltage edit")
        
        # Load fuse RAM and read after values
        load_fuse_ram(domain_info)
        after_data = read_all_wps(domain_info)
        
        # Generate before/after report
        label = domain_info.get('label', domain_name.upper())
        
        df_before = self._make_vf_dataframe_from_data(domain_info, before_data, wp_count, label)
        df_after = self._make_vf_dataframe_from_data(domain_info, after_data, wp_count, label)
        
        # Export to Excel
        excel_path: str = create_timestamped_filename(f'vf_curve_wp_edit_{domain_name}', 'xlsx')
        export_multiple_sheets({'Before': df_before, 'After': df_after}, excel_path)
        
        # Generate plot
        png_path: str = create_timestamped_filename(f'vf_curve_wp_edit_{domain_name}', 'png')
        plot_before_after(df_before, df_after, label, png_path)
        
        # Verify changes
        verification = self._verify_wp_edit(before_data, after_data, voltage_changes, freq_changes)

        return {
            'before_dataframe': df_before,
            'after_dataframe': df_after,
            'excel_path': excel_path,
            'png_path': png_path,
            'verification': verification,
            'reset_info': reset_result,
            'cold_reset_detected': False
        }
    
    def _make_vf_dataframe(self, domain_info, max_wp, label=None):
        """Create VF DataFrame for a domain.

        Voltage column shows the **effective** voltage (base + adder) when the
        domain carries a ``vf_voltage_adder`` field, so the user always sees
        what the hardware actually uses.  The raw base registers are NOT
        modified — adders are read-only fuse corrections applied here for
        display purposes only, keeping bump / edit / verify operations correct.

        For domains with ``vf_voltage_delta_idx1`` / ``vf_voltage_delta_idx2``
        (e.g. core bigcore base VF), two extra columns are appended showing the
        per-WP delta correction voltages (WP0 is always None since deltas start
        at index 1).
        """
        label = label or domain_info.get('label', '')
        wp_count = domain_info['wp_count']

        # Read all WPs — base voltage only (safe for bump/edit/verify)
        wps = read_all_wps(domain_info)
        voltages, freqs = zip(*wps) if wps else ([], [])
        voltages: list[Any] = list(voltages)
        freqs: list[Any] = list(freqs)

        # Sum in adder for effective display voltage (display only, not written back)
        adder_values = read_adder_voltages(domain_info)
        if adder_values is not None:
            voltages = [
                (v + adder_values[i]) if (v is not None and i < len(adder_values)) else v
                for i, v in enumerate(voltages)
            ]

        # Clamp to max_wp (pad with None if shorter, truncate if longer than effective count)
        voltages = (voltages + [None] * max_wp)[:max_wp]
        freqs    = (freqs    + [None] * max_wp)[:max_wp]

        data = {
            'WP': [f'P{i}' for i in range(max_wp)],
            f'{label} Voltage (V)': voltages,
            f'{label} Freq (MHz)': freqs,
        }

        # Append delta columns if the domain carries them
        delta_data = read_delta_voltages(domain_info)
        if delta_data is not None:
            for col_key, col_label in [('delta_idx1', f'{label} Delta Idx1 (V)'),
                                        ('delta_idx2', f'{label} Delta Idx2 (V)')]:
                raw_vals = list(delta_data.get(col_key, []))
                # Deltas cover WP1 onwards; pad front with None for WP0.
                # Clamp to exactly max_wp to guard against raw_vals already
                # including WP0 (length == max_wp) which would make the list
                # one element too long.
                padded = ([None] + raw_vals + [None] * max_wp)[:max_wp]
                data[col_label] = padded

        return pd.DataFrame(data)

    def _effective_wp_count(self, domain_info):
        """Return the number of WPs that actually carry data on this platform.

        Scans from the end of the vf_voltage/vf_ratio register list and returns
        the index of the last WP whose voltage OR frequency is non-zero, plus 1.
        Falls back to ``domain_info['wp_count']`` when all values are zero/None
        (e.g. in mock mode with blank registers).
        """
        wps = read_all_wps(domain_info)
        last_active = 0
        for idx, (v, f) in enumerate(wps):
            v_ok: bool = v is not None and float(v) != 0.0
            f_ok: bool = f is not None and float(f) != 0.0
            if v_ok or f_ok:
                last_active: int = idx + 1
        # Fallback: respect the full config count when all reads return 0
        return last_active if last_active > 0 else domain_info['wp_count']

    def _make_vf_dataframe_from_data(self, domain_info, wps_data, max_wp, label=None):
        """Create VF DataFrame from pre-read data.

        Clamps (or pads with None) wps_data to exactly *max_wp* rows so the
        DataFrame length always matches whether max_wp is smaller or larger than
        the raw data length.
        """
        label = label or domain_info.get('label', '')

        voltages, freqs = zip(*wps_data) if wps_data else ([], [])

        # Clamp to max_wp (pad with None if shorter, truncate if longer)
        voltages = (list(voltages) + [None] * max_wp)[:max_wp]
        freqs    = (list(freqs)    + [None] * max_wp)[:max_wp]

        return pd.DataFrame({
            'WP': [f'P{i}' for i in range(max_wp)],
            f'{label} Voltage (V)': voltages,
            f'{label} Freq (MHz)': freqs
        })
    
    def _make_cumulative_dataframe(self, domain_names, max_wp):
        """Create cumulative DataFrame for multiple domains."""
        data = {'WP': [f'P{i}' for i in range(max_wp)]}

        for domain_name in domain_names:
            domain_info = self.config_loader.get_domain(domain_name)
            label = domain_info.get('label', domain_name.upper())
            df = self._make_vf_dataframe(domain_info, max_wp, label)
            data[f'{label} Voltage (V)'] = df[f'{label} Voltage (V)']
            data[f'{label} Freq (MHz)'] = df[f'{label} Freq (MHz)']

        return pd.DataFrame(data)
    
    def _verify_bump(self, before_data, after_data, bump_mv, direction):
        """Verify bump operation succeeded within tolerance."""
        verification = {
            'success': True,
            'details': []
        }
        
        tolerance_mv = 5.7  # ~2 register steps
        
        for i, (before_wp, after_wp) in enumerate(zip(before_data, after_data)):
            before_v, _ = before_wp
            after_v, _ = after_wp
            
            if before_v is None or after_v is None:
                continue
            
            before_mv = before_v * 1000
            after_mv = after_v * 1000
            
            if direction == 'up':
                expected_mv = before_mv + bump_mv
            else:
                expected_mv = before_mv - bump_mv
            
            diff = abs(after_mv - expected_mv)
            
            if diff > tolerance_mv:
                verification['success'] = False
                verification['details'].append({
                    'wp': i,
                    'before_v': before_v,
                    'after_v': after_v,
                    'expected_v': expected_mv / 1000,
                    'diff_mv': diff
                })
        
        return verification
    
    def _verify_wp_edit(self, before_data, after_data, voltage_changes, freq_changes=None):
        """Verify WP edit operation succeeded within tolerance."""
        freq_changes = freq_changes or {}
        verification = {
            'success': True,
            'details': []
        }

        tolerance_mv  = 5.7   # ~2 voltage register steps
        tolerance_mhz = 1.0   # 1 MHz (single ratio step is freq_multiplier)

        # Verify voltage changes
        for wp_idx, target_mv in voltage_changes.items():
            before_v, _ = before_data[wp_idx]
            after_v,  _ = after_data[wp_idx]

            if before_v is None or after_v is None:
                continue

            after_mv = after_v * 1000
            diff = abs(after_mv - target_mv)

            if diff > tolerance_mv:
                verification['success'] = False

            verification['details'].append({
                'wp':               wp_idx,
                'kind':             'voltage',
                'before_v':         before_v,
                'after_v':          after_v,
                'expected_v':       target_mv / 1000,
                'diff_mv':          diff,
                'within_tolerance': diff <= tolerance_mv
            })

        # Verify frequency changes
        for wp_idx, target_mhz in freq_changes.items():
            _, before_freq = before_data[wp_idx]
            _, after_freq  = after_data[wp_idx]

            if after_freq is None:
                continue

            diff_mhz = abs((after_freq or 0) - target_mhz)

            if diff_mhz > tolerance_mhz:
                verification['success'] = False

            verification['details'].append({
                'wp':               wp_idx,
                'kind':             'freq',
                'before_freq':      before_freq,
                'after_freq':       after_freq,
                'expected_freq':    target_mhz,
                'diff_mhz':         diff_mhz,
                'within_tolerance': diff_mhz <= tolerance_mhz
            })

        return verification

    def _handle_cold_reset_voltage_op(
            self, domain_names, before_data, unique_fuse_rams,
            cold_reset_details, op_name='voltage'):
        """Verify voltage revert after cold reset; shared by bump_voltages and edit_voltages.

        Avoids duplicating ~25 lines of identical cold-reset recovery logic across
        all voltage mutation methods.

        Args:
            domain_names:       List of domain names affected.
            before_data:        {domain_name: [(voltage_v, freq_mhz), ...]} pre-change.
            unique_fuse_rams:   {fuse_ram_path: domain_info} for affected domains.
            cold_reset_details: cold_reset_details sub-dict from reset_result.
            op_name:            Human-readable label for log/message text.

        Returns:
            dict: Result dict with ``error='COLD_RESET'`` and revert verification info.
        """
        log.info(f"[CRITICAL] COLD RESET DETECTED - SUT powered off!")
        log.info(f"{op_name} settings caused system instability")
        log.info(f"Fuses automatically reverted to original values on cold reset")

        revert_verified, revert_details = self._verify_automatic_revert(
            domain_names, before_data, unique_fuse_rams
        )
        return {
            'error':                'COLD_RESET',
            'cold_reset_detected':  True,
            'cold_reset_details':   cold_reset_details,
            'auto_revert_verified': revert_verified,
            'revert_details':       revert_details,
            'message': (
                f"\U0001f534 COLD RESET: SUT POWERED OFF COMPLETELY\n\n"
                f"The {op_name} change caused system instability:\n"
                f"\u2022 Indicators: {', '.join(cold_reset_details.get('indicators', ['Power lost']))}\n\n"
                f"{revert_details}"
            ),
        }

    def _handle_cold_reset_freq_op(self, domain_info, before_ratios, cold_reset_details, op_name):
        """Verify ratio revert after cold reset from a frequency operation.

        Shared by :meth:`flatten_frequency` and :meth:`customize_frequency` to
        avoid duplicating ~30 lines of identical cold-reset recovery logic.

        Args:
            domain_info:        Domain configuration dict.
            before_ratios:      Frequency ratios captured before the operation.
            cold_reset_details: ``cold_reset_details`` sub-dict from reset_result.
            op_name:            Human-readable operation label for log/message text.

        Returns:
            dict: Result dict with ``error='COLD_RESET'`` and revert verification info.
        """
        try:
            log.info(f"[VERIFY] Checking if frequency ratios reverted to original values...")
            recover_from_deep_sleep(bypass_cooldown=True)
            load_fuse_ram(domain_info)
            current_ratios = read_frequency_ratios(domain_info)
            revert_verified = (current_ratios == before_ratios)
            if revert_verified:
                log.info(f"[SUCCESS] Original frequency ratios confirmed after cold reset")
            else:
                log.warning(f"Frequency ratios differ from original - system may be unstable")
        except Exception as ex:
            log.error(f"Could not verify frequency ratios after cold reset: {ex}")
            revert_verified = False

        return {
            'error': 'COLD_RESET',  # Special error code
            'cold_reset_detected': True,
            'cold_reset_details': cold_reset_details,
            'auto_revert_verified': revert_verified,
            'message': (
                f"SUT POWERED OFF after {op_name}! This usually means the frequency settings caused system instability.\n\n"
                f"Detected: {', '.join(cold_reset_details.get('indicators', ['Unknown']))}\n\n"
                f"The fuses automatically reverted to original values when the system powered off.\n"
                f"{'Verified: Original ratios are restored.' if revert_verified else 'Warning: Could not verify original ratios - check system state.'}"
            )
        }

    def _verify_automatic_revert(self, domain_names, before_data, unique_fuse_rams):
        """
        Verify that fuses automatically reverted after cold reset.
        
        When a cold reset (power cycle) occurs, the hardware automatically reverts
        fuses to their originally programmed values (factory/fuse defaults).
        This function checks what values are present after cold reset.
        
        Args:
            domain_names: List of domain names
            before_data: Dict of {domain_name: [(voltage_v, freq_mhz), ...]} - values BEFORE our change
            unique_fuse_rams: Dict of unique fuse RAM paths
            
        Returns:
            tuple: (bool success, str details_message)
        """
        try:
            timestamp: str = datetime.now().isoformat()
            log.info(f"[VERIFY] ========== VERIFYING COLD RESET AUTO-REVERT [{timestamp}] ==========")
            log.info(f"[VERIFY] Checking what values hardware restored after cold reset...")
            
            # After cold reset, system needs significant time to power back on
            # Wait for system to complete cold boot before attempting ITP access
            log.info(f"[VERIFY] Waiting for system to power on after cold reset...")
            log.info(f"[VERIFY] This may take 60-120 seconds for full cold boot...")
            
            # Wait for SUT to become reachable with longer timeout
            boot_success = wait_for_sut_boot(timeout_seconds=180, check_interval=3, min_boot_time=30)
            
            if not boot_success:
                log.warning(f"[{datetime.now().isoformat()}] System did not boot within timeout after cold reset")
                return False, "❌ System did not recover after cold reset.\nPlease manually power on the SUT and verify fuse values."
            
            log.info(f"[SUCCESS] [{datetime.now().isoformat()}] System is reachable after cold reset")
            
            # Try multiple recovery attempts with increasing delays
            max_recovery_attempts = 3
            for attempt in range(1, max_recovery_attempts + 1):
                log.info(f"[VERIFY] [{datetime.now().isoformat()}] ITP recovery attempt {attempt}/{max_recovery_attempts}...")
                recovery_success: bool = recover_from_deep_sleep(bypass_cooldown=True)
                
                if recovery_success:
                    log.info(f"[SUCCESS] [{datetime.now().isoformat()}] ITP recovery successful on attempt {attempt}")
                    break
                    
                if attempt < max_recovery_attempts:
                    wait_time: int = 15 * attempt  # Increasing backoff: 15s, 30s
                    log.info(f"Recovery attempt {attempt} failed, waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
            else:
                log.warning(f"[{datetime.now().isoformat()}] All ITP recovery attempts failed")
                return False, "❌ Could not establish ITP connection after cold reset.\nPlease manually verify fuse values."
            
            # Load fuse RAM for all unique paths with retry logic
            log.info(f"[VERIFY] Loading fuse RAM to read current values...")
            for fuse_ram_path, domain_info in unique_fuse_rams.items():
                load_success = False
                for load_attempt in range(1, 3):
                    success: None | bool = load_fuse_ram(domain_info)
                    if success:
                        load_success = True
                        break
                    log.warning(f"Load attempt {load_attempt} failed for {fuse_ram_path}, retrying...")
                    time.sleep(5)
                
                if not load_success:
                    log.warning(f"[{datetime.now().isoformat()}] Could not load fuse RAM for verification at {fuse_ram_path}")
                    return False, "❌ Could not read fuses after cold reset.\nITP connection unstable."
            
            # Read current voltages for each domain and build comparison details
            log.info(f"[VERIFY] Reading current fuse values after cold reset...")
            details_lines = []
            reverted_to_before = True
            
            for domain_name in domain_names:
                domain_info = self.config_loader.get_domain(domain_name)
                label = domain_info.get('label', domain_name.upper())
                before_voltages = before_data[domain_name]
                
                log.info(f"[VERIFY] Checking {label}...")
                current_data = read_all_wps(domain_info)
                
                details_lines.append(f"\n{label} Domain:")
                
                # Compare each WP
                for wp_idx, ((before_v, before_f), (curr_v, curr_f)) in enumerate(zip(before_voltages, current_data)):
                    if before_v is None or curr_v is None:
                        continue
                    
                    before_mv = before_v * 1000
                    curr_mv = curr_v * 1000
                    diff_mv = curr_mv - before_mv
                    
                    # Check if it reverted to our "before" values
                    if abs(diff_mv) > 5.7:  # More than tolerance
                        reverted_to_before = False
                        details_lines.append(f"  WP{wp_idx}: {curr_mv:.2f}mV (was {before_mv:.2f}mV before our change, diff: {diff_mv:+.2f}mV)")
                    else:
                        details_lines.append(f"  WP{wp_idx}: {curr_mv:.2f}mV (matches pre-change value)")
            
            details_message: str = "\n".join(details_lines)
            
            if reverted_to_before:
                log.info(f"[SUCCESS] ========== REVERTED TO PRE-CHANGE VALUES ==========")
                log.info(f"[SUCCESS] Hardware restored the voltages that existed before our change")
                return True, f"✅ Hardware restored pre-change values:\n{details_message}"
            else:
                log.info(f"========== REVERTED TO FUSE DEFAULT VALUES ==========")
                log.info(f"Hardware restored fuse defaults (not our pre-change values)")
                log.info(f"This is normal behavior - cold reset goes to programmed fuse values")
                return True, f"✅ Hardware restored fuse default values:\n{details_message}\n\n⚠️ Note: Values differ from pre-change state.\nThis is normal - cold reset restores programmed fuse defaults."
            
        except Exception as ex:
            log.error(f"Exception during verification: {ex}")
            import traceback
            traceback.print_exc()
            return False, f"❌ Error verifying auto-revert: {ex}"

    # ================================================================== #
    # Undo / revert                                                        #
    # ================================================================== #

    def revert_from_undo_log(self) -> dict:
        """
        Revert the most recent voltage operation recorded in the undo log.

        Reads the last entry from  vf_curve_manager_undo_log.json,
        restores each WP voltage to its saved value using write_voltage(),
        flushes fuse RAM, and resets the target.

        Returns:
            dict with keys:
                'reverted_op'       : str  — operation type that was undone
                'domains'           : list — domain names affected
                'entries_remaining' : int  — undo log entries left after this revert
            or  {'error': str}  on failure
        """
        if not _UNDO_LOG_PATH.exists():
            return {'error': 'No undo log found. No operations to revert.'}

        try:
            with open(_UNDO_LOG_PATH, 'r') as f:
                history = json.load(f)
        except Exception as ex:
            return {'error': f'Failed to read undo log: {ex}'}

        if not history:
            return {'error': 'Undo log is empty. No operations to revert.'}

        entry = history[-1]
        op_type  = entry.get('operation', 'unknown')
        domains: list[Any]  = list(entry.get('domains', {}).keys())

        log.info(f"[REVERT] Reverting '{op_type}' on domains: {', '.join(domains)}")
        log.info(f"[REVERT] Timestamp: {entry.get('timestamp', 'unknown')}")

        # Determine unique fuse RAM paths
        unique_fuse_rams: dict = {}
        for domain_name in domains:
            domain_info = self.config_loader.get_domain(domain_name)
            if domain_info is None:
                return {'error': f"Domain '{domain_name}' not found in current configuration."}
            fuse_ram_path = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
            if fuse_ram_path not in unique_fuse_rams:
                unique_fuse_rams[fuse_ram_path] = domain_info

        # Load fuse RAM
        for domain_info in unique_fuse_rams.values():
            load_fuse_ram(domain_info)

        # Restore voltages
        saved_domains = entry.get('domains', {})
        for domain_name, wp_list in saved_domains.items():
            domain_info = self.config_loader.get_domain(domain_name)
            if domain_info is None:
                log.warning(f"Domain '{domain_name}' not found — skipping")
                continue
            for wp_idx, (voltage_v, _freq) in enumerate(wp_list):
                if voltage_v is None:
                    continue
                voltage_mv = int(round(voltage_v * 1000))
                success: bool = write_voltage(domain_info, wp_idx, voltage_mv)
                if success:
                    log.info(f"[REVERT]   {domain_name} WP{wp_idx}: restored {voltage_mv} mV")
                else:
                    log.warning(f"{domain_name} WP{wp_idx}: write failed")

        # Flush fuse RAM and reset
        for domain_info in unique_fuse_rams.values():
            flush_fuse_ram(domain_info)

        log.info("[REVERT] Resetting target...")
        reset_target()

        # Remove the reverted entry from the log
        history.pop()
        try:
            with open(_UNDO_LOG_PATH, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as ex:
            log.warning(f"Could not update undo log after revert: {ex}")

        return {
            'reverted_op': op_type,
            'domains': domains,
            'entries_remaining': len(history),
        }

    # ================================================================== #
    # Voltage sweep                                                        #
    # ================================================================== #

    def sweep_voltages(self, domain_names: list, from_mv: int, to_mv: int, step_mv: int) -> dict:
        """
        Sweep voltages from  from_mv  to  to_mv  (relative to current baseline)
        in  step_mv  increments, recording pass/fail at each step.

        The sweep applies CUMULATIVE bumps from the current hardware state.
        A cold reset at any step halts the sweep immediately.

        Args:
            domain_names: List of domain names to sweep together
            from_mv:      Start offset in mV (e.g. -50)
            to_mv:        End offset in mV  (e.g. +50)
            step_mv:      Step size in mV   (e.g.  10, always positive)

        Returns:
            dict: {
                'steps':        list of step result dicts,
                'passed':       int,
                'total':        int,
                'stopped_early':bool,
                'excel_path':   str or None,
            }
        """
        step_mv = abs(step_mv)
        if step_mv == 0:
            return {'error': 'step_mv must be non-zero'}

        # Build ordered list of absolute offsets
        sign: int = 1 if to_mv >= from_mv else -1
        offsets = []
        mv: int = from_mv
        while (sign > 0 and mv <= to_mv) or (sign < 0 and mv >= to_mv):
            offsets.append(mv)
            mv += sign * step_mv

        if not offsets:
            return {'error': 'No valid sweep steps — check from/to/step values'}

        log.info(f"\n[SWEEP] {len(offsets)} steps: {offsets[0]:+d} mV → {offsets[-1]:+d} mV  "
              f"(step={step_mv} mV,  domains={', '.join(domain_names)})\n")

        steps_results = []
        current_offset = 0   # tracks cumulative offset from baseline

        for step_offset in offsets:
            delta = step_offset - current_offset
            if delta == 0:
                continue

            direction: str = 'up' if delta > 0 else 'down'
            log.info(f"[SWEEP] Applying {delta:+d} mV ({direction}) → cumulative offset {step_offset:+d} mV")

            result = self.bump_voltages(domain_names, abs(delta), direction)

            step_rec = {
                'offset_mv':  step_offset,
                'delta_mv':   delta,
                'timestamp':  datetime.now().isoformat(),
                'status':     'pass',
                'error':      None,
                'cold_reset': False,
            }

            if 'error' in result:
                if result.get('error') == 'COLD_RESET':
                    step_rec['status'] = 'cold_reset'
                    step_rec['cold_reset'] = True
                    step_rec['error'] = 'Cold reset detected'
                    steps_results.append(step_rec)
                    log.info(f"[SWEEP] ❌ Cold reset at {step_offset:+d} mV — stopping sweep")
                    break
                else:
                    step_rec['status'] = 'fail'
                    step_rec['error'] = result['error']
                    steps_results.append(step_rec)
                    log.info(f"[SWEEP] ✗ Error at {step_offset:+d} mV: {result['error']} — stopping sweep")
                    break
            else:
                current_offset = step_offset
                steps_results.append(step_rec)
                log.info(f"[SWEEP] ✓ {step_offset:+d} mV  PASS")

        passed: int = sum(1 for s in steps_results if s['status'] == 'pass')
        stopped_early: bool = any(s['status'] in ('cold_reset', 'fail') for s in steps_results)

        # Save sweep report to CSV
        excel_path = None
        png_path = None
        try:
            df_sweep = pd.DataFrame(steps_results)
            excel_path = create_timestamped_filename('vf_sweep_results', 'xlsx')
            export_dataframe_to_excel(df_sweep, excel_path, 'Sweep Results')
            log.info(f"\n[SWEEP] Results saved: {excel_path}")
        except Exception as ex:
            log.warning(f"Could not save sweep report: {ex}")
        # Generate sweep chart
        if steps_results:
            try:
                offsets  = [s.get('voltage_offset_mv', 0) for s in steps_results]
                statuses = [s.get('status', '') for s in steps_results]
                colours  = ['green' if st == 'pass' else 'red' for st in statuses]
                fig, ax  = plt.subplots(figsize=(10, 5))
                ax.scatter(offsets, [1] * len(offsets), c=colours, s=80, zorder=3)
                ax.axhline(1, color='grey', linewidth=0.5, linestyle='--')
                ax.set_xlabel('Voltage Offset (mV)')
                ax.set_title('Sweep Results')
                ax.set_yticks([])
                legend_handles = [
                    Line2D([0], [0], marker='o', color='w', markerfacecolor='green', markersize=8, label='Pass'),
                    Line2D([0], [0], marker='o', color='w', markerfacecolor='red',   markersize=8, label='Fail/Reset'),
                ]
                ax.legend(handles=legend_handles, loc='upper right')
                png_path = create_timestamped_filename('vf_sweep_results', 'png')
                fig.savefig(png_path, bbox_inches='tight', dpi=150)
                plt.close(fig)
                log.info(f"[SWEEP] Chart saved: {png_path}")
            except Exception as _chart_ex:
                log.warning(f"Could not generate sweep chart: {_chart_ex}")

        return {
            'steps':         steps_results,
            'passed':        passed,
            'total':         len(steps_results),
            'stopped_early': stopped_early,
            'excel_path':    excel_path,
            'png_path':      png_path,
        }

    # -----------------------------------------------------------------------
    # Scalar modifier operations
    # -----------------------------------------------------------------------

    def show_scalar_modifiers(self, type_filter: str = None) -> dict:
        """Read all scalar modifier registers and return a structured report.

        Args:
            type_filter: Optional type key (e.g. 'itd_voltage', 'p0_override')
                         to limit output to a specific class.  None = all types.

        Returns:
            dict:
                'modifiers': list of dicts, each with label/type/encoding/
                             raw/converted/units/register/fuse_path
                'total':     int
                'by_type':   dict {type_key: [modifier_dict, ...]}
                'ok':        bool
        """
        from utils.hardware_access import read_all_scalar_modifiers as _read_all

        scalars = self.config_loader.get_scalar_modifiers()
        if not scalars:
            log.info("No scalar modifiers configured.  "
                  "Run auto-discovery to populate them.")
            return {'modifiers': [], 'total': 0, 'by_type': {}, 'ok': True}

        if type_filter:
            scalars = {k: v for k, v in scalars.items()
                       if v.get('type') == type_filter}

        log.info(f"\n[SCALARS] Reading {len(scalars)} scalar modifier(s) from hardware...")
        results = _read_all(scalars)

        modifiers = []
        by_type:  dict = {}
        for reg_name, r in results.items():
            info = scalars.get(reg_name, {})
            entry = {
                'register':   reg_name,
                'label':      r.get('label', reg_name),
                'type':       r.get('type', 'unknown'),
                'encoding':   r.get('encoding', 'raw'),
                'raw':        r.get('raw'),
                'converted':  r.get('converted', ''),
                'units':      r.get('units', ''),
                'fuse_path':  info.get('fuse_path', ''),
                'description':info.get('description', ''),
                'ok':         r.get('ok', False),
            }
            modifiers.append(entry)
            by_type.setdefault(entry['type'], []).append(entry)

        modifiers.sort(key=lambda x: (x['type'], x['register']))

        log.info(f"\n{'='*70}")
        log.info(f"SCALAR MODIFIERS")
        log.info(f"{'='*70}")
        for t, entries in sorted(by_type.items()):
            log.info(f"\n  [{t.upper()}]")
            for e in entries:
                val_str: str = f"{e['converted']} {e['units']}".strip() if e['ok'] else 'read error'
                log.info(f"    {e['register']:<55}  {val_str}")
        log.info("")

        return {
            'modifiers': modifiers,
            'total':     len(modifiers),
            'by_type':   by_type,
            'ok':        True,
        }

    def edit_scalar_modifier(self, register_name: str, new_physical_value: float) -> dict:
        """Write a new value to a single scalar modifier register.

        Args:
            register_name:     The register key from scalar_modifiers config.
            new_physical_value: Physical value in natural units
                               (MHz for ratio_mhz, mV for voltage_mv, raw otherwise).

        Returns:
            dict:
                'ok':      bool
                'before':  dict with previous raw/converted/units
                'after':   dict with new raw/converted/units
                'message': str
        """
        from utils.hardware_access import (
            read_scalar_modifier         as _read,
            write_scalar_modifier        as _write,
            scalar_physical_to_raw       as _to_raw,
        )

        scalars = self.config_loader.get_scalar_modifiers()
        if register_name not in scalars:
            return {'ok': False,
                    'message': f"'{register_name}' not found in scalar_modifiers config"}

        info = scalars[register_name]
        label = info.get('label', register_name)

        # Read current value
        before = _read(info)
        if not before['ok']:
            return {'ok': False, 'message': f"Could not read '{register_name}'"}

        raw_new: int = _to_raw(new_physical_value, info)
        units   = before.get('units', 'raw')
        log.info(f"[SCALAR EDIT] '{label}'")
        log.info(f"  Before : {before['converted']} {units}  (raw={before['raw']})")
        log.info(f"  Writing: {new_physical_value} {units}  → raw={raw_new}")

        ok: bool = _write(info, raw_new)
        after = _read(info)

        msg: str = (f"OK  — {label}: {before['converted']} {units} → "
               f"{after.get('converted','?')} {units}"
               if ok else f"FAILED  — {label}")

        return {
            'ok':      ok,
            'before':  before,
            'after':   after,
            'message': msg,
        }


