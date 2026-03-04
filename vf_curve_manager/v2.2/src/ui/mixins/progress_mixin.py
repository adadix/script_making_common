"""ProgressMixin — progress dialogs, inline worker QThreads, and tab delegates."""
from __future__ import annotations

import time

from PyQt5.QtWidgets import QApplication, QMessageBox, QProgressDialog
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal

import logging
log = logging.getLogger(__name__)


class ProgressMixin:
    """Provides reusable progress-dialog infrastructure and the inline
    FlattenWorker / WPEditWorker QThread subclasses."""

    # ── Tab delegates (forwarded to ui/tabs/result_tabs.py) ──────────────────

    def _create_result_tab(self, df, excel_path, png_path):
        """Create result tab. Implementation delegated to ui/tabs/result_tabs.py."""
        from ui.tabs.result_tabs import _create_result_tab as _impl
        return _impl(df, excel_path, png_path)

    def _create_cumulative_tab(self, excel_path, png_path):
        """Create cumulative tab. Implementation delegated to ui/tabs/result_tabs.py."""
        from ui.tabs.result_tabs import _create_cumulative_tab as _impl
        return _impl(excel_path, png_path)

    def _create_bump_result_tab(self, df_before, df_after, excel_path, png_path, verification):
        """Create bump result tab. Implementation delegated to ui/tabs/result_tabs.py."""
        from ui.tabs.result_tabs import _create_bump_result_tab as _impl
        return _impl(df_before, df_after, excel_path, png_path, verification)

    # ── Progress-dialog cleanup ───────────────────────────────────────────────

    def _cleanup_progress_dialog(self, progress):
        """Close a QProgressDialog and force a UI repaint to prevent artefacts."""
        progress.close()
        QApplication.processEvents()
        self.repaint()
        QApplication.processEvents()

    # ── Generic run-with-progress helper ─────────────────────────────────────

    def _run_with_progress(self, worker, title, message, max_seconds,
                           on_finished, on_error_title='Operation Failed'):
        """Create a QProgressDialog, run *worker* in the background, and handle
        the common timeout / SUT-warning / cleanup logic.

        Args:
            worker:          A QThread subclass with ``finished`` and ``error``
                             pyqtSignal attributes, not yet started.
            title:           QProgressDialog window title.
            message:         Progress label text shown in the dialog.
            max_seconds:     Seconds before the timeout warning fires.
            on_finished:     Callable connected to ``worker.finished``; receives
                             whatever args the signal carries.
            on_error_title:  Title for the QMessageBox.critical shown on error.
        """
        from utils import hardware_access

        progress = QProgressDialog(message, None, 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(400)
        progress.setCancelButton(None)

        state = {'finished': False, 'start_time': time.time()}

        def _on_finished(*args):
            state['finished'] = True
            self._cleanup_progress_dialog(progress)
            on_finished(*args)

        def _on_error(error_msg):
            state['finished'] = True
            self._cleanup_progress_dialog(progress)
            QMessageBox.critical(
                self, on_error_title,
                f"Operation failed:\n\n{error_msg}")
            self._set_status("Status: Ready")

        def _check_timeout():
            if state['finished']:
                return
            elapsed = int(time.time() - state['start_time'])
            if elapsed >= max_seconds:
                state['finished'] = True
                self._cleanup_progress_dialog(progress)
                if hardware_access.ENABLE_SUT_VERIFICATION:
                    QMessageBox.warning(
                        self, 'SUT Boot Timeout',
                        f"SUT did not boot within {max_seconds} seconds.\n\n"
                        "The SUT may be stuck. Please check:\n"
                        "- SUT power state\n- ITP connection\n- Network connectivity\n\n"
                        "You may need to manually power cycle the SUT.",
                    )
                else:
                    QMessageBox.warning(
                        self, 'Operation Timeout',
                        f"Operation exceeded timeout of {max_seconds} seconds.",
                    )
                self._set_status("Status: Timeout - Check SUT", 'error')
            else:
                QTimer.singleShot(1000, _check_timeout)

        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.start()
        QTimer.singleShot(1000, _check_timeout)
        progress.show()

    # ── Operation launchers ───────────────────────────────────────────────────

    def _show_progress_dialog_for_bump(self, domains, bump_mv, direction, max_seconds):
        """Run bump operation with progress dialog and timeout handling."""
        from ui.workers import BumpWorkerThread
        self.bump_worker = BumpWorkerThread(
            self.curve_engine, domains, bump_mv, direction)
        self._run_with_progress(
            self.bump_worker,
            title='Bump Voltage Operation',
            message='Processing bump operation...\n\nPlease wait while the system resets and boots.',
            max_seconds=max_seconds,
            on_finished=lambda results: self._after_bump(results, domains, direction),
            on_error_title='Bump Failed',
        )

    def _show_progress_dialog_for_flatten(self, domain_name, flatten_val, label,
                                          domain_info, max_seconds):
        """Run flatten operation with progress dialog and timeout handling."""

        class FlattenWorker(QThread):
            finished = pyqtSignal(dict, str, dict)
            error    = pyqtSignal(str)

            def __init__(self, curve_engine, domain_name, flatten_val, label, domain_info):
                super().__init__()
                self.curve_engine = curve_engine
                self.domain_name  = domain_name
                self.flatten_val  = flatten_val
                self.label        = label
                self.domain_info  = domain_info

            def run(self):
                try:
                    result_data = self.curve_engine.flatten_frequency(
                        self.domain_name, self.flatten_val)
                    if 'error' in result_data:
                        self.error.emit(result_data['error'])
                    else:
                        result_data['flatten_val'] = self.flatten_val
                        self.finished.emit(result_data, self.label, self.domain_info)
                except Exception as ex:
                    self.error.emit(f"Flatten operation failed: {ex}")

        self.flatten_worker = FlattenWorker(
            self.curve_engine, domain_name, flatten_val, label, domain_info)
        self._run_with_progress(
            self.flatten_worker,
            title='Flatten Frequency Operation',
            message='Processing flatten operation...\n\nPlease wait while the system resets and boots.',
            max_seconds=max_seconds,
            on_finished=self._after_flatten,
            on_error_title='Flatten Failed',
        )

    def _show_progress_dialog_for_wp_edit(self, domain_name, voltage_changes, label,
                                          max_seconds, freq_changes=None):
        """Run WP edit operation with progress dialog and timeout handling."""
        freq_changes = freq_changes or {}

        class WPEditWorker(QThread):
            finished = pyqtSignal(dict)
            error    = pyqtSignal(str)

            def __init__(self, curve_engine, domain_name, voltage_changes, freq_changes):
                super().__init__()
                self.curve_engine   = curve_engine
                self.domain_name    = domain_name
                self.voltage_changes = voltage_changes
                self.freq_changes    = freq_changes

            def run(self):
                try:
                    import traceback as _tb
                    result_data = self.curve_engine.edit_voltages(
                        self.domain_name, self.voltage_changes,
                        freq_changes=self.freq_changes)
                    if 'error' in result_data:
                        self.error.emit(result_data['error'])
                    else:
                        self.finished.emit(result_data)
                except Exception as ex:
                    import traceback as _tb
                    _tb.print_exc()
                    self.error.emit(f"WP edit operation failed: {ex}")

        self.wp_edit_worker = WPEditWorker(
            self.curve_engine, domain_name, voltage_changes, freq_changes)
        self._run_with_progress(
            self.wp_edit_worker,
            title='WP Edit Operation',
            message='Processing WP edit operation...\n\nPlease wait while the system resets and boots.',
            max_seconds=max_seconds,
            on_finished=lambda result_data: self._after_wp_edit(result_data, label),
            on_error_title='WP Edit Failed',
        )

    def _show_progress_dialog(self, max_seconds, on_finish):
        """Show a simple progress dialog during target reset (legacy helper)."""
        progress = QProgressDialog(
            'Resetting target system...\n\nPlease wait while the system resets and boots.',
            None, 0, 0, self)
        progress.setWindowTitle('Target Reset')
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(400)
        progress.setCancelButton(None)

        state = {'finished': False}

        def check_if_done():
            if state['finished']:
                return
            if not progress.isVisible():
                state['finished'] = True
                self._cleanup_progress_dialog(progress)
                on_finish()
            else:
                QTimer.singleShot(500, check_if_done)

        QTimer.singleShot(500, check_if_done)
        progress.show()
