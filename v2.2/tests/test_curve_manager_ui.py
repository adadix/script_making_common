"""Integration tests for CurveManagerUI — the main application window."""
import sys, os, pytest
from unittest.mock import MagicMock, patch

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PyQt5.QtWidgets import QWidget, QCheckBox, QTabWidget, QLabel
from ui.curve_manager_ui import CurveManagerUI


# ─── shared fixture ────────────────────────────────────────────────────────

@pytest.fixture
def ui(qapp, mock_curve_engine, config_loader):
    """Instantiate CurveManagerUI with real config_loader and mock engine."""
    window = CurveManagerUI(mock_curve_engine, config_loader)
    yield window
    window.close()


# ─── construction ─────────────────────────────────────────────────────────

class TestConstruction:
    def test_is_qwidget(self, ui):
        assert isinstance(ui, QWidget)

    def test_window_title_set(self, ui):
        assert 'VF Curve Manager' in ui.windowTitle()

    def test_has_domain_checkboxes(self, ui):
        assert isinstance(ui.domain_checkboxes, dict)
        assert len(ui.domain_checkboxes) > 0

    def test_domain_checkboxes_are_qcheckboxes(self, ui):
        for cb in ui.domain_checkboxes.values():
            assert isinstance(cb, QCheckBox)

    def test_has_curve_engine(self, ui, mock_curve_engine):
        assert ui.curve_engine is mock_curve_engine

    def test_has_config_loader(self, ui, config_loader):
        assert ui.config_loader is config_loader

    def test_dark_theme_default_false(self, ui):
        assert ui.dark_theme is False

    def test_has_sut_verification_checkbox(self, ui):
        assert hasattr(ui, 'sut_verification_checkbox')


# ─── get_selected_domains ─────────────────────────────────────────────────

class TestGetSelectedDomains:
    def test_returns_list(self, ui):
        result = ui.get_selected_domains()
        assert isinstance(result, list)

    def test_empty_when_none_checked(self, ui):
        for cb in ui.domain_checkboxes.values():
            cb.setChecked(False)
        assert ui.get_selected_domains() == []

    def test_returns_checked_domains(self, ui):
        for cb in ui.domain_checkboxes.values():
            cb.setChecked(False)
        first_domain = next(iter(ui.domain_checkboxes))
        ui.domain_checkboxes[first_domain].setChecked(True)
        assert ui.get_selected_domains() == [first_domain]

    def test_all_selected(self, ui):
        for cb in ui.domain_checkboxes.values():
            cb.setChecked(True)
        assert set(ui.get_selected_domains()) == set(ui.domain_checkboxes.keys())


# ─── _set_status ─────────────────────────────────────────────────────────

class TestSetStatus:
    def test_accepts_ok_level(self, ui):
        ui._set_status('Status: Ready', level='ok')  # should not raise

    def test_accepts_warning_level(self, ui):
        ui._set_status('Warning: something', level='warning')

    def test_accepts_error_level(self, ui):
        ui._set_status('Error: failed', level='error')

    def test_accepts_plain_string(self, ui):
        ui._set_status('Some status message')


# ─── toggle_theme ─────────────────────────────────────────────────────────

class TestToggleTheme:
    def test_toggles_dark_theme_flag(self, ui):
        initial = ui.dark_theme
        ui.toggle_theme()
        assert ui.dark_theme is not initial

    def test_double_toggle_restores(self, ui):
        initial = ui.dark_theme
        ui.toggle_theme()
        ui.toggle_theme()
        assert ui.dark_theme is initial


# ─── open_scalar_modifiers_dialog ─────────────────────────────────────────

class TestScalarModifiersDialog:
    def test_opens_dialog_with_mock_engine(self, ui, qapp):
        from PyQt5.QtWidgets import QDialog
        # Auto-reject so the dialog doesn't block
        with patch.object(QDialog, 'exec_', return_value=0):
            ui.open_scalar_modifiers_dialog()  # should not raise


# ─── structural / widget tree ────────────────────────────────────────────

class TestWidgetStructure:
    def test_has_tab_widget(self, ui):
        tabs = ui.findChildren(QTabWidget)
        assert len(tabs) >= 1

    def test_minimum_size_set(self, ui):
        assert ui.minimumWidth() >= 900
        assert ui.minimumHeight() >= 600
