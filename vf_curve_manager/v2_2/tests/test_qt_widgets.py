"""pytest-qt tests for worker threads, tab builders, and dialogs."""
import sys, os, pytest

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from unittest.mock import MagicMock, patch
import pandas as pd
from PyQt5.QtWidgets import QWidget, QTableWidget

from ui.workers import BumpWorkerThread, CustomizeWorkerThread
from ui.tabs.result_tabs import (
    _create_result_tab, _create_cumulative_tab, _create_bump_result_tab
)
from ui.tabs.registers_tab import build_registers_tab_widget
from ui.dialogs.scalar_modifiers import ScalarModifiersDialog

# ─── helpers ──────────────────────────────────────────────────────────────

def _make_engine(bump_return=None, customize_return=None):
    engine = MagicMock()
    engine.bump_voltages.return_value = bump_return or {'data': {}}
    engine.customize_frequency.return_value = customize_return or {'data': {}}
    engine.config_loader.get_scalar_modifiers.return_value = {}
    return engine

def _sample_df():
    return pd.DataFrame({
        'WP': [0, 1, 2],
        'Voltage_mV': [1200.0, 1150.0, 1100.0],
        'Frequency_MHz': [3600, 3200, 2800],
    })

def _sample_records():
    return [
        {
            'name': 'reg_cpu',
            'value': 255,
            'hex': '0xff',
            'active': True,
            'domain': 'cpu',
            'category': 'wp',
            'fuse_path': 'cdie.reg_cpu',
            'description': 'test register',
        }
    ]

# ─── BumpWorkerThread ─────────────────────────────────────────────────────

class TestBumpWorkerThread:
    def test_emits_finished_on_success(self, qtbot):
        engine = _make_engine(bump_return={'data': {'cpu': 1200}})
        w = BumpWorkerThread(engine, ['cpu'], 10, 'up')
        with qtbot.waitSignal(w.finished, timeout=5000) as sig:
            w.start()
        assert isinstance(sig.args[0], dict)
        w.wait(2000)

    def test_emits_error_on_exception(self, qtbot):
        engine = _make_engine()
        engine.bump_voltages.side_effect = RuntimeError('hw failure')
        w = BumpWorkerThread(engine, [], 10, 'up')
        with qtbot.waitSignal(w.error, timeout=5000) as sig:
            w.start()
        assert 'hw failure' in sig.args[0]
        w.wait(2000)

    def test_emits_error_when_result_has_error_key(self, qtbot):
        engine = _make_engine(bump_return={'error': 'voltage out of range'})
        w = BumpWorkerThread(engine, ['cpu'], 5, 'down')
        with qtbot.waitSignal(w.error, timeout=5000) as sig:
            w.start()
        assert 'voltage out of range' in sig.args[0]
        w.wait(2000)

    def test_direction_passed_to_engine(self, qtbot):
        engine = _make_engine()
        w = BumpWorkerThread(engine, ['cpu'], 10, 'down')
        with qtbot.waitSignal(w.finished, timeout=5000):
            w.start()
        engine.bump_voltages.assert_called_once_with(['cpu'], 10, 'down')
        w.wait(2000)

# ─── CustomizeWorkerThread ─────────────────────────────────────────────────

class TestCustomizeWorkerThread:
    def test_emits_finished_on_success(self, qtbot):
        engine = _make_engine(customize_return={'data': {}})
        w = CustomizeWorkerThread(engine, 'cpu', {'p0': 3600, 'p1': 3200, 'pn': 800})
        with qtbot.waitSignal(w.finished, timeout=5000):
            w.start()
        w.wait(2000)

    def test_emits_error_on_exception(self, qtbot):
        engine = _make_engine()
        engine.customize_frequency.side_effect = ValueError('bad freq')
        w = CustomizeWorkerThread(engine, 'cpu', {})
        with qtbot.waitSignal(w.error, timeout=5000) as sig:
            w.start()
        assert 'bad freq' in sig.args[0]
        w.wait(2000)

    def test_domain_and_freqs_forwarded(self, qtbot):
        engine = _make_engine()
        freqs = {'p0': 3800, 'p1': 3000, 'pn': 800}
        w = CustomizeWorkerThread(engine, 'gpu', freqs)
        with qtbot.waitSignal(w.finished, timeout=5000):
            w.start()
        engine.customize_frequency.assert_called_once_with('gpu', freqs)
        w.wait(2000)

# ─── result_tabs builders ─────────────────────────────────────────────────

class TestCreateResultTab:
    def test_returns_qwidget(self, qapp):
        tab = _create_result_tab(_sample_df(), None, None)
        assert isinstance(tab, QWidget)

    def test_returns_qwidget_with_excel_path(self, qapp, tmp_path):
        tab = _create_result_tab(_sample_df(), str(tmp_path / 'out.xlsx'), None)
        assert isinstance(tab, QWidget)

class TestCreateCumulativeTab:
    def test_returns_qwidget(self, qapp):
        tab = _create_cumulative_tab(None, None)
        assert isinstance(tab, QWidget)

class TestCreateBumpResultTab:
    def test_returns_qwidget(self, qapp):
        tab = _create_bump_result_tab(
            _sample_df(), _sample_df(), None, None,
            {'success': True, 'details': ''}
        )
        assert isinstance(tab, QWidget)

    def test_both_dfs_shown(self, qapp):
        from PyQt5.QtWidgets import QScrollArea
        tab = _create_bump_result_tab(
            _sample_df(), _sample_df(), None, None,
            {'success': False, 'details': 'mismatch'}
        )
        # bump tab uses QLabel+QScrollArea for before/after, not QTableWidget
        scrolls = tab.findChildren(QScrollArea)
        assert len(scrolls) >= 1

# ─── registers_tab builder ────────────────────────────────────────────────

class TestBuildRegistersTabWidget:
    def test_returns_qwidget(self, qapp):
        w = build_registers_tab_widget(_sample_records(), 'TestPlatform', '2024-01-01')
        assert isinstance(w, QWidget)

    def test_contains_table(self, qapp):
        w = build_registers_tab_widget(_sample_records(), 'TestPlatform', '2024-01-01')
        tables = w.findChildren(QTableWidget)
        assert len(tables) >= 1

    def test_empty_records(self, qapp):
        w = build_registers_tab_widget([], 'NoPlatform', '2024-01-01')
        assert isinstance(w, QWidget)

# ─── ScalarModifiersDialog ────────────────────────────────────────────────

class TestScalarModifiersDialog:
    def test_instantiates_with_mock_engine(self, qapp):
        engine = _make_engine()
        dlg = ScalarModifiersDialog(engine)
        assert dlg is not None

    def test_has_table_attribute(self, qapp):
        engine = _make_engine()
        dlg = ScalarModifiersDialog(engine)
        assert hasattr(dlg, 'table')

    def test_table_is_qtablewidget(self, qapp):
        engine = _make_engine()
        dlg = ScalarModifiersDialog(engine)
        assert isinstance(dlg.table, QTableWidget)

    def test_get_scalars_delegates_to_engine(self, qapp):
        engine = _make_engine()
        engine.config_loader.get_scalar_modifiers.return_value = {
            'cpu_s1': {'name': 's1', 'value': 1.1, 'type': 'linear', 'enabled': True}
        }
        dlg = ScalarModifiersDialog(engine)
        scalars = dlg._get_scalars()
        assert isinstance(scalars, dict)
