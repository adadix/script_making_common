"""DiscoveryMixin — discovered-registers tab and scalar-modifiers dialog."""
from __future__ import annotations

from PyQt5.QtWidgets import QMessageBox

import logging
log = logging.getLogger(__name__)


class DiscoveryMixin:
    """Handles live/cached register discovery and the Scalar Modifiers dialog."""

    # ── Live hardware scan ────────────────────────────────────────────────────

    def _live_scan_registers(self):
        """Scan all VF registers directly from live hardware.

        Called when no discovery cache exists so the tab is always accurate
        without relying on a pre-built / stale cache file.

        Returns:
            (records, platform_display, timestamp)  on success
            (None,    None,             None)        on failure
        """
        try:
            from discovery.auto_discover_vf_registers import (
                detect_platform_name, load_platform_config,
                discover_fuse_paths, load_fuse_ram_once,
                analyze_fuse_path, _all_results_to_flat_records,
                _save_discovery_cache, auto_learn_unknown_patterns,
            )
            import time as _time

            log.info("No discovery cache — scanning registers live from hardware...")

            platform_name    = detect_platform_name()
            cfg              = load_platform_config(platform_name)
            platform_display = cfg.get('display_name', platform_name)
            fuse_root        = cfg['fuse_root']

            fuse_paths = discover_fuse_paths(cfg)
            if not fuse_paths:
                log.error("No fuse paths found — ITP not connected or platform not recognised.")
                return None, None, None

            log.info("Found %d fuse path(s). Loading fuse RAM (may take 2-5 min)...",
                     len(fuse_paths))
            load_fuse_ram_once(fuse_root)

            # Register all discovered fuse roots so the session guard prevents
            # accidental re-calls to load_fuse_ram for sibling roots.
            try:
                from utils.hardware_access import notify_fuse_ram_loaded
                _seen: set = set()
                for _fp in fuse_paths:
                    _parts = _fp.split('.')
                    if len(_parts) >= 2:
                        _root = '.'.join(_parts[:2])
                        if _root not in _seen:
                            notify_fuse_ram_loaded(_root)
                            _seen.add(_root)
            except Exception:
                pass

            all_path_results = {}
            for path_str in fuse_paths:
                label  = path_str.split('.')[-1]
                result = analyze_fuse_path(path_str, label, cfg)
                if result:
                    all_path_results[path_str] = result

            if not all_path_results:
                log.error("No accessible registers found — check fuse paths.")
                return None, None, None

            auto_learn_unknown_patterns(all_path_results, platform_name, cfg)

            records   = _all_results_to_flat_records(all_path_results)
            timestamp = _time.strftime('%Y-%m-%d %H:%M:%S')
            _save_discovery_cache(records, platform_name, platform_display)

            log.info("Live scan complete: %d registers, cache saved.", len(records))
            return records, platform_display, timestamp

        except Exception as _lse:
            import traceback
            log.error("Live scan error: %s", _lse)
            traceback.print_exc()
            return None, None, None

    # ── Open registers tab ────────────────────────────────────────────────────

    def open_registers_tab(self):
        """Load discovery cache, refresh values from hardware, show the tab."""
        try:
            from discovery.auto_discover_vf_registers import load_discovery_cache
        except ImportError as exc:
            QMessageBox.warning(self, 'Discovery Module',
                                f'Discovery module not available:\n{exc}')
            return

        records, _pname, platform_display, timestamp = load_discovery_cache()

        if not records:  # None (missing cache) OR [] (empty/corrupt cache)
            records, platform_display, timestamp = self._live_scan_registers()
            _pname = None
            if not records:
                QMessageBox.information(
                    self, 'Discovery Required',
                    'No cached data and live hardware scan failed.\n\n'
                    'Check that ITP is connected and fuse paths are accessible,\n'
                    'then click this button again.\n\n'
                    'You can also run:  python auto_discover_vf_registers.py',
                )
                return

        # ── Live hardware refresh ─────────────────────────────────────────
        hw_status = 'cached'
        try:
            from utils.hardware_access import load_fuse_ram, get_fuse_object
            from collections import defaultdict

            path_groups = defaultdict(list)
            for rec in records:
                fp = rec.get('fuse_path', '')
                if fp:
                    path_groups[fp].append(rec)

            loaded_ram = set()
            for fp in path_groups:
                parts    = fp.split('.')
                fuse_ram = '.'.join(parts[:-1]) if len(parts) > 1 else fp
                if fuse_ram not in loaded_ram:
                    stub = {'fuse_path': fp, 'fuse_ram_path': fuse_ram,
                            'label': parts[-1]}
                    try:
                        if load_fuse_ram(stub):
                            loaded_ram.add(fuse_ram)
                    except Exception as _le:
                        log.warning("load_fuse_ram(%s): %s", fuse_ram, _le)

            refreshed = 0
            for fp, recs_in_path in path_groups.items():
                fuse_obj = get_fuse_object(fp)
                if fuse_obj is None:
                    continue
                for rec in recs_in_path:
                    reg_name = rec.get('name', '')
                    if not reg_name:
                        continue
                    try:
                        live_val = getattr(fuse_obj, reg_name, None)
                        if live_val is not None:
                            rec['value']  = live_val
                            rec['hex']    = f'0x{live_val:x}'
                            rec['active'] = bool(live_val)
                            refreshed += 1
                    except Exception:
                        pass

            if refreshed > 0:
                hw_status = f'\U0001f7e2 live \u2014 {refreshed} registers'
                try:
                    from discovery.auto_discover_vf_registers import _save_discovery_cache
                    _save_discovery_cache(
                        records,
                        _pname or 'generic',
                        platform_display or 'Unknown Platform',
                    )
                except Exception as _ce:
                    log.warning("Could not persist refreshed values: %s", _ce)
            else:
                hw_status = '\U0001f7e1 cached (no live reads)'

        except Exception as ex:
            log.info("Hardware refresh skipped: %s", ex)
            hw_status = '\u26aa cached'

        # Remove stale tab if present, then rebuild fresh
        for idx in range(self.output_tabs.count()):
            if self.output_tabs.tabText(idx).startswith('\U0001f50d'):
                self.output_tabs.removeTab(idx)
                break

        tab_widget = self._build_registers_tab_widget(
            records, platform_display or _pname or 'Unknown', timestamp, hw_status)
        _badge = ('\U0001f7e2'
                  if ('\U0001f7e2' in hw_status or 'live' in hw_status.lower())
                  else '\u26a0')
        tab_idx = self.output_tabs.addTab(
            tab_widget, f'\U0001f50d Discovered Registers {_badge}')
        self.output_tabs.setCurrentIndex(tab_idx)

    # ── Tab builder (delegates to tabs/registers_tab.py) ─────────────────────

    def _build_registers_tab_widget(self, records: list, platform_display: str,
                                    timestamp: str, hw_status: str = '\u26aa cached'):
        """Build the Discovered Registers tab widget."""
        from ui.tabs.registers_tab import build_registers_tab_widget as _build
        return _build(records, platform_display, timestamp, hw_status)

    # ── Scalar Modifiers dialog ───────────────────────────────────────────────

    def open_scalar_modifiers_dialog(self):
        """Open the Scalar Modifiers dialog."""
        if not hasattr(self, 'curve_engine') or self.curve_engine is None:
            QMessageBox.warning(self, 'Not Initialised',
                                'VF Curve Manager is not initialised yet.\n'
                                'Please connect to hardware first.')
            return
        from ui.dialogs.scalar_modifiers import ScalarModifiersDialog
        dlg = ScalarModifiersDialog(self.curve_engine, parent=self)
        dlg.exec_()
