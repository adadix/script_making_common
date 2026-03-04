"""ThemeMixin — header / footer / status-bar / button-style helpers."""
from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt, QTimer


class ThemeMixin:
    """Provides Intel-branded header/footer, light/dark themes, and reusable
    button-style class variables for all CurveManagerUI sub-classes."""

    # ── Reusable button style sheets ────────────────────────────────────────
    _BTN_PRIMARY = (
        'QPushButton { background: #0071c5; color: white; border: none; '
        'border-radius: 4px; font-size: 14px; font-weight: bold; }'
        'QPushButton:hover { background: #005a9e; }'
    )
    _BTN_ACTION = (
        'QPushButton { background: #28a745; color: white; border: none; '
        'border-radius: 4px; font-size: 12px; font-weight: bold; }'
        'QPushButton:hover { background: #218838; }'
    )
    _BTN_DANGER = (
        'QPushButton { background: #dc3545; color: white; border: none; '
        'border-radius: 4px; font-size: 12px; font-weight: bold; }'
        'QPushButton:hover { background: #c82333; }'
    )
    _BTN_SECONDARY = (
        'QPushButton { background: #6c757d; color: white; border: none; '
        'border-radius: 4px; font-size: 12px; font-weight: bold; }'
        'QPushButton:hover { background: #5a6268; }'
    )
    _BTN_INFO = (
        'QPushButton { background: #17a2b8; color: white; border: none; '
        'border-radius: 4px; font-size: 12px; font-weight: bold; }'
        'QPushButton:hover { background: #138496; }'
    )
    _BTN_PURPLE = (
        'QPushButton { background: #6f42c1; color: white; border: none; '
        'border-radius: 4px; font-size: 12px; font-weight: bold; }'
        'QPushButton:hover { background: #5a379e; }'
        'QPushButton:disabled { background: #adb5bd; color: #6c757d; }'
    )
    _BTN_DIALOG_APPLY = (
        'QPushButton { background-color: #0071c5; color: white; font-weight: bold; '
        'padding: 8px 16px; border-radius: 4px; }'
        'QPushButton:hover { background-color: #005a9e; }'
    )
    _BTN_DIALOG_CANCEL = (
        'QPushButton { background-color: #6c757d; color: white; '
        'padding: 8px 16px; border-radius: 4px; }'
        'QPushButton:hover { background-color: #5a6268; }'
    )

    # ── Status colour map ────────────────────────────────────────────────────
    _STATUS_COLOR = {'ok': '#90EE90', 'busy': '#ffc107', 'error': '#ff4444'}

    # ── Header ───────────────────────────────────────────────────────────────

    def create_header(self, parent_layout):
        """Create professional header with Intel branding."""
        header_frame = QWidget()
        header_frame.setFixedHeight(80)
        header_frame.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0071c5, stop:1 #005a9e);
            }
        """)

        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(20, 10, 20, 10)

        logo_label = QLabel("Intel<sup>®</sup>")
        logo_label.setTextFormat(Qt.RichText)
        logo_label.setStyleSheet("""
            QLabel { color: white; font-size: 32px; font-weight: bold;
                     font-family: 'Arial'; background: transparent; }
        """)
        header_layout.addWidget(logo_label)

        title_label = QLabel("VF Curve Manager v2.2™ — BDC CVE Labs")
        title_label.setStyleSheet("""
            QLabel { color: white; font-size: 24px; font-weight: bold;
                     font-family: 'Segoe UI'; background: transparent; }
        """)
        header_layout.addWidget(title_label, alignment=Qt.AlignCenter)

        right_controls = QHBoxLayout()

        self.status_label = QLabel("")
        self._set_status("Status: Ready")
        right_controls.addWidget(self.status_label)

        self.theme_btn = QPushButton("Light Mode")
        self.theme_btn.setFixedSize(100, 35)
        self.theme_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.9);
                border: 1px solid rgba(255,255,255,0.5);
                border-radius: 4px; color: #0071c5;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: white; }
        """)
        self.theme_btn.clicked.connect(self.toggle_theme)
        right_controls.addWidget(self.theme_btn)

        header_layout.addLayout(right_controls)
        parent_layout.addWidget(header_frame)

    # ── Footer ───────────────────────────────────────────────────────────────

    def create_footer(self, parent_layout):
        """Create footer with copyright."""
        footer_frame = QWidget()
        footer_frame.setFixedHeight(40)
        footer_frame.setStyleSheet("""
            QWidget { background: #f8f9fa; border-top: 1px solid #dee2e6; }
        """)

        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(20, 0, 20, 0)

        footer_label = QLabel("© 2025 Intel Corporation. BDC CVE Labs™. All rights reserved.")
        footer_label.setStyleSheet("""
            QLabel { color: #6c757d; font-size: 11px; background: transparent; }
        """)
        footer_layout.addWidget(footer_label, alignment=Qt.AlignLeft)
        parent_layout.addWidget(footer_frame)

    # ── Theme toggle ────────────────────────────────────────────────────────

    def toggle_theme(self):
        """Toggle between light and dark themes."""
        self.dark_theme = not self.dark_theme
        if self.dark_theme:
            self.apply_dark_theme()
            self.theme_btn.setText("Dark Mode")
        else:
            self.apply_light_theme()
            self.theme_btn.setText("Light Mode")

    def apply_light_theme(self):
        """Apply light theme."""
        self.setStyleSheet("QWidget { background-color: white; color: #212529; }")
        for btn in self.domain_buttons.values():
            btn.setStyleSheet("""
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

    def apply_dark_theme(self):
        """Apply dark theme."""
        self.setStyleSheet("QWidget { background-color: #2b2b2b; color: #ffffff; }")
        for btn in self.domain_buttons.values():
            btn.setStyleSheet("""
                QPushButton {
                    background: #3c3c3c; border: 2px solid #0071c5;
                    border-radius: 4px; color: #ffffff;
                    font-size: 10px; font-weight: bold;
                    padding: 5px; text-align: center;
                }
                QPushButton:hover { background: #4a4a4a; }
                QPushButton:checked {
                    background: #0071c5; color: white; border: 2px solid #005a9e;
                }
            """)

    # ── Status helper ────────────────────────────────────────────────────────

    def _set_status(self, text: str, level: str = 'ok') -> None:
        """Set status label text and colour.

        Args:
            text:  Message to display.
            level: ``'ok'`` (green), ``'busy'`` (yellow), or ``'error'`` (red).
        """
        color = self._STATUS_COLOR.get(level, '#90EE90')
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f'QLabel {{ color: {color}; font-size: 14px; font-weight: bold; '
            f'background: transparent; padding: 5px 10px; }}'
        )
