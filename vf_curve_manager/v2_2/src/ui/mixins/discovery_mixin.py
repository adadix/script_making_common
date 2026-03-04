"""DiscoveryMixin — discovered-registers tab and scalar-modifiers dialog."""
from __future__ import annotations

import logging
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import QMessageBox, QProgressDialog

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _DiscoveryWorker(QThread):
    """Run discovery in a background thread so the Qt event loop never blocks."""

    finished = pyqtSignal(object, object, object, object)
    progress = pyqtSignal(str)
    error    = pyqtSignal(str)

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
        records, _pname, platform_display, timestamp = load_discovery_cache()

        if not records:
            self.progress.emit("No cache -- detecting platform...")
            records, platform_display, timestamp = self._live_scan()
            _pname = None
            if not records:
                return None, None, None, None
            return records, platform_display, timestamp, "live -- just discovered"

        self.progress.emit(
            "Cache loaded ({} registers) -- refreshing from hardware...".format(len(records))
        )
        hw_status = self._hw_refresh(records, _pname, platform_display)
        return records, platform_display or _pname or "Unknown", timestamp, hw_status

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
            platform_name    = detect_platform_name()
            cfg              = load_platform_config(platform_name)
            platform_display = cfg.get("display_name", platform_name)

            self.progress.emit("Discovering fuse paths...")
            fuse_paths = discover_fuse_paths(cfg)
            if not fuse_paths:
                log.error("No fuse paths found.")
                return None, None, None

            n = len(fuse_paths)
            self.progress.emit("Scanning {} fuse path(s)...\n(this takes several minutes)".format(n))
            log.info("[DiscoveryWorker] scanning %d fuse paths", n)

            all_path_results = {}
            for i, path_str in enumerate(fuse_paths):
                label = path_str.split(".")[-1]
                self.progress.emit("Scanning path {}/{}:\n{}".format(i + 1, n, label))
                result = analyze_fuse_path(path_str, label, cfg)
                if result:
                    all_path_results[path_str] = result

            if not all_path_results:
                log.error("No accessible registers found.")
                return None, None, None

            self.progress.emit("Auto-learning patterns...")
            auto_learn_unknown_patterns(all_path_results, platform_name, cfg)

            self.progress.emit("Saving cache...")
            records   = _all_results_to_flat_records(all_path_results)
            timestamp = _time.strftime("%Y-%m-%d %H:%M:%S")
            _save_discovery_cache(records, platform_name, platform_display)

            log.info("[DiscoveryWorker] live scan complete -- %d registers", len(records))
            return records, platform_display, timestamp

        except Exception as exc:
            import traceback
            log.error("Live scan error: %s", exc)
            traceback.print_exc()
            return None, None, None

    def _hw_refresh(self, records, _pname, platform_display):
        try:
            from utils.hardware_access import load_fuse_ram, get_fuse_object
            from collections import defaultdict

            path_groups = defaultdict(list)
            for rec in records:
                fp = rec.get("fuse_path", "")
                if fp:
                    path_groups[fp].append(rec)

            loaded_ram = set()
            for fp in path_groups:
                parts    = fp.split(".")
                fuse_ram = ".".join(parts[:-1]) if len(parts) > 1 else fp
                if fuse_ram not in loaded_ram:
                    stub = {"fuse_path": fp, "fuse_ram_path": fuse_ram, "label": parts[-1]}
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
                return "live -- {} registers".format(refreshed)

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

        dlg = QProgressDialog("Starting discovery...", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Discovering Registers")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(420)
        dlg.setValue(0)
        dlg.show()

        worker = _DiscoveryWorker(parent=self)
        self._discovery_worker = worker  # keep reference

        def _on_progress(msg):
            if not dlg.wasCanceled():
                dlg.setLabelText(msg)

        def _on_error(msg):
            dlg.close()
            QMessageBox.critical(self, "Discovery Error",
                                 "Discovery failed:\n{}".format(msg))

        def _on_finished(records, platform_display, timestamp, hw_status):
            dlg.close()
            if records is None:
                QMessageBox.information(
                    self, "Discovery Required",
                    "No cached data and live hardware scan failed.\n\n"
                    "Check that ITP is connected and fuse paths are accessible,\n"
                    "then click this button again.\n\n"
                    "You can also run:  python auto_discover_vf_registers.py",
                )
                return
            self._populate_registers_tab(records, platform_display, timestamp, hw_status)

        worker.progress.connect(_on_progress)
        worker.error.connect(_on_error)
        worker.finished.connect(_on_finished)

        def _cancel():
            if worker.isRunning():
                worker.terminate()
                worker.wait(2000)
        dlg.canceled.connect(_cancel)

        worker.start()

    def _populate_registers_tab(self, records, platform_display, timestamp, hw_status):
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
        )
        badge = "live" if "live" in (hw_status or "").lower() else "cached"
        tab_idx = self.output_tabs.addTab(
            tab_widget, "🔍 Discovered Registers [{}]".format(badge))
        self.output_tabs.setCurrentIndex(tab_idx)

    def _build_registers_tab_widget(self, records, platform_display, timestamp,
                                    hw_status="cached"):
        from ui.tabs.registers_tab import build_registers_tab_widget as _build
        return _build(records, platform_display, timestamp, hw_status)

    def open_scalar_modifiers_dialog(self):
        if not hasattr(self, "curve_engine") or self.curve_engine is None:
            QMessageBox.warning(self, "Not Initialised",
                                "VF Curve Manager is not initialised yet.\n"
                                "Please connect to hardware first.")
            return
        from ui.dialogs.scalar_modifiers import ScalarModifiersDialog
        dlg = ScalarModifiersDialog(self.curve_engine, parent=self)
        dlg.exec_()
