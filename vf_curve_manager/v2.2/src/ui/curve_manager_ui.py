"""
CurveManagerUI - PyQt5 Dashboard Interface for VF Curve Manager Tool

Professional interface matching VF Monitor Tool v2.0 theme.
Features:
- Intel-styled header with gradient
- Domain selection sidebar with buttons
- Metrics and operations controls
- Real-time progress tracking
- Data export and visualization
"""

import sys
import os
import time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QCheckBox, QGroupBox, QLineEdit, QSpinBox, QSizePolicy,
    QScrollArea, QTabWidget, QDialog, QDialogButtonBox, QTableWidget,
    QTableWidgetItem, QProgressDialog
)
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

# Add parent directory to path
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from utils import hardware_access

import logging
log = logging.getLogger(__name__)


# â”€â”€ Worker threads (background QThread operations) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from .workers import BumpWorkerThread, CustomizeWorkerThread
from .dialogs.scalar_modifiers import ScalarModifiersDialog
from .tabs.result_tabs import (
    _create_result_tab as _rt_create_result_tab,
    _create_cumulative_tab as _rt_create_cumulative_tab,
    _create_bump_result_tab as _rt_create_bump_result_tab,
)
from .mixins import ThemeMixin, DomainMixin, OperationsMixin, DiscoveryMixin, ProgressMixin


class _ZeroWpFilterWorker(QThread):
    """Background thread that calls filter_zero_wp_domains().

    Emits ``finished_signal`` with the list of domain names that were removed
    once the hardware I/O completes.  All fuse RAM loads happen inside this
    thread so the GUI main thread is never blocked.
    """
    finished_signal = pyqtSignal(list)  # list[str] of pruned domain names

    def __init__(self, config_loader, parent=None):
        super().__init__(parent)
        self._config_loader = config_loader

    def run(self):
        try:
            pruned = self._config_loader.filter_zero_wp_domains()
        except Exception as _e:
            log.warning("Zero-WP domain filter failed (non-fatal): %s", _e)
            pruned = []
        self.finished_signal.emit(pruned)


class CurveManagerUI(ThemeMixin, DomainMixin, OperationsMixin, DiscoveryMixin, ProgressMixin, QWidget):
    """Professional VF Curve Manager UI matching VF Monitor v2.2 theme."""

    def __init__(self, curve_engine, config_loader):
        super().__init__()
        self.setWindowTitle('VF Curve Manager v2.2â„¢ â€” BDC CVE Labs')
        self.setWindowIcon(QIcon())
        self.resize(1400, 900)
        
        self.curve_engine = curve_engine
        self.config_loader = config_loader
        self.dark_theme = False
        
        # Get domain lists
        self.domains = self.config_loader.get_domain_list()
        
        # Initialize UI state
        self.domain_checkboxes = {}
        self.domain_buttons = {}
        
        # Create the professional dashboard UI
        self.create_dashboard_ui()

        # Sync ENABLE_SUT_VERIFICATION to match checkbox default (unchecked = False).
        # init_hardware() defaults enable_sut_check=True, so without this sync the
        # flag stays True even when the checkbox is unchecked.
        from utils import hardware_access as _ha
        _ha.ENABLE_SUT_VERIFICATION = self.sut_verification_checkbox.isChecked()

        # Release layout-driven minimum size so the window fits on 1920px screens.
        # Qt normally enforces layout.minimumSize() on the window; that hint can
        # exceed the physical display width and trigger a geometry warning.
        from PyQt5.QtWidgets import QLayout
        self.layout().setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(900, 600)

        # Defer zero-WP domain filtering: run it in a background thread 500 ms
        # after the window becomes visible so the GUI is never blocked by the
        # fuse RAM load calls that the filter requires.
        self._zero_wp_worker = None  # keep a reference to prevent GC
        QTimer.singleShot(500, self._start_zero_wp_filter)
    
    # ── Deferred zero-WP domain filter ─────────────────────────────────────

    def _start_zero_wp_filter(self):
        """Launch background thread that filters out all-zero WP domains."""
        from utils import hardware_access as _ha
        if getattr(_ha, 'MOCK_MODE', False):
            return  # nothing to filter in mock mode
        self._zero_wp_worker = _ZeroWpFilterWorker(self.config_loader, parent=self)
        self._zero_wp_worker.finished_signal.connect(self._on_zero_wp_filter_done)
        self._zero_wp_worker.start()
        log.debug("Zero-WP domain filter started in background thread")

    def _on_zero_wp_filter_done(self, pruned: list):
        """Called on the main thread when the background filter completes."""
        if not pruned:
            return
        for domain_name in pruned:
            btn = self.domain_buttons.pop(domain_name, None)
            if btn is not None:
                btn.hide()
                btn.deleteLater()
            self.domain_checkboxes.pop(domain_name, None)
        # Keep self.domains in sync
        self.domains = [d for d in self.domains if d not in pruned]
        self.update_selected_domains_display()
        log.info("Zero-WP filter: hid %d domain(s) from selector: %s",
                 len(pruned), ', '.join(pruned))
        self._set_status(
            f"Domain list updated — {len(pruned)} unprogrammed domain(s) hidden",
            level='ok',
        )

    # ── Dashboard layout ────────────────────────────────────────────────────

    def create_dashboard_ui(self):
        """Create professional dashboard with header, tabs, sidebar, and footer"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header Section
        self.create_header(main_layout)
        
        # Selected Domains Display (full width at top)
        self.create_selected_domains_display(main_layout)
        
        # Content Area (Domain Sidebar + Controls/Output)
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)
        
        # Left: Domain selection sidebar with scroll
        # Width is driven by the longest domain key name (10px font Ã— 8px/char heuristic).
        # Cap raised to 320 to accommodate long names like core0_bigcore_base_vf (21 chars).
        max_domain_length = max(len(d) for d in self.domains) if self.domains else 10
        sidebar_width = max(200, min(320, max_domain_length * 12 + 40))
        
        domain_container = QWidget()
        domain_container.setMaximumWidth(sidebar_width)
        domain_container.setMinimumWidth(sidebar_width)
        domain_layout = QVBoxLayout(domain_container)
        self.create_domain_sidebar(domain_layout)
        content_layout.addWidget(domain_container)
        
        # Right: Controls and Output (with stretch factor to fill space)
        right_container = QWidget()
        right_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        self.create_controls(right_layout)
        self.create_output_area(right_layout)
        content_layout.addWidget(right_container, 1)  # Stretch factor = 1
        
        main_layout.addLayout(content_layout, 1)  # Stretch factor = 1 to fill vertical space
        
        # Footer Section
        self.create_footer(main_layout)
        
        # Apply initial theme
        self.apply_light_theme()
    
    def create_controls(self, parent_layout):
        """Create control panels for operations."""
        controls_container = QWidget()
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setSpacing(10)
        
        # Interpolation checkbox
        self.interp_checkbox = QCheckBox('Enable Interpolation')
        self.interp_checkbox.setChecked(True)
        self.interp_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 11px;
                color: #212529;
            }
        """)
        controls_layout.addWidget(self.interp_checkbox)
        
        # SUT Verification checkbox
        self.sut_verification_checkbox = QCheckBox('Enable SUT Verification & Recovery')
        self.sut_verification_checkbox.setChecked(False)  # Default: disabled for fast operation
        self.sut_verification_checkbox.setToolTip(
            'Enable automatic SUT reachability checking and ITP recovery from power states.\n'
            'When disabled: Faster operation, suitable for stable systems.\n'
            'When enabled: Automatic recovery from SLP_S5, sleep states, and reset conditions.'
        )
        self.sut_verification_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 11px;
                color: #212529;
            }
        """)
        self.sut_verification_checkbox.stateChanged.connect(self.on_sut_verification_changed)
        controls_layout.addWidget(self.sut_verification_checkbox)
        
        # Show VF Curve button
        self.btn_show_vf = QPushButton('\U0001F4CA Show VF Curve')
        self.btn_show_vf.setFixedHeight(40)
        self.btn_show_vf.setStyleSheet(self._BTN_PRIMARY)
        self.btn_show_vf.clicked.connect(self.show_vf_curve)
        controls_layout.addWidget(self.btn_show_vf)
        
        # Bump Controls Group
        bump_group = QGroupBox('Voltage Bump Controls')
        bump_group.setStyleSheet("""
            QGroupBox {
                background: #f8f9fa;
                border: 2px solid #0071c5;
                border-radius: 6px;
                font-weight: bold;
                padding-top: 15px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        bump_layout = QVBoxLayout()
        
        # Bump value input
        bump_input_layout = QHBoxLayout()
        bump_input_layout.addWidget(QLabel('Bump Value (mV):'))
        self.bump_val = QLineEdit()
        self.bump_val.setPlaceholderText('Enter millivolts')
        self.bump_val.setStyleSheet("""
            QLineEdit {
                padding: 5px;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
        """)
        bump_input_layout.addWidget(self.bump_val)
        bump_layout.addLayout(bump_input_layout)
        
        # Bump buttons
        bump_buttons_layout = QHBoxLayout()
        self.btn_bump_up = QPushButton('\u2B06 Bump Up')
        self.btn_bump_down = QPushButton('\u2B07 Bump Down')
        for btn in [self.btn_bump_up, self.btn_bump_down]:
            btn.setFixedHeight(35)
            btn.setStyleSheet(self._BTN_ACTION)
        self.btn_bump_down.setStyleSheet(self._BTN_DANGER)
        self.btn_bump_up.clicked.connect(lambda: self.bump_domains('up'))
        self.btn_bump_down.clicked.connect(lambda: self.bump_domains('down'))
        bump_buttons_layout.addWidget(self.btn_bump_up)
        bump_buttons_layout.addWidget(self.btn_bump_down)
        bump_layout.addLayout(bump_buttons_layout)
        
        bump_group.setLayout(bump_layout)
        controls_layout.addWidget(bump_group)
        
        # Additional Operations
        ops_layout = QHBoxLayout()
        self.btn_wp_edit = QPushButton('\u270F WP Edit')
        self.btn_flatten_freq = QPushButton('\U0001F4DD Flatten Freq')
        self.btn_customize_freq = QPushButton('\u2699 Customize Freq')
        
        for btn in [self.btn_wp_edit, self.btn_flatten_freq, self.btn_customize_freq]:
            btn.setFixedHeight(35)
            btn.setStyleSheet(self._BTN_SECONDARY)
        
        self.btn_wp_edit.clicked.connect(self.wp_edit)
        self.btn_flatten_freq.clicked.connect(self.flatten_freq)
        self.btn_customize_freq.clicked.connect(self.customize_freq)
        
        ops_layout.addWidget(self.btn_wp_edit)
        ops_layout.addWidget(self.btn_flatten_freq)
        ops_layout.addWidget(self.btn_customize_freq)
        controls_layout.addLayout(ops_layout)

        # Discovered Registers â€” view + export all scanned register values
        self.btn_discovered_regs = QPushButton('\U0001F50D Discovered Registers')
        self.btn_discovered_regs.setFixedHeight(35)
        self.btn_discovered_regs.setStyleSheet(self._BTN_INFO)
        self.btn_discovered_regs.setToolTip(
            'View all registers found during the last discovery run.\n'
            'Shows name, value (dec + hex), active status, category, domain,\n'
            'fuse path and description. Supports filtering and Excel export.'
        )
        self.btn_discovered_regs.clicked.connect(self.open_registers_tab)
        controls_layout.addWidget(self.btn_discovered_regs)

        # â”€â”€ Scalar Modifiers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self.scalar_mod_checkbox = QCheckBox('Enable Scalar Modifiers')
        self.scalar_mod_checkbox.setChecked(False)
        self.scalar_mod_checkbox.setToolTip(
            'Enable the Scalar Modifiers panel to view and edit ITD, P0 override,\n'
            'downbin, MCT delta, ACODE min, and other single-register fuse values.'
        )
        self.scalar_mod_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 11px;
                color: #212529;
            }
        """)
        controls_layout.addWidget(self.scalar_mod_checkbox)

        self.btn_scalar_mods = QPushButton('\U0001f527 Scalar Modifiers...')
        self.btn_scalar_mods.setFixedHeight(35)
        self.btn_scalar_mods.setEnabled(False)
        self.btn_scalar_mods.setStyleSheet(self._BTN_PURPLE)
        self.btn_scalar_mods.setToolTip(
            "Open the Scalar Modifiers dialog to read and write scalar fuse registers.\n"
            "Requires 'Enable Scalar Modifiers' checkbox to be checked."
        )
        self.btn_scalar_mods.clicked.connect(self.open_scalar_modifiers_dialog)
        self.scalar_mod_checkbox.stateChanged.connect(
            lambda state: self.btn_scalar_mods.setEnabled(bool(state))
        )
        controls_layout.addWidget(self.btn_scalar_mods)

        controls_layout.addStretch()
        parent_layout.addWidget(controls_container)
    
    def create_output_area(self, parent_layout):
        """Create tabbed output area for results."""
        output_group = QGroupBox("Output / Results")
        output_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        output_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                padding-top: 15px;
                background: white;
                border: 2px solid #0071c5;
                border-radius: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #0071c5;
            }
        """)
        
        output_layout = QVBoxLayout()
        output_layout.setContentsMargins(5, 5, 5, 5)
        
        self.output_tabs = QTabWidget()
        self.output_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.output_tabs.setTabPosition(QTabWidget.North)
        self.output_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #0071c5;
                background: white;
            }
            QTabBar::tab {
                background: #e0e0e0;
                color: #212529;
                padding: 8px 16px;
                border: 1px solid #ccc;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background: #0071c5;
                color: white;
            }
            QTabBar::tab:hover {
                background: #005a9e;
                color: white;
            }
        """)
        
        # Initial placeholder tab
        placeholder = QLabel("<i>Run operations to see results here</i>")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color: #666; padding: 20px; font-size: 12px;")
        self.output_tabs.addTab(placeholder, "Info")
        
        output_layout.addWidget(self.output_tabs)
        output_group.setLayout(output_layout)
        parent_layout.addWidget(output_group, 1)  # Stretch factor = 1 to expand

    # ------------------------------------------------------------------
