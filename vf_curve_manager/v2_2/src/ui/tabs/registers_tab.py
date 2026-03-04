"""Discovered Registers tab widget builder.

build_registers_tab_widget() creates the filterable, editable table
used in the "Discovered Registers" tab of the Registers view.
Extracted from CurveManagerUI to keep the main class smaller.

Sub-builders
------------
_build_info_bar         -- top info QLabel
_build_filter_combos    -- domain/category/root filter combos + search + buttons
_build_registers_table  -- styled QTableWidget with headers
build_registers_tab_widget -- orchestrator (filter/edit/export closure logic lives here)
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QMessageBox, QComboBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor


# ---------------------------------------------------------------------------
# Sub-builder 1: info bar
# ---------------------------------------------------------------------------

def _build_info_bar(records: list, platform_display: str,
                    hw_status: str, timestamp: str) -> QLabel:
    """Return the styled info QLabel shown at the top of the tab."""
    active_cnt = sum(1 for r in records if r.get('active'))
    lbl = QLabel(
        f'<b>Platform:</b> {platform_display} &nbsp;|&nbsp; '
        f'<b>Total:</b> {len(records)} registers &nbsp;|&nbsp; '
        f'<b>Active (\u22600):</b> {active_cnt} &nbsp;|&nbsp; '
        f'<b>Values:</b> {hw_status} &nbsp;|&nbsp; '
        f'<b>Cache stamp:</b> {timestamp}'
    )
    lbl.setStyleSheet('padding:3px; font-size:11px; color:#333;')
    return lbl


# ---------------------------------------------------------------------------
# Sub-builder 2: filter combo row
# ---------------------------------------------------------------------------

def _build_filter_combos(records: list) -> dict:
    """
    Build all filter widgets for the registers tab.

    Returns a dict with keys:
        domain_combo, cat_combo, fuse_root_combo,
        active_cb, search_edit, export_btn, save_btn
    """
    all_domains = ['All'] + sorted({r.get('domain', 'unknown') for r in records})
    all_cats    = ['All'] + sorted({r.get('category', 'other')  for r in records})
    all_roots   = ['All'] + sorted({
        r.get('fuse_path', '').split('.')[0]
        for r in records if r.get('fuse_path', '')
    })

    domain_combo = QComboBox()
    domain_combo.addItems(all_domains)
    domain_combo.setFixedWidth(140)

    cat_combo = QComboBox()
    cat_combo.addItems(all_cats)
    cat_combo.setFixedWidth(140)

    fuse_root_combo = QComboBox()
    fuse_root_combo.addItems(all_roots)
    fuse_root_combo.setFixedWidth(90)
    fuse_root_combo.setToolTip(
        'Filter by fuse root\n'
        '  cdie = CPU cores, bigcore, atom, GT, ring\n'
        '  soc  = IO, USB, PCIe, PMC'
    )

    active_cb   = QCheckBox('Active only')
    search_edit = QLineEdit()
    search_edit.setPlaceholderText('Search name or description\u2026')
    search_edit.setFixedWidth(240)

    export_btn = QPushButton('\U0001f4be Export to Excel')
    export_btn.setFixedHeight(28)
    export_btn.setStyleSheet(
        'QPushButton{background:#0071c5;color:white;border:none;'
        'border-radius:4px;font-weight:bold;padding:0 10px;}'
        'QPushButton:hover{background:#005a9e;}'
    )

    save_btn = QPushButton('\u26a1 Apply to Hardware')
    save_btn.setFixedHeight(28)
    save_btn.setToolTip(
        'Write edited values to hardware via ITP:\n'
        '  load_fuse_ram \u2192 write \u2192 flush_fuse_ram \u2192 resettarget \u2192 verify'
    )
    save_btn.setStyleSheet(
        'QPushButton{background:#c0392b;color:white;border:none;'
        'border-radius:4px;font-weight:bold;padding:0 10px;}'
        'QPushButton:hover{background:#96281b;}'
    )

    return dict(
        domain_combo=domain_combo,
        cat_combo=cat_combo,
        fuse_root_combo=fuse_root_combo,
        active_cb=active_cb,
        search_edit=search_edit,
        export_btn=export_btn,
        save_btn=save_btn,
    )


# ---------------------------------------------------------------------------
# Sub-builder 3: table widget
# ---------------------------------------------------------------------------

_REG_COLS = [
    'Register Name', 'Value (Dec)', 'Value (Hex)', 'Converted',
    'Active', 'Category', 'Domain', 'Fuse Path', 'Description'
]


def _build_registers_table() -> QTableWidget:
    """Return a styled, sortable QTableWidget with the standard register columns."""
    from PyQt5.QtWidgets import QHeaderView

    table = QTableWidget(0, len(_REG_COLS))
    table.setHorizontalHeaderLabels(_REG_COLS)
    table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.SelectedClicked)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSortingEnabled(True)
    table.setAlternatingRowColors(False)
    table.setStyleSheet("""
        QTableWidget  { font-size:11px; gridline-color:#e0e0e0; }
        QHeaderView::section {
            background-color:#0071c5; color:white;
            font-weight:bold; padding:5px;
            border:1px solid #005a9e;
        }
        QTableWidget::item:selected { background:#cce5ff; color:#212529; }
    """)
    hdr = table.horizontalHeader()
    hdr.setSectionResizeMode(0, QHeaderView.Interactive)
    hdr.setSectionResizeMode(len(_REG_COLS) - 1, QHeaderView.Stretch)
    for i in range(1, len(_REG_COLS) - 1):
        hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
    table.setColumnWidth(0, 330)
    table.verticalHeader().setDefaultSectionSize(22)
    return table


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_registers_tab_widget(records: list, platform_display: str,
                                timestamp: str, hw_status: str = '\u26aa cached'):
    """Build the Discovered Registers tab: filter bar + table + export button.

    Uses _build_info_bar, _build_filter_combos, and _build_registers_table
    for widget construction; defines filter/edit/export closure logic here.
    """
    container = QWidget()
    layout    = QVBoxLayout(container)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(5)

    # ── Info bar ──────────────────────────────────────────────────────
    active_cnt = sum(1 for r in records if r.get('active'))
    info_lbl = _build_info_bar(records, platform_display, hw_status, timestamp)
    layout.addWidget(info_lbl)

    # ── Filter widgets ────────────────────────────────────────────────
    ctrls = _build_filter_combos(records)
    domain_combo    = ctrls['domain_combo']
    cat_combo       = ctrls['cat_combo']
    fuse_root_combo = ctrls['fuse_root_combo']
    active_cb       = ctrls['active_cb']
    search_edit     = ctrls['search_edit']
    export_btn      = ctrls['export_btn']
    save_btn        = ctrls['save_btn']

    filter_row = QHBoxLayout()
    filter_row.addWidget(QLabel('Domain:'))
    filter_row.addWidget(domain_combo)
    filter_row.addSpacing(8)
    filter_row.addWidget(QLabel('Category:'))
    filter_row.addWidget(cat_combo)
    filter_row.addSpacing(8)
    filter_row.addWidget(QLabel('Root:'))
    filter_row.addWidget(fuse_root_combo)
    filter_row.addSpacing(8)
    filter_row.addWidget(active_cb)
    filter_row.addSpacing(8)
    filter_row.addWidget(QLabel('Search:'))
    filter_row.addWidget(search_edit)
    filter_row.addStretch()
    filter_row.addWidget(save_btn)
    filter_row.addSpacing(6)
    filter_row.addWidget(export_btn)
    layout.addLayout(filter_row)

    # ── Table ─────────────────────────────────────────────────────────
    table = _build_registers_table()
    layout.addWidget(table, 1)

    # Only Value (Dec) column is editable — triggers hardware write
    EDITABLE_COLS = {1}   # col index
    VALUE_COL     = 1

    # ── Pending edits tracking ────────────────────────────────────────
    _pending_edits = {}
    _populating    = [False]
    _edit_color    = QColor('#0055aa')

    act_color   = QColor('#E6F4EA')
    inact_color = QColor('#FFF8F0')

    # ── Populate helper ───────────────────────────────────────────────
    def _populate(recs):
        _populating[0] = True
        table.setSortingEnabled(False)
        table.setRowCount(len(recs))
        for ri, rec in enumerate(recs):
            is_active = bool(rec.get('active'))
            bg   = act_color if is_active else inact_color
            name = rec.get('name', '')
            pend = _pending_edits.get(name, {})
            dec_val = '' if rec.get('value') is None else str(rec.get('value'))
            if name in pend:
                dec_val = str(pend['new_value'])
            vals = [
                name,
                dec_val,
                rec.get('hex', ''),
                rec.get('converted', ''),
                'Yes' if is_active else 'No',
                rec.get('category',    ''),
                rec.get('domain',      ''),
                rec.get('fuse_path',   ''),
                rec.get('description', ''),
            ]
            for ci, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setBackground(bg)
                if ci == 0:
                    item.setData(Qt.UserRole, name)
                _ro_cats = {'itd_voltage', 'itd_slope', 'p0_override', 'acode_min'}
                _sensor_row = rec.get('category', '') in _ro_cats
                if ci in EDITABLE_COLS and not _sensor_row:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    if name in pend:
                        item.setForeground(_edit_color)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(ri, ci, item)
        table.setSortingEnabled(True)
        _populating[0] = False

    # ── Filter logic ──────────────────────────────────────────────────
    def _apply_filter(*_):
        sel_dom  = domain_combo.currentText()
        sel_cat  = cat_combo.currentText()
        sel_root = fuse_root_combo.currentText()
        act_only = active_cb.isChecked()
        query    = search_edit.text().strip().lower()
        filtered = [
            r for r in records
            if (sel_dom  == 'All' or r.get('domain',   '') == sel_dom)
            and (sel_cat == 'All' or r.get('category', '') == sel_cat)
            and (sel_root == 'All'
                 or r.get('fuse_path', '').split('.')[0] == sel_root)
            and (not act_only or r.get('active'))
            and (not query
                 or query in r.get('name', '').lower()
                 or query in r.get('description', '').lower())
        ]
        _populate(filtered)
        info_lbl.setText(
            f'<b>Platform:</b> {platform_display} &nbsp;|&nbsp; '
            f'<b>Showing:</b> {len(filtered)}/{len(records)} registers &nbsp;|&nbsp; '
            f'<b>Active (\u22600):</b> {active_cnt} &nbsp;|&nbsp; '
            f'<b>Values:</b> {hw_status} &nbsp;|&nbsp; '
            f'<b>Cache stamp:</b> {timestamp}'
        )

    domain_combo.currentIndexChanged.connect(_apply_filter)
    cat_combo.currentIndexChanged.connect(_apply_filter)
    fuse_root_combo.currentIndexChanged.connect(_apply_filter)
    active_cb.stateChanged.connect(_apply_filter)
    search_edit.textChanged.connect(_apply_filter)

    # ── Export handler ────────────────────────────────────────────────
    def _export():
        try:
            from discovery.auto_discover_vf_registers import export_discovered_registers_to_excel
        except ImportError as exc:
            QMessageBox.warning(container, 'Export Error', str(exc))
            return
        sel_dom  = domain_combo.currentText()
        sel_cat  = cat_combo.currentText()
        sel_root = fuse_root_combo.currentText()
        act_only = active_cb.isChecked()
        query    = search_edit.text().strip().lower()
        exp_recs = [
            r for r in records
            if (sel_dom  == 'All' or r.get('domain',   '') == sel_dom)
            and (sel_cat == 'All' or r.get('category', '') == sel_cat)
            and (sel_root == 'All'
                 or r.get('fuse_path', '').split('.')[0] == sel_root)
            and (not act_only or r.get('active'))
            and (not query
                 or query in r.get('name', '').lower()
                 or query in r.get('description', '').lower())
        ]
        path = export_discovered_registers_to_excel(
            platform_display=platform_display, records=exp_recs
        )
        if path:
            QMessageBox.information(
                container, 'Export Complete',
                f'Exported {len(exp_recs)} registers to:\n\n{path}'
            )
        else:
            QMessageBox.warning(
                container, 'Export Failed',
                'Could not export to Excel.\nCheck console output for details.'
            )

    export_btn.clicked.connect(_export)

    # ── Item-changed handler ──────────────────────────────────────────
    def _on_item_changed(item):
        if _populating[0]:
            return
        col = item.column()
        if col != VALUE_COL:
            return
        name_item = table.item(item.row(), 0)
        if name_item is None:
            return
        reg_name = name_item.data(Qt.UserRole)
        if not reg_name:
            return
        rec = next((r for r in records if r['name'] == reg_name), None)
        if rec is None:
            return
        txt = item.text().strip()
        try:
            new_val = int(txt, 0)
        except ValueError:
            item.setForeground(QColor('#cc0000'))
            return
        _pending_edits[reg_name] = {
            'fuse_path': rec.get('fuse_path', ''),
            'new_value': new_val,
        }
        item.setForeground(_edit_color)
        save_btn.setText(f'\u26a1 Apply to Hardware ({len(_pending_edits)})')

    table.itemChanged.connect(_on_item_changed)

    # ── Apply to hardware handler ────────────────────────────────────
    def _apply_to_hw():
        if not _pending_edits:
            QMessageBox.information(container, 'No Edits',
                                    'Double-click a Value (Dec) cell to enter a new value,\n'
                                    'then click Apply to Hardware.')
            return
        lines = [f"  {name}  \u2192  {info['new_value']}  (0x{info['new_value']:x})"
                 for name, info in _pending_edits.items()]
        confirm = QMessageBox.question(
            container, 'Apply to Hardware',
            'This will:\n'
            '  1. load_fuse_ram\n'
            '  2. Write new values\n'
            '  3. flush_fuse_ram\n'
            '  4. itp.resettarget()\n'
            '  5. Verify readback\n\n'
            'Registers to write:\n' + '\n'.join(lines) + '\n\nProceed?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            from utils.hardware_access import apply_discovered_register_edits
        except ImportError as exc:
            QMessageBox.critical(container, 'Import Error', str(exc))
            return
        edits_list = [
            {'fuse_path': info['fuse_path'],
             'reg_name':  name,
             'new_value': info['new_value']}
            for name, info in _pending_edits.items()
        ]
        result = apply_discovered_register_edits(edits_list)
        if result['success']:
            _pending_edits.clear()
            black = QColor('#000000')
            for row in range(table.rowCount()):
                it = table.item(row, VALUE_COL)
                if it:
                    it.setForeground(black)
            save_btn.setText('\u26a1 Apply to Hardware')
            lines = []
            for w in result['written']:
                tick = '\u2713' if w['verified'] else '\u2717 MISMATCH'
                lines.append(f"[{tick}] {w['reg_name']}: {w['before']} \u2192 {w['after']}")
            QMessageBox.information(
                container, 'Applied',
                result['message'] + '\n\n' + '\n'.join(lines)
            )
        else:
            msg = result['message']
            if result.get('cold_reset'):
                msg = '\u274c COLD RESET detected!\n\n' + msg
            QMessageBox.critical(container, 'Apply Failed', msg)

    save_btn.clicked.connect(_apply_to_hw)

    _populate(records)
    return container
