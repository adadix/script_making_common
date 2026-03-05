"""DiscoveryMixin — discovered-registers tab and scalar-modifiers dialog."""
from __future__ import annotations

import logging
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import QMessageBox, QProgressDialog

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_spec_query(platform: str, codesign_project: str,
                      no_description: list,
                      no_conversion: list,
                      total_registers: int = 0) -> str:
    """Return a structured Copilot query covering BOTH gaps:

    1. Registers with no description at all (no_description)
    2. Registers that have a description but are missing precision/units
       needed for the Converted column (no_conversion)

    The inline list shows up to 200 names per section; the full lists are
    always written to spec_db_request.json / spec_query_for_copilot.txt.
    """
    import pathlib
    proj_hint = f' (CoDesign project: **{codesign_project}**)' if codesign_project else ''
    # src/ui/mixins/ → .parent×3 = src/  → / 'utils' = src/utils/
    tool_root = pathlib.Path(__file__).parent.parent.parent / 'utils'
    req_file  = tool_root / 'spec_db_request.json'
    txt_file  = tool_root / 'spec_query_for_copilot.txt'

    INLINE_LIMIT = 200

    def _name_block(names: list) -> str:
        show = names[:INLINE_LIMIT]
        block = '\n'.join(f'  - {n}' for n in show)
        if len(names) > INLINE_LIMIT:
            block += f'\n  ... and {len(names) - INLINE_LIMIT} more — full list in `{req_file}`'
        return block

    # --- Section 1: completely missing entries ---
    sec1 = ''
    if no_description:
        sec1 = (
            f'### Section 1 — {len(no_description)} registers with NO spec entry\n'
            f'Add a full entry for each (all fields below).\n'
            f'{_name_block(no_description)}\n'
        )

    # --- Section 2: conversion gap ---
    sec2 = ''
    if no_conversion:
        sec2 = (
            f'### Section 2 — {len(no_conversion)} registers MISSING `precision` and/or `units`\n'
            f'These already have a `description` in fuse_spec_db.json but the '
            f'`precision` and `units` fields are empty or missing.\n'
            f'The **Converted** column in the VF Curve Manager UI uses these fields to '
            f'display physical values (e.g. mV, MHz). Without them the column is blank.\n'
            f'Please look up and fill in `precision` (e.g. `"U1.8"`, `"100MHz"`, `"mV"`) '
            f'and `units` (e.g. `"V"`, `"MHz"`, `"mV"`, `"1/C"`) for each register below.\n'
            f'{_name_block(no_conversion)}\n'
        )

    total_str = f' (out of {total_registers} total scanned)' if total_registers else ''
    return (
        f'Platform **{platform}**{proj_hint} was scanned by the '
        f'VF Curve Manager{total_str}.\n\n'
        f'Two spec-data gaps were found in `src/fuse_spec_db.json` that need '
        f'to be fixed using the **CoDesign MCP**:\n\n'
        f'**Required JSON fields for every entry:**\n'
        f'`description`, `precision`, `units`, `width`, `default`, `domain`, `doc_source`\n'
        f'Match the format of existing WCL / NVL / GFC entries. '
        f'Include ALL categories, not just VF/ITD/PM.\n\n'
        f'**Conversion fields are critical** — `precision` controls how the raw fuse integer '
        f'is displayed as a physical value in the UI (e.g. `"U1.8"` → divide by 256 → volts; '
        f'`"100MHz"` → multiply by 100 → MHz). Without these the Converted column is blank.\n\n'
        f'{sec1}'
        f'{sec2}'
        f'Full register lists also saved to:\n'
        f'  `{req_file}`\n'
        f'  `{txt_file}`'
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _DiscoveryWorker(QThread):
    """Run discovery in a background thread so the Qt event loop never blocks."""

    finished      = pyqtSignal(object, object, object, object)
    progress      = pyqtSignal(str)
    progress_step = pyqtSignal(int, int)   # (current_path_index, total_paths)
    error         = pyqtSignal(str)
    # Emitted when the platform has zero spec-DB coverage.
    # Args: (platform_name, pre_composed_copilot_query)
    spec_missing  = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            records, platform_display, timestamp, hw_status = self._do_work()
            self.finished.emit(records, platform_display, timestamp, hw_status)
        except Exception as exc:
            import traceback
            log.error("DiscoveryWorker unhandled: %s", exc)
            traceback.print_exc()
            self.error.emit(str(exc))
            self.finished.emit(None, None, None, None)

    def _do_work(self):
        from discovery.auto_discover_vf_registers import load_discovery_cache
        import time as _time

        self.progress.emit("Loading discovery cache...")
        self.progress_step.emit(0, 0)   # indeterminate while loading
        records, _pname, platform_display, timestamp = load_discovery_cache()

        if not records:
            self.progress.emit("No cache -- detecting platform...")
            records, _pname, platform_display, timestamp = self._live_scan()
            if not records:
                return None, None, None, None
            # spec_missing check AFTER full scan (all fuses read + saved)
            self._emit_spec_missing_if_needed(_pname, records)
            return records, platform_display, timestamp, "live -- just discovered"

        # Cache exists — enrich with HAS spec metadata
        if _pname:
            try:
                from discovery.spec_db import enrich_records
                enrich_records(_pname, records)
            except Exception:
                pass

        self.progress.emit(
            "Cache loaded ({} registers) -- refreshing from hardware...".format(len(records))
        )
        self.progress_step.emit(0, 0)   # indeterminate while about to start hw refresh
        hw_status = self._hw_refresh(records, _pname, platform_display)

        # spec_missing check only AFTER hw refresh so all values are fresh
        if _pname:
            self._emit_spec_missing_if_needed(_pname, records)

        return records, platform_display or _pname or "Unknown", timestamp, hw_status

    def _emit_spec_missing_if_needed(self, platform_name: str, records: list) -> None:
        """Emit spec_missing whenever any register is missing description OR
        conversion data (precision / units).

        Tracks two separate gaps:
        - no_description : registers with no spec_description at all
        - no_conversion  : registers that have a description but are missing
                           spec_precision or spec_units (needed for the
                           Converted column to show physical values)
        """
        try:
            from discovery.spec_db import write_request, get_codesign_project
            if not platform_name:
                return
            no_description = [
                r['name'] for r in records
                if not r.get('spec_description')
            ]
            no_conversion = [
                r['name'] for r in records
                if r.get('spec_description')
                and not (r.get('spec_precision') and r.get('spec_units'))
            ]
            if not no_description and not no_conversion:
                return   # full coverage including conversion data — nothing to do
            self.progress.emit(
                "Spec coverage: {}/{} missing description, {}/{} missing "
                "conversion (precision/units) — writing request...".format(
                    len(no_description), len(records),
                    len(no_conversion), len(records))
            )
            self.progress_step.emit(0, 0)  # indeterminate
            write_request(platform_name, no_description, no_conversion)
            proj  = get_codesign_project(platform_name)
            query = _build_spec_query(
                platform_name, proj, no_description, no_conversion, len(records)
            )
            # Write the full (non-truncated) query to disk immediately so it
            # is available before the user clicks the "Update Spec DB" button.
            # The file gets ALL register names; the clipboard copy (query) is
            # truncated to 200 for display only.
            try:
                import pathlib as _pl
                _tool_root = _pl.Path(__file__).parent.parent.parent / 'utils'
                _txt = _tool_root / 'spec_query_for_copilot.txt'
                # Build full text: same header as query but with all names
                _lines = [query.split('\n### Section')[0].rstrip()]  # header block
                if no_description:
                    _lines.append(
                        f'\n### Section 1 \u2014 {len(no_description)} registers with NO spec entry\n'
                        f'Add a full entry for each (all fields below).'
                    )
                    _lines.extend(f'  - {n}' for n in no_description)
                if no_conversion:
                    _lines.append(
                        f'\n### Section 2 \u2014 {len(no_conversion)} registers MISSING `precision` and/or `units`\n'
                        f'Fill in `precision` and `units` for each register below.'
                    )
                    _lines.extend(f'  - {n}' for n in no_conversion)
                _lines.append(f'\nFull lists also saved to: {_txt}')
                _txt.write_text('\n'.join(_lines), encoding='utf-8')
            except Exception:
                pass
            self.spec_missing.emit(platform_name, query)
        except Exception:
            pass

    def _live_scan(self):
        try:
            from discovery.auto_discover_vf_registers import (
                detect_platform_name, load_platform_config,
                discover_fuse_paths,
                analyze_fuse_path, _all_results_to_flat_records,
                _save_discovery_cache, auto_learn_unknown_patterns,
            )
            import time as _time

            self.progress.emit("Detecting platform...")
            self.progress_step.emit(0, 0)   # indeterminate
            platform_name    = detect_platform_name()
            cfg              = load_platform_config(platform_name)
            platform_display = cfg.get("display_name", platform_name)

            self.progress.emit("Discovering fuse paths...")
            fuse_paths = discover_fuse_paths(cfg)
            if not fuse_paths:
                log.error("No fuse paths found.")
                return None, None, None, None

            n = len(fuse_paths)
            self.progress.emit("Scanning {} fuse path(s)...\n\nThis may take several minutes.".format(n))
            self.progress_step.emit(0, n)   # switch to determinate for path scan
            log.info("[DiscoveryWorker] scanning %d fuse paths", n)

            all_path_results = {}
            for i, path_str in enumerate(fuse_paths):
                label = path_str.split(".")[-1]
                pct   = int((i + 1) * 100 / n)
                self.progress.emit(
                    "Scanning path {}/{}: {}\n\n{} of {} paths complete  ({}%)".format(
                        i + 1, n, label, i + 1, n, pct)
                )
                self.progress_step.emit(i + 1, n)
                result = analyze_fuse_path(path_str, label, cfg)
                if result:
                    all_path_results[path_str] = result

            if not all_path_results:
                log.error("No accessible registers found.")
                return None, None, None, None

            # Switch back to indeterminate for post-processing phases
            self.progress_step.emit(0, 0)
            self.progress.emit("Auto-learning patterns...")
            auto_learn_unknown_patterns(all_path_results, platform_name, cfg)

            self.progress.emit("Building flat register list and saving cache...")
            records   = _all_results_to_flat_records(all_path_results, platform_name)
            timestamp = _time.strftime("%Y-%m-%d %H:%M:%S")
            _save_discovery_cache(records, platform_name, platform_display)

            log.info("[DiscoveryWorker] live scan complete -- %d registers", len(records))
            # Return platform_name so _do_work can run spec_missing check
            # AFTER this method returns (not here, so it fires post hw-refresh)
            return records, platform_name, platform_display, timestamp

        except Exception as exc:
            import traceback
            log.error("Live scan error: %s", exc)
            traceback.print_exc()
            return None, None, None, None

    def _hw_refresh(self, records, _pname, platform_display):
        try:
            from utils.hardware_access import load_fuse_ram, get_fuse_object
            from collections import defaultdict

            path_groups = defaultdict(list)
            for rec in records:
                fp = rec.get("fuse_path", "")
                if fp:
                    path_groups[fp].append(rec)

            path_list = list(path_groups.keys())
            n_paths   = len(path_list)
            self.progress.emit(
                "Reading live fuse values from hardware ({} path(s))..."
                "\n\nThis may take a moment per path.".format(n_paths)
            )
            self.progress_step.emit(0, n_paths)   # switch bar to determinate

            loaded_ram = set()
            for i, fp in enumerate(path_list):
                parts    = fp.split(".")
                label    = parts[-1] if parts else fp
                fuse_ram = ".".join(parts[:-1]) if len(parts) > 1 else fp
                pct      = int((i + 1) * 100 / n_paths) if n_paths else 100
                self.progress.emit(
                    "HW refresh {}/{}: {}\n\n{} of {} paths  ({}%)".format(
                        i + 1, n_paths, label, i + 1, n_paths, pct)
                )
                self.progress_step.emit(i + 1, n_paths)
                if fuse_ram not in loaded_ram:
                    stub = {"fuse_path": fp, "fuse_ram_path": fuse_ram, "label": label}
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
                    reg_name = rec.get("name", "")
                    if not reg_name:
                        continue
                    try:
                        live_val = getattr(fuse_obj, reg_name, None)
                        if live_val is not None:
                            rec["value"]  = live_val
                            rec["hex"]    = "0x{:x}".format(live_val)
                            rec["active"] = bool(live_val)
                            refreshed += 1
                    except Exception:
                        pass

            if refreshed > 0:
                try:
                    from discovery.auto_discover_vf_registers import _save_discovery_cache
                    _save_discovery_cache(records, _pname or "generic",
                                          platform_display or "Unknown Platform")
                except Exception as _ce:
                    log.warning("Could not persist refreshed values: %s", _ce)
                self.progress.emit("Hardware refresh complete — {} values updated.".format(refreshed))
                self.progress_step.emit(0, 0)   # back to indeterminate
                return "live -- {} registers".format(refreshed)

            self.progress.emit("Hardware refresh complete (no live reads).")
            self.progress_step.emit(0, 0)   # back to indeterminate
            return "cached (no live reads)"

        except Exception as ex:
            log.info("Hardware refresh skipped: %s", ex)
            return "cached"


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class DiscoveryMixin:
    """Handles live/cached register discovery and the Scalar Modifiers dialog."""

    def open_registers_tab(self):
        """Start background discovery and show a live progress dialog."""
        try:
            from discovery.auto_discover_vf_registers import load_discovery_cache  # noqa: F401
        except ImportError as exc:
            QMessageBox.warning(self, "Discovery Module",
                                "Discovery module not available:\n{}".format(exc))
            return

        # Use a dummy range (0, 1) in the constructor to avoid a Qt quirk
        # where (0, 0) causes the internal QProgressBar to render as "100%"
        # for one frame before the pulsing animation starts.
        dlg = QProgressDialog("Starting discovery...", "Cancel", 0, 1, self)
        dlg.setWindowTitle("Discovering Registers")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setAutoClose(False)    # keep dialog open when bar hits 100 %
        dlg.setAutoReset(False)    # don't reset bar back to 0 at maximum
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(500)
        dlg.show()
        dlg.setRange(0, 0)         # switch to pulsing AFTER show() so Qt paints it correctly

        worker = _DiscoveryWorker(parent=self)
        self._discovery_worker = worker  # keep reference

        def _on_progress(msg):
            if not dlg.wasCanceled():
                dlg.setLabelText(msg)

        def _on_progress_step(current, total):
            """Keep the dialog in indeterminate / pulsing mode throughout.

            Switching to a determinate bar causes it to fill to 100 % at the
            end of every path scan and then snap back, which looks broken.
            The label text already carries percentage info so the user still
            sees meaningful progress without the bar ever hitting 100 %.
            """
            if dlg.wasCanceled():
                return
            # Always pulsing — setRange(0,0) is the Qt5 indeterminate mode
            dlg.setRange(0, 0)

        def _on_error(msg):
            dlg.close()
            QMessageBox.critical(self, "Discovery Error",
                                 "Discovery failed:\n{}".format(msg))

        _spec_missing_info = [None]   # [0] = (platform, query) or None

        def _on_spec_missing(platform, query):
            _spec_missing_info[0] = (platform, query)

        def _on_finished(records, platform_display, timestamp, hw_status):
            if records is None:
                dlg.close()
                QMessageBox.information(
                    self, "Discovery Required",
                    "No cached data and live hardware scan failed.\n\n"
                    "Check that ITP is connected and fuse paths are accessible,\n"
                    "then click this button again.\n\n"
                    "You can also run:  python auto_discover_vf_registers.py",
                )
                return
            # Keep dialog open with a pulsing bar while the table is built.
            dlg.setRange(0, 0)   # indeterminate — setRange(0,0) is the Qt5 way
            dlg.setLabelText(
                "Loading {} registers into Discovered Registers tab\u2026\n\n"
                "Almost done — building table, please wait.".format(len(records))
            )
            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()   # let the label repaint before blocking
            self._populate_registers_tab(
                records, platform_display, timestamp, hw_status,
                spec_missing_info=_spec_missing_info[0],
            )
            dlg.close()   # close AFTER tab is fully populated

        worker.progress.connect(_on_progress)
        worker.progress_step.connect(_on_progress_step)
        worker.error.connect(_on_error)
        worker.spec_missing.connect(_on_spec_missing)
        worker.finished.connect(_on_finished)

        def _cancel():
            if worker.isRunning():
                worker.terminate()
                worker.wait(2000)
        dlg.canceled.connect(_cancel)

        worker.start()

    def _populate_registers_tab(self, records, platform_display, timestamp, hw_status,
                                spec_missing_info=None):
        for idx in range(self.output_tabs.count()):
            if self.output_tabs.tabText(idx).startswith("\U0001f50d"):
                self.output_tabs.removeTab(idx)
                break
        # also check with actual unicode
        for idx in range(self.output_tabs.count()):
            if self.output_tabs.tabText(idx).startswith("🔍"):
                self.output_tabs.removeTab(idx)
                break

        tab_widget = self._build_registers_tab_widget(
            records,
            platform_display or "Unknown",
            timestamp,
            hw_status or "cached",
            spec_missing_info=spec_missing_info,
        )
        badge = "live" if "live" in (hw_status or "").lower() else "cached"
        tab_idx = self.output_tabs.addTab(
            tab_widget, "🔍 Discovered Registers [{}]".format(badge))
        self.output_tabs.setCurrentIndex(tab_idx)

    def _build_registers_tab_widget(self, records, platform_display, timestamp,
                                    hw_status="cached", spec_missing_info=None):
        from ui.tabs.registers_tab import build_registers_tab_widget as _build
        return _build(records, platform_display, timestamp, hw_status,
                      spec_missing_info=spec_missing_info)

    def open_scalar_modifiers_dialog(self):
        if not hasattr(self, "curve_engine") or self.curve_engine is None:
            QMessageBox.warning(self, "Not Initialised",
                                "VF Curve Manager is not initialised yet.\n"
                                "Please connect to hardware first.")
            return
        from ui.dialogs.scalar_modifiers import ScalarModifiersDialog
        dlg = ScalarModifiersDialog(self.curve_engine, parent=self)
        dlg.exec_()
