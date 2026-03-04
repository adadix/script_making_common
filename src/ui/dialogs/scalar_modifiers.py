"""Scalar Modifiers dialog for VF Curve Manager UI.

ScalarModifiersDialog provides a full table view of all scalar modifier
registers discovered in vf_domains.json, with per-row hardware read,
inline editing, and Write Selected support.
"""
import sys
import os
from PyQt5.QtWidgets import QDialog, QMessageBox, QApplication
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QHeaderView, QAbstractItemView
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

# Ensure src/ is on path when this module is imported standalone
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

class ScalarModifiersDialog(QDialog):
    """Dialog for viewing and editing scalar modifier registers.

    Features
    --------
    - Full table of all scalar modifiers discovered in vf_domains.json
    - Per-row refresh from hardware
    - Inline editing with physical-unit entry and raw-to-physical conversion
    - Read All / Write Selected buttons
    - Type filter combo to limit view to a specific modifier class
    """

    _STYLE_BASE = """
        QDialog { background: #f8f9fa; }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #0071c5;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 8px;
        }
        QGroupBox::title { left: 8px; padding: 0 4px; }
        QPushButton {
            background: #0071c5; color: white;
            border: none; border-radius: 4px;
            padding: 5px 12px; font-weight: bold;
        }
        QPushButton:hover { background: #005a9e; }
        QPushButton:disabled { background: #adb5bd; color: #6c757d; }
        QTableWidget { gridline-color: #dee2e6; }
        QHeaderView::section {
            background: #0071c5; color: white;
            padding: 4px; font-weight: bold;
        }
        QComboBox { padding: 3px 6px; }
    """

    COL_TYPE    = 0
    COL_LABEL   = 1
    COL_REGISTER= 2
    COL_RAW     = 3
    COL_CONV    = 4
    COL_UNITS   = 5
    COL_NEWVAL  = 6
    COL_STATUS  = 7
    HEADERS     = ['Type', 'Label', 'Register', 'Raw', 'Value', 'Units',
                   'New Value', 'Status']

    def __init__(self, curve_engine, parent=None):
        super().__init__(parent)
        self.curve_engine = curve_engine
        self.setWindowTitle('Scalar Modifiers')
        self.setMinimumSize(1100, 600)
        self.setStyleSheet(self._STYLE_BASE)
        self._build_ui()
        self._populate_type_filter()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                                      QComboBox, QPushButton, QTableWidget,
                                      QTableWidgetItem, QGroupBox, QHeaderView,
                                      QAbstractItemView)
        from PyQt5.QtCore import Qt

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ── Filter bar ────────────────────────────────────────────────────
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel('Filter by type:'))
        self.type_combo = QComboBox()
        self.type_combo.setMinimumWidth(180)
        self.type_combo.currentTextChanged.connect(self._apply_type_filter)
        filter_bar.addWidget(self.type_combo)
        filter_bar.addStretch()
        self.lbl_count = QLabel('0 modifiers')
        filter_bar.addWidget(self.lbl_count)
        main_layout.addLayout(filter_bar)

        # ── Table ─────────────────────────────────────────────────────────
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setAlternatingRowColors(True)
        col_widths = [100, 200, 280, 60, 80, 55, 90, 120]
        for i, w in enumerate(col_widths):
            self.table.setColumnWidth(i, w)
        # Only COL_NEWVAL is editable — lock the rest
        self.table.itemChanged.connect(self._on_item_changed)
        main_layout.addWidget(self.table)

        # ── Action buttons ────────────────────────────────────────────────
        btn_bar = QHBoxLayout()
        self.btn_read_all = QPushButton('\U0001f504  Read All from Hardware')
        self.btn_write_sel = QPushButton('\u270f  Write Selected Row')
        self.btn_write_sel.setStyleSheet(
            'QPushButton { background: #28a745; color: white; border: none; '
            'border-radius: 4px; padding: 5px 12px; font-weight: bold; } '
            'QPushButton:hover { background: #218838; } '
            'QPushButton:disabled { background: #adb5bd; color: #6c757d; }'
        )
        btn_close = QPushButton('Close')
        btn_close.setStyleSheet(
            'QPushButton { background: #6c757d; color: white; border: none; '
            'border-radius: 4px; padding: 5px 12px; font-weight: bold; } '
            'QPushButton:hover { background: #5a6268; }'
        )
        self.btn_read_all.clicked.connect(self.read_all)
        self.btn_write_sel.clicked.connect(self.write_selected)
        btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_read_all)
        btn_bar.addWidget(self.btn_write_sel)
        btn_bar.addStretch()
        btn_bar.addWidget(btn_close)
        main_layout.addLayout(btn_bar)

        # ── Status label ──────────────────────────────────────────────────
        self.status_label = QLabel('')
        self.status_label.setStyleSheet('color: #495057; font-size: 11px;')
        main_layout.addWidget(self.status_label)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _populate_type_filter(self):
        """Populate the type filter combo from the loaded scalars config."""
        scalars = self._get_scalars()
        types = sorted({v.get('type', 'unknown') for v in scalars.values()})
        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        self.type_combo.addItem('(all types)')
        for t in types:
            self.type_combo.addItem(t)
        self.type_combo.blockSignals(False)
        self._load_table(scalars)

    def _get_scalars(self) -> dict:
        """Return scalar_modifiers from config."""
        try:
            return self.curve_engine.config_loader.get_scalar_modifiers()
        except Exception:
            return {}

    def _load_table(self, scalars: dict):
        """Fill the table with rows for each scalar modifier (no hardware read yet)."""
        from PyQt5.QtWidgets import QTableWidgetItem
        from PyQt5.QtCore import Qt

        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for reg, info in sorted(scalars.items(),
                                 key=lambda kv: (kv[1].get('type', ''), kv[0])):
            r = self.table.rowCount()
            self.table.insertRow(r)
            for col, text in enumerate([
                info.get('type', ''),
                info.get('label', reg),
                reg,
                '',    # raw
                '',    # converted
                '',    # units
                '',    # new value (editable)
                '\u23f3 pending',
            ]):
                item = QTableWidgetItem(str(text))
                if col != self.COL_NEWVAL:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, col, item)

        self.table.blockSignals(False)
        self.lbl_count.setText(f'{self.table.rowCount()} modifiers')

    def _apply_type_filter(self, type_text: str):
        """Show/hide rows by selected type."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_TYPE)
            typ = item.text() if item else ''
            visible = (type_text == '(all types)') or (typ == type_text)
            self.table.setRowHidden(row, not visible)

    def _on_item_changed(self, item):
        """Clear status cell when user edits New Value column."""
        if item.column() == self.COL_NEWVAL:
            status_item = self.table.item(item.row(), self.COL_STATUS)
            if status_item:
                status_item.setText('\u270f edited')

    def _set_row_values(self, row: int, result: dict):
        """Update raw/converted/units/status cells in a row from a read result."""
        from PyQt5.QtWidgets import QTableWidgetItem
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QColor

        if result.get('ok'):
            raw  = str(result.get('raw', ''))
            conv = str(result.get('converted', ''))
            units = str(result.get('units', ''))
            status = '\u2713 ok'
            status_color = QColor('#28a745')
        else:
            raw = conv = units = ''
            status = '\u2717 error'
            status_color = QColor('#dc3545')

        for col, txt in [(self.COL_RAW, raw), (self.COL_CONV, conv),
                         (self.COL_UNITS, units), (self.COL_STATUS, status)]:
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
            item.setText(txt)
            if col == self.COL_STATUS:
                item.setForeground(status_color)

    # ── Actions ────────────────────────────────────────────────────────────

    def read_all(self):
        """Read all currently visible scalar modifiers from hardware."""
        scalars = self._get_scalars()
        if not scalars:
            self.status_label.setText('No scalar modifiers configured.')
            return

        from utils.hardware_access import read_all_scalar_modifiers as _read_all
        self.status_label.setText('Reading from hardware...')
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        results = _read_all(scalars)

        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            reg_item = self.table.item(row, self.COL_REGISTER)
            if reg_item is None:
                continue
            reg = reg_item.text()
            if reg in results:
                self._set_row_values(row, results[reg])
        self.table.blockSignals(False)

        ok_count = sum(1 for r in results.values() if r.get('ok'))
        self.status_label.setText(
            f'Read {ok_count}/{len(results)} modifiers successfully.'
        )

    def write_selected(self):
        """Write the New Value for the currently selected row."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.status_label.setText('Select a row first.')
            return

        row = rows[0].row()
        reg_item  = self.table.item(row, self.COL_REGISTER)
        val_item  = self.table.item(row, self.COL_NEWVAL)
        if val_item is None or val_item.text().strip() == '':
            self.status_label.setText('Enter a new value in the "New Value" column first.')
            return

        reg = reg_item.text().strip()

        try:
            new_phys = float(val_item.text().strip())
        except ValueError:
            self.status_label.setText('New Value must be a number.')
            return

        from PyQt5.QtWidgets import QMessageBox
        units_item = self.table.item(row, self.COL_UNITS)
        units = units_item.text() if units_item else ''
        answer = QMessageBox.question(
            self, 'Confirm Write',
            f"Write {new_phys} {units} to '{reg}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        result = self.curve_engine.edit_scalar_modifier(reg, new_phys)
        if result['ok']:
            self._set_row_values(row, result['after'])
            status_item = self.table.item(row, self.COL_STATUS)
            if status_item:
                status_item.setText('\u2713 written')
            self.status_label.setText(f'OK: {result["message"]}')
            # Before/after Excel export (same as bump/flatten pattern)
            try:
                from discovery.discovery_core import export_scalar_change_to_excel
                _scalars = self.curve_engine.config_loader.get_scalar_modifiers()
                _info    = _scalars.get(reg, {})
                _xl = export_scalar_change_to_excel(
                    reg, result['before'], result['after'], _info)
                if _xl:
                    import os
                    self.status_label.setText(
                        f'OK: {result["message"]}  \u2014  Excel: {os.path.basename(_xl)}')
            except Exception:
                pass  # Excel export is best-effort; never block the GUI
        else:
            self.status_label.setText(f'FAILED: {result["message"]}')
