"""OperationsMixin — VF hardware operations: show, bump, WP-edit, flatten, customize."""
from __future__ import annotations

import traceback

from PyQt5.QtWidgets import (
    QApplication, QMessageBox, QDialog, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QSpinBox, QProgressDialog,
)
from PyQt5.QtCore import QTimer, Qt

import logging
log = logging.getLogger(__name__)


class OperationsMixin:
    """Hardware operations: show VF curves, bump voltages, WP edit,
    flatten frequency, and customize frequency."""

    # ── SUT-verification checkbox callback ───────────────────────────────────

    def on_sut_verification_changed(self, state):
        """Handle SUT verification checkbox state change."""
        from utils import hardware_access
        is_enabled = (state == Qt.Checked)
        hardware_access.ENABLE_SUT_VERIFICATION = is_enabled

        if is_enabled:
            self._set_status("Status: SUT Verification Enabled", 'busy')
            QMessageBox.information(
                self,
                'SUT Verification Enabled',
                '⚠️ IMPORTANT: Enable this ONLY if NGA Network is enabled on the setup!\n\n'
                'ITP Recovery is now ENABLED:\n\n'
                '✓ Automatic SUT reachability checking (ping)\n'
                '✓ ITP recovery from power states (SLP_S5, sleep, reset)\n'
                '✓ Boot verification after resets\n'
                '✓ Automatic retry after recovery\n\n'
                'Note: This may add 5-15 seconds overhead on power state errors.\n\n'
                '⚠️ Without NGA enabled, SUT verification will fail!',
            )
        else:
            self._set_status("Status: Fast Mode (SUT Verification Disabled)")

        QTimer.singleShot(2000, lambda: self._set_status("Status: Ready"))

    # ── Show VF Curve ────────────────────────────────────────────────────────

    def show_vf_curve(self):
        """Show VF curves for selected domains."""
        domains = self.get_selected_domains()
        if not domains:
            QMessageBox.warning(self, 'No Domain Selected',
                                'Please select at least one domain.')
            return

        self._set_status("Status: Reading VF Curves...", 'busy')
        QApplication.processEvents()

        interp_enabled = self.interp_checkbox.isChecked()
        results = self.curve_engine.show_vf_curves(domains, interp_enabled)

        self.output_tabs.clear()

        for domain_name in domains:
            if domain_name in results['dataframes']:
                df         = results['dataframes'][domain_name]
                excel_path = results['excel_paths'][domain_name]
                png_path   = results['png_paths'][domain_name]
                tab        = self._create_result_tab(df, excel_path, png_path)
                label      = self.config_loader.get_domain(domain_name).get(
                    'label', domain_name.upper())
                self.output_tabs.addTab(tab, label)

        if len(domains) > 1 and 'cumulative_excel' in results:
            cum_tab = self._create_cumulative_tab(
                results['cumulative_excel'], results['cumulative_png'])
            self.output_tabs.addTab(cum_tab, 'Cumulative')

        self._set_status("Status: Ready")
        QMessageBox.information(self, 'VF Curve Displayed',
                                'VF curves displayed successfully.\n\nData exported to Logs directory.')

    # ── Bump ─────────────────────────────────────────────────────────────────

    def bump_domains(self, direction):
        """Bump voltages for selected domains."""
        from utils import hardware_access
        domains = self.get_selected_domains()
        if not domains:
            QMessageBox.warning(self, 'No Domain Selected',
                                'Please select at least one domain.')
            return

        try:
            val = int(self.bump_val.text())
        except ValueError:
            QMessageBox.warning(self, 'Invalid Input',
                                'Enter a valid bump value (mV).')
            return

        msg = (f"Are you sure you want to bump {direction} by {val} mV for:\n"
               + ', '.join(d.upper() for d in domains)
               + "\n\nThis will modify hardware fuses and reset the target!")
        reply = QMessageBox.question(self, 'Confirm Bump Operation', msg,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self._set_status(f"Status: Bumping {direction}...", 'busy')
        QApplication.processEvents()

        max_timeout = 300 if hardware_access.ENABLE_SUT_VERIFICATION else 20
        self._show_progress_dialog_for_bump(domains, val, direction, max_timeout)

    def _after_bump(self, results, domains, direction):
        """Handle bump completion."""
        if results.get('error') == 'COLD_RESET':
            self._handle_cold_reset_error(results, 'Bump Voltage')
            return

        self.output_tabs.clear()

        for domain_name in domains:
            if domain_name in results['before_dataframes']:
                df_before    = results['before_dataframes'][domain_name]
                df_after     = results['after_dataframes'][domain_name]
                excel_path   = results['excel_paths'][domain_name]
                png_path     = results['png_paths'][domain_name]
                verification = results['verification'][domain_name]
                tab          = self._create_bump_result_tab(
                    df_before, df_after, excel_path, png_path, verification)
                label        = self.config_loader.get_domain(domain_name).get(
                    'label', domain_name.upper())
                self.output_tabs.addTab(tab, label)

        self._set_status("Status: Ready")

        all_success = all(results['verification'][d]['success'] for d in domains)
        if all_success:
            QMessageBox.information(
                self, 'Bump Successful',
                f'Voltage bump {direction} completed successfully!\n\n'
                'All voltages verified within tolerance.')
        else:
            QMessageBox.warning(
                self, 'Bump Verification',
                f'Voltage bump {direction} completed but some values outside tolerance.\n\n'
                'Check the output tabs for details.')

    # ── Cold-reset error dialog ───────────────────────────────────────────────

    def _handle_cold_reset_error(self, results, operation_name):
        """Show cold-reset detection dialog with guidance."""
        self._set_status("Status: Cold Reset Detected!", 'error')

        revert_verified = results.get('auto_revert_verified', False)
        revert_details  = results.get('revert_details', '')

        if revert_verified:
            icon    = QMessageBox.Warning
            title   = f'{operation_name} — Cold Reset Detected'
            message = (
                f"⚠️ COLD RESET DETECTED — SUT POWERED OFF COMPLETELY\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "What Happened:\n"
                "The voltage/frequency change you applied exceeded hardware stability limits, "
                "causing the system to power off completely (cold reset) instead of performing "
                "a normal warm reset. This is a hardware protection mechanism.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✅ AUTOMATIC HARDWARE PROTECTION:\n\n"
                "When the cold reset occurred, the hardware fuses automatically reverted to their "
                "originally programmed values. This is built-in protection — no data was lost or "
                "corrupted, and the system is now in a safe state.\n\n"
                f"{revert_details}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ RECOMMENDED NEXT STEPS:\n\n"
                "  • The settings you tried are too aggressive for this hardware\n"
                "  • Try smaller voltage increments (5-10mV instead of 20mV+)\n"
                "  • Test at lower working points (WP) first\n"
                "  • Review hardware specifications for safe operating ranges\n"
                "  • You've successfully identified the voltage/frequency stability boundary\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Technical Details:\n"
                "Cold reset indicator detected during boot monitoring. System protected itself "
                "by triggering automatic power-off and fuse restoration. No manual intervention required."
            )
        else:
            icon    = QMessageBox.Warning
            title   = f'{operation_name} — Cold Reset Detected (Verification Failed)'
            message = (
                f"⚠️ COLD RESET DETECTED — SUT POWERED OFF COMPLETELY\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "What Happened:\n"
                "The voltage/frequency change you applied exceeded hardware stability limits, "
                "causing the system to power off completely (cold reset) instead of performing "
                "a normal warm reset. This is a hardware protection mechanism.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ AUTOMATIC HARDWARE PROTECTION:\n\n"
                "When the cold reset occurred, the hardware should have automatically reverted "
                "fuses to their originally programmed values. However, we couldn't verify the "
                "current state due to ITP communication issues after the reset.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ REQUIRED ACTIONS:\n\n"
                "  1. Verify the SUT is powered on and stable\n"
                "  2. Check ITP connection status\n"
                "  3. Use 'Show VF Curve' to read current fuse values\n"
                "  4. Verify values match expected original/programmed defaults\n"
                "  5. If values appear incorrect, manually restore them\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Technical Details:\n"
                "Cold reset detected but post-reset verification could not complete. "
                "The hardware protection mechanism should have restored fuse values automatically. "
                "Manual verification recommended due to communication timeout."
            )

        msg_box = QMessageBox(icon, title, message, QMessageBox.Ok, self)
        msg_box.setTextFormat(Qt.PlainText)
        msg_box.exec_()
        QTimer.singleShot(1000, lambda: self._set_status("Status: Ready"))

    # ── WP Edit ───────────────────────────────────────────────────────────────

    def wp_edit(self):
        """Open WP Edit dialog with editable table."""
        from utils import hardware_access
        domains = self.get_selected_domains()
        if not domains or len(domains) != 1:
            QMessageBox.warning(self, 'WP Edit',
                                'Please select exactly ONE domain for WP editing.')
            return

        domain_name = domains[0]
        domain_info = self.config_loader.get_domain(domain_name)
        label       = domain_info.get('label', domain_name.upper())
        wp_count    = domain_info['wp_count']

        try:
            from utils.hardware_access import load_fuse_ram, read_voltage_frequency
            load_fuse_ram(domain_info)
            current_wps = []
            for i in range(wp_count):
                voltage_v, freq_mhz = read_voltage_frequency(domain_info, i)
                current_wps.append((voltage_v, freq_mhz))
        except Exception as ex:
            QMessageBox.critical(self, 'WP Edit',
                                 f'Error reading current VF values: {ex}')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f'WP Edit - {label}')
        dlg.resize(840, 500)
        layout = QVBoxLayout(dlg)

        info_label = QLabel(
            f'<b>Edit Working Points for {label}</b><br>'
            '<b style="color: #d9534f;">Voltage: enter in millivolts (mV) — e.g. 1203 for 1.203 V</b><br>'
            '<b style="color: #0071c5;">Frequency: enter in MHz — e.g. 3600 for 3600 MHz. Leave blank to keep current.</b><br>'
            'Unchanged WPs can be left as-is. Click "Apply Changes" to commit to hardware.'
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        table = QTableWidget(wp_count, 6)
        table.setHorizontalHeaderLabels([
            'WP',
            'Current\nVoltage (V)', 'Current\nVoltage (mV)', '⚠ New Voltage\n(mV ONLY)',
            'Current\nFreq (MHz)',  '✏ New Freq\n(MHz)',
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setStyleSheet("""
            QTableWidget { font-size: 11px; }
            QHeaderView::section {
                background-color: #0071c5; color: white; font-weight: bold;
                padding: 5px; border: 1px solid #005a9e;
            }
        """)

        for i in range(wp_count):
            voltage_v, freq_mhz = current_wps[i]

            wp_item = QTableWidgetItem(f'P{i}')
            wp_item.setFlags(wp_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 0, wp_item)

            v_item = QTableWidgetItem(
                f'{voltage_v:.4f}' if voltage_v is not None else 'N/A')
            v_item.setFlags(v_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 1, v_item)

            mv_val  = int(round(voltage_v * 1000)) if voltage_v is not None else 0
            mv_item = QTableWidgetItem(str(mv_val))
            mv_item.setFlags(mv_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 2, mv_item)

            new_mv_item = QTableWidgetItem(str(mv_val))
            table.setItem(i, 3, new_mv_item)

            freq_text  = f'{freq_mhz:.0f}' if freq_mhz is not None else 'N/A'
            freq_item  = QTableWidgetItem(freq_text)
            freq_item.setFlags(freq_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 4, freq_item)

            new_freq_item = QTableWidgetItem(
                freq_text if freq_mhz is not None else '')
            if freq_mhz is None:
                new_freq_item.setFlags(new_freq_item.flags() & ~Qt.ItemIsEditable)
                from PyQt5.QtGui import QColor
                new_freq_item.setBackground(QColor('#3a3a3a'))
            table.setItem(i, 5, new_freq_item)

        layout.addWidget(table)

        btns      = QDialogButtonBox()
        btn_apply = QPushButton('Apply Changes')
        btn_apply.setStyleSheet(self._BTN_DIALOG_APPLY)
        btn_cancel = QPushButton('Cancel')
        btn_cancel.setStyleSheet(self._BTN_DIALOG_CANCEL)
        btns.addButton(btn_apply,  QDialogButtonBox.AcceptRole)
        btns.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        btn_apply.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        layout.addWidget(btns)

        if not dlg.exec_():
            return

        # ── Parse voltage changes ─────────────────────────────────────────
        voltage_changes = {}
        for i in range(wp_count):
            current_mv = int(table.item(i, 2).text())
            try:
                new_value_text = table.item(i, 3).text().strip()
                if '.' in new_value_text:
                    QMessageBox.critical(
                        self, 'Invalid Input Format',
                        f'WP{i}: Detected decimal point in input "{new_value_text}".\n\n'
                        'You must enter voltage in MILLIVOLTS (mV), not Volts!\n\n'
                        f'Example: For 1.203V, enter 1203 (not 1.203)')
                    return
                new_mv = int(new_value_text)
                if new_mv < 300 or new_mv > 2000:
                    reply = QMessageBox.question(
                        self, 'Voltage Out of Range',
                        f'WP{i}: Voltage {new_mv} mV ({new_mv/1000:.3f}V) is outside typical range (300-2000 mV).\n\n'
                        'Did you accidentally enter Volts instead of millivolts?\n\n'
                        f'  • If you meant 1.203V, enter 1203\n'
                        f'  • If you really want {new_mv} mV, click Yes to continue\n\n'
                        'Continue anyway?',
                        QMessageBox.Yes | QMessageBox.No)
                    if reply != QMessageBox.Yes:
                        return
                if new_mv != current_mv:
                    voltage_changes[i] = new_mv
            except ValueError:
                QMessageBox.warning(self, 'Invalid Input',
                                    f'Invalid voltage value for WP{i}. Must be an integer (in mV).')
                return

        # ── Parse frequency changes ───────────────────────────────────────
        freq_changes = {}
        for i in range(wp_count):
            freq_cell = table.item(i, 5)
            if freq_cell is None:
                continue
            if not (freq_cell.flags() & Qt.ItemIsEditable):
                continue
            new_freq_text = freq_cell.text().strip()
            if not new_freq_text:
                continue
            cur_freq_text = table.item(i, 4).text().strip()
            try:
                new_freq_mhz = float(new_freq_text)
                if new_freq_mhz <= 0:
                    raise ValueError('non-positive')
                cur_freq_mhz = (float(cur_freq_text)
                                if cur_freq_text not in ('', 'N/A') else None)
                if cur_freq_mhz is None or abs(new_freq_mhz - cur_freq_mhz) >= 1:
                    freq_changes[i] = new_freq_mhz
            except ValueError:
                QMessageBox.warning(
                    self, 'Invalid Input',
                    f'Invalid frequency value for WP{i}: "{new_freq_text}". Must be a number in MHz.')
                return

        if not voltage_changes and not freq_changes:
            QMessageBox.information(self, 'No Changes',
                                    'No voltage or frequency changes detected.')
            return

        lines = []
        if voltage_changes:
            lines.append('Voltage changes:')
            lines += [f'  WP{wp}: {mv} mV'
                      for wp, mv in sorted(voltage_changes.items())]
        if freq_changes:
            lines.append('Frequency changes:')
            lines += [f'  WP{wp}: {mhz:g} MHz'
                      for wp, mhz in sorted(freq_changes.items())]
        msg   = (f"Apply the following changes to {label}?\n\n"
                 + '\n'.join(lines)
                 + "\n\nThis will modify hardware fuses and reset the target!")
        reply = QMessageBox.question(self, 'Confirm WP Edit', msg,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self._set_status(f"Status: Editing WP voltage/frequency for {label}...", 'busy')
        QApplication.processEvents()

        max_timeout = 300 if hardware_access.ENABLE_SUT_VERIFICATION else 20
        self._show_progress_dialog_for_wp_edit(
            domain_name, voltage_changes, label, max_timeout,
            freq_changes=freq_changes)

    # ── Flatten frequency ─────────────────────────────────────────────────────

    def flatten_freq(self):
        """Open Flatten Frequency dialog."""
        from utils import hardware_access
        from utils.hardware_access import read_frequency_ratios

        domains = self.get_selected_domains()
        if not domains or len(domains) != 1:
            QMessageBox.warning(self, 'Flatten Frequency',
                                'Please select exactly ONE domain to flatten.')
            return

        domain_name = domains[0]
        if not self.config_loader.has_flatten_support(domain_name):
            QMessageBox.warning(
                self, 'Flatten Frequency',
                f'Domain {domain_name.upper()} does not support frequency flattening.')
            return

        domain_info = self.config_loader.get_domain(domain_name)
        freq_mult   = domain_info.get('freq_multiplier', 100)
        label       = domain_info.get('label', domain_name.upper())

        try:
            from utils.hardware_access import load_fuse_ram
            load_fuse_ram(domain_info)
            ratios = read_frequency_ratios(domain_info)
        except Exception as ex:
            QMessageBox.critical(self, 'Flatten Frequency',
                                 f'Error reading frequency ratios: {ex}')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f'Flatten Frequency - {label}')
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(
            f'<b>Current Frequency Ratios ({freq_mult} MHz units):</b>'))
        ratio_text = ''.join(
            f'{key.upper()}: {val} ({val * freq_mult} MHz)\n'
            for key, val in ratios.items())
        layout.addWidget(QLabel(ratio_text))
        layout.addWidget(QLabel(
            '<b>Select target ratio to flatten all frequencies:</b>'))

        btns   = QDialogButtonBox()
        result = {'flatten_to': None}

        def set_flatten(val):
            result['flatten_to'] = val
            dlg.accept()

        for key, val in ratios.items():
            btn = QPushButton(f'Flatten to {key.upper()} ({val * freq_mult} MHz)')
            btn.clicked.connect(lambda checked, v=val: set_flatten(v))
            btns.addButton(btn, QDialogButtonBox.ActionRole)

        btn_cancel = QPushButton('Cancel')
        btn_cancel.clicked.connect(dlg.reject)
        btns.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        layout.addWidget(btns)

        if not dlg.exec_() or result['flatten_to'] is None:
            return

        flatten_val = result['flatten_to']
        try:
            self._set_status(f"Status: Flattening frequency for {label}...", 'busy')
            QApplication.processEvents()
            max_timeout = 300 if hardware_access.ENABLE_SUT_VERIFICATION else 20
            self._show_progress_dialog_for_flatten(
                domain_name, flatten_val, label, domain_info, max_timeout)
        except Exception as ex:
            QMessageBox.critical(self, 'Flatten Frequency',
                                 f'Error during flatten operation: {ex}')
            traceback.print_exc()

    def _after_flatten(self, result_data, label, domain_info):
        """Handle flatten completion."""
        if 'error' in result_data:
            if result_data.get('error') == 'COLD_RESET':
                self._handle_cold_reset_error(result_data, 'Flatten Frequency')
                return
            QMessageBox.critical(self, 'Flatten Frequency',
                                 f'Error: {result_data["error"]}')
            self._set_status("Status: Ready")
            return

        df         = result_data['dataframe']
        excel_path = result_data['excel_path']
        freq_mult  = domain_info.get('freq_multiplier', 100)
        flatten_val = result_data.get('flatten_val', 0)

        tab = self._create_result_tab(df, excel_path, None)
        if self.output_tabs.count() >= 10:
            self.output_tabs.removeTab(1)
        self.output_tabs.addTab(tab, f'Flatten: {label}')
        self.output_tabs.setCurrentWidget(tab)

        self._set_status("Status: Ready")
        QMessageBox.information(
            self, 'Flatten Frequency',
            f'Successfully flattened {label} frequencies to {flatten_val * freq_mult} MHz!\n\n'
            f'Results exported to:\n{excel_path}\n\nTarget has been reset.')

    # ── Customize frequency ───────────────────────────────────────────────────

    def customize_freq(self):
        """Open Customize Frequency dialog."""
        from ui.workers import CustomizeWorkerThread
        selected_domains = self.get_selected_domains()
        if len(selected_domains) != 1:
            QMessageBox.warning(self, 'Customize Frequency',
                                'Please select exactly ONE domain to customize.')
            return

        domain_name = selected_domains[0]
        if not self.config_loader.has_flatten_support(domain_name):
            QMessageBox.warning(
                self, 'Customize Frequency',
                f'Domain {domain_name.upper()} does not support frequency customization.')
            return

        domain_info = self.config_loader.get_domain(domain_name)
        label       = domain_info.get('label', domain_name.upper())

        from utils.hardware_access import load_fuse_ram, read_frequency_ratios
        try:
            load_fuse_ram(domain_info)
            current_ratios = read_frequency_ratios(domain_info)
        except Exception as ex:
            QMessageBox.critical(self, 'Customize Frequency',
                                 f'Failed to read current frequency ratios:\n{ex}')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f'Customize Frequency - {label}')
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout()

        header = QLabel(f'⚙ Customize Frequency Ratios for {label}')
        header.setStyleSheet("""
            QLabel {
                font-size: 14px; font-weight: bold;
                color: #0071c5; padding: 10px;
            }
        """)
        layout.addWidget(header)

        freq_mult    = domain_info.get('freq_multiplier', 100)
        current_freqs = {key: value * freq_mult for key, value in current_ratios.items()}
        current_text  = ('Current Frequencies:\n'
                         + '\n'.join(f'{key.upper()}: {current_freqs.get(key, 0)} MHz'
                                     for key in current_ratios.keys()))
        info_label = QLabel(current_text)
        info_label.setStyleSheet(
            "padding: 10px; background: #f0f0f0; border-radius: 4px;")
        layout.addWidget(info_label)
        layout.addWidget(QLabel('Enter custom frequencies (MHz):'))

        freq_inputs = {}
        for key in current_ratios.keys():
            row  = QHBoxLayout()
            row.addWidget(QLabel(f'{key.upper()}:'))
            spin = QSpinBox()
            spin.setRange(0, 10000)
            spin.setSingleStep(100)
            spin.setValue(int(current_freqs.get(key, 0)))
            spin.setSuffix(' MHz')
            freq_inputs[key] = spin
            row.addWidget(spin)
            row.addStretch()
            layout.addLayout(row)

        warning = QLabel(
            '⚠️ WARNING: Invalid frequencies may cause system instability!')
        warning.setStyleSheet(
            "color: #d9534f; font-weight: bold; padding: 10px;")
        layout.addWidget(warning)

        from PyQt5.QtWidgets import QDialogButtonBox as _DBB
        buttons = _DBB(_DBB.Ok | _DBB.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.setLayout(layout)

        if dlg.exec_() != QDialog.Accepted:
            return

        custom_frequencies = {key: spin.value() for key, spin in freq_inputs.items()}

        freq_lines = '\n'.join(
            f'{k.upper()}: {v} MHz' for k, v in custom_frequencies.items())
        reply = QMessageBox.question(
            self, 'Confirm Customize Frequency',
            f'Customize frequencies for {label}?\n\n{freq_lines}\n\n'
            'This will reset the target system.',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self.customize_worker = CustomizeWorkerThread(
            self.curve_engine, domain_name, custom_frequencies)
        self.customize_worker.finished.connect(
            lambda result: self._after_customize_freq(result, label, domain_info))
        self.customize_worker.error.connect(
            lambda err: QMessageBox.critical(self, 'Customize Frequency', err))

        progress = QProgressDialog(
            'Processing customize frequency operation...\n\nPlease wait while the system resets and boots.',
            None, 0, 0, self)
        progress.setWindowTitle('Customize Frequency Operation')
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(400)
        progress.setCancelButton(None)
        progress.show()

        self.customize_worker.finished.connect(progress.close)
        self.customize_worker.error.connect(progress.close)
        self.customize_worker.start()

    def _after_customize_freq(self, result_data, label, domain_info):
        """Handle customize-frequency completion."""
        if 'error' in result_data:
            if result_data.get('error') == 'COLD_RESET':
                self._handle_cold_reset_error(result_data, 'Customize Frequency')
                return
            QMessageBox.critical(self, 'Customize Frequency',
                                 f'Customize operation failed:\n{result_data["error"]}')
            self._set_status("Status: Ready")
            return

        df          = result_data['dataframe']
        excel_path  = result_data['excel_path']
        png_path    = result_data.get('png_path')
        before_freqs = result_data['before_frequencies']
        after_freqs  = result_data['after_frequencies']

        tab = self._create_result_tab(df, excel_path, png_path)
        if self.output_tabs.count() >= 10:
            self.output_tabs.removeTab(1)
        self.output_tabs.addTab(tab, f'Customize: {label}')
        self.output_tabs.setCurrentWidget(tab)

        self._set_status("Status: Ready")

        comparison = 'Before → After:\n' + ''.join(
            f"{key.upper()}: {before_freqs.get(key):.0f} MHz → {after_freqs.get(key):.0f} MHz\n"
            for key in after_freqs
            if before_freqs.get(key) is not None and after_freqs.get(key) is not None
        )
        QMessageBox.information(
            self, 'Customize Frequency',
            f'Successfully customized {label} frequencies!\n\n{comparison}'
            f'\nResults exported to:\n{excel_path}\n\nTarget has been reset.')

    # ── WP edit completion ────────────────────────────────────────────────────

    def _after_wp_edit(self, result_data, label):
        """Handle WP edit completion."""
        if 'error' in result_data:
            if result_data.get('error') == 'COLD_RESET':
                self._handle_cold_reset_error(result_data, 'WP Edit')
                return
            QMessageBox.critical(self, 'WP Edit',
                                 f'Error: {result_data["error"]}')
            self._set_status("Status: Ready")
            return

        df_before    = result_data['before_dataframe']
        df_after     = result_data['after_dataframe']
        excel_path   = result_data['excel_path']
        png_path     = result_data['png_path']
        verification = result_data['verification']

        tab = self._create_bump_result_tab(
            df_before, df_after, excel_path, png_path, verification)
        if self.output_tabs.count() >= 10:
            self.output_tabs.removeTab(1)
        self.output_tabs.addTab(tab, f'WP Edit: {label}')
        self.output_tabs.setCurrentWidget(tab)

        self._set_status("Status: Ready")

        if verification['success']:
            QMessageBox.information(
                self, 'WP Edit Success',
                f'Successfully edited working points for {label}!\n\n'
                'All changes verified within tolerance (±5.7mV / ±1MHz).\n\n'
                f'Results exported to:\n{excel_path}\n\nTarget has been reset.')
        else:
            details_text = '\n'.join(
                (f"WP{d['wp']} voltage: Expected {d['expected_v']:.4f}V, "
                 f"Got {d['after_v']:.4f}V (Diff: {d['diff_mv']:.2f}mV)")
                if d.get('kind', 'voltage') == 'voltage'
                else
                (f"WP{d['wp']} freq: Expected {d['expected_freq']:g}MHz, "
                 f"Got {d.get('after_freq', '?'):g}MHz "
                 f"(Diff: {d.get('diff_mhz', 0):.1f}MHz)")
                for d in verification['details']
                if not d.get('within_tolerance', False)
            )
            QMessageBox.warning(
                self, 'WP Edit Verification',
                f'WP edit completed but some values outside tolerance:\n\n{details_text}\n\n'
                f'Results exported to:\n{excel_path}')
