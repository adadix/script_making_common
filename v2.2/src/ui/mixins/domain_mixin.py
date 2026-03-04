"""DomainMixin — domain selection sidebar and selection-state helpers."""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QGroupBox, QVBoxLayout, QScrollArea, QWidget, QLabel, QPushButton,
    QCheckBox, QSizePolicy,
)
from PyQt5.QtCore import Qt


class DomainMixin:
    """Provides the scrollable domain-selection sidebar and all state helpers
    that track which domains the user has checked."""

    # ── Selected-domains display bar ─────────────────────────────────────────

    def create_selected_domains_display(self, parent_layout):
        """Create selected domains display at the top."""
        selected_group = QGroupBox("Selected Domains")
        selected_group.setStyleSheet("""
            QGroupBox {
                background: #f0f8ff;
                border: 2px solid #0071c5;
                border-radius: 6px;
                font-weight: bold;
                padding-top: 15px;
                margin: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        selected_layout = QVBoxLayout()

        self.selected_domains_label = QLabel("<i>No domains selected</i>")
        self.selected_domains_label.setWordWrap(True)
        self.selected_domains_label.setStyleSheet("padding: 5px; font-size: 12px;")
        selected_layout.addWidget(self.selected_domains_label)

        selected_group.setLayout(selected_layout)
        selected_group.setMaximumHeight(80)
        parent_layout.addWidget(selected_group)

    # ── Sidebar ───────────────────────────────────────────────────────────────

    def create_domain_sidebar(self, parent_layout):
        """Create vertical scrollable domain selection sidebar."""
        domain_group = QGroupBox("Domain Selection")
        domain_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                padding-top: 15px;
                background: white;
                color: #212529;
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

        group_layout = QVBoxLayout()

        note_label = QLabel("Click to select domains:")
        note_label.setStyleSheet(
            "color: #666; font-size: 10px; font-style: italic; padding: 5px;")
        group_layout.addWidget(note_label)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                border: none; background: #f0f0f0; width: 10px; margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #0071c5; border-radius: 5px; min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(5)
        scroll_layout.setContentsMargins(5, 5, 5, 5)

        for domain in self.domains:
            domain_btn = QPushButton(domain.upper())
            domain_btn.setCheckable(True)
            domain_btn.setFixedHeight(35)
            domain_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            domain_btn.setStyleSheet("""
                QPushButton {
                    background: white; border: 2px solid #0071c5;
                    border-radius: 4px; color: #212529;
                    font-size: 10px; font-weight: bold;
                    padding: 5px; text-align: center;
                }
                QPushButton:hover { background: #e8f4fd; }
                QPushButton:checked {
                    background: #0071c5; color: white; border: 2px solid #005a9e;
                }
            """)
            domain_btn.clicked.connect(
                lambda checked, d=domain, btn=domain_btn:
                self.on_domain_button_clicked(d, btn))

            enable_checkbox = QCheckBox()
            enable_checkbox.setChecked(False)
            enable_checkbox.setVisible(False)
            self.domain_checkboxes[domain] = enable_checkbox
            self.domain_buttons[domain] = domain_btn

            scroll_layout.addWidget(domain_btn)

        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_widget)
        group_layout.addWidget(scroll_area)

        domain_group.setLayout(group_layout)
        parent_layout.addWidget(domain_group)

    # ── Selection state helpers ───────────────────────────────────────────────

    def on_domain_button_clicked(self, domain, button):
        """Handle clicking on a domain button to toggle selection."""
        checkbox = self.domain_checkboxes[domain]
        checkbox.setChecked(button.isChecked())
        self.update_selected_domains_display()

    def update_selected_domains_display(self):
        """Update the selected domains display box."""
        selected = [d.upper() for d, cb in self.domain_checkboxes.items()
                    if cb.isChecked()]

        if selected:
            self.selected_domains_label.setText(
                f"<b>Selected:</b> {', '.join(selected)} <b>({len(selected)} domains)</b>"
            )
            self.selected_domains_label.setStyleSheet(
                "padding: 5px; font-size: 12px; color: #0071c5;")
        else:
            self.selected_domains_label.setText("<i>No domains selected</i>")
            self.selected_domains_label.setStyleSheet(
                "padding: 5px; font-size: 12px; color: #999;")

    def get_selected_domains(self):
        """Return list of selected domain names."""
        return [d for d, cb in self.domain_checkboxes.items() if cb.isChecked()]
