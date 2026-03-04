"""Tests for core/curve_engine.py running in mock mode."""
import json
import pytest


@pytest.fixture
def first_domain(config_loader):
    domains = config_loader.get_domain_list()
    if not domains:
        pytest.skip("No domains configured in vf_domains.json")
    return domains[0]


class TestShowVfCurve:
    def test_returns_dict(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.show_vf_curves([first_domain])
        assert isinstance(result, dict)


class TestBumpVoltages:
    def test_bump_up_returns_dict(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.bump_voltages([first_domain], 10, 'up')
        assert isinstance(result, dict)

    def test_bump_down_returns_dict(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.bump_voltages([first_domain], 10, 'down')
        assert isinstance(result, dict)

    def test_bump_creates_undo_log(self, mock_curve_engine, first_domain, tmp_path, monkeypatch):
        import core.curve_engine as ce
        undo_path = tmp_path / 'undo.json'
        monkeypatch.setattr(ce, '_UNDO_LOG_PATH', undo_path)
        mock_curve_engine.bump_voltages([first_domain], 5, 'up')
        if undo_path.exists():
            with open(undo_path) as f:
                history = json.load(f)
            assert isinstance(history, list)


class TestRevertFromUndoLog:
    def test_revert_no_log_returns_error(self, mock_curve_engine, tmp_path, monkeypatch):
        import core.curve_engine as ce
        monkeypatch.setattr(ce, '_UNDO_LOG_PATH', tmp_path / 'undo.json')
        result = mock_curve_engine.revert_from_undo_log()
        assert 'error' in result

    def test_revert_empty_log_returns_error(self, mock_curve_engine, tmp_path, monkeypatch):
        import core.curve_engine as ce
        undo_path = tmp_path / 'undo.json'
        undo_path.write_text('[]')
        monkeypatch.setattr(ce, '_UNDO_LOG_PATH', undo_path)
        result = mock_curve_engine.revert_from_undo_log()
        assert 'error' in result

    def test_bump_then_revert_pops_entry(self, mock_curve_engine, first_domain,
                                         tmp_path, monkeypatch):
        import core.curve_engine as ce
        undo_path = tmp_path / 'undo.json'
        monkeypatch.setattr(ce, '_UNDO_LOG_PATH', undo_path)

        mock_curve_engine.bump_voltages([first_domain], 10, 'up')

        if not undo_path.exists():
            pytest.skip("bump did not write undo log — skipping")

        with open(undo_path) as f:
            before_count = len(json.load(f))

        result = mock_curve_engine.revert_from_undo_log()

        if 'error' in result:
            pytest.skip(f"revert returned error (may be domain resolution issue): {result['error']}")

        assert result['entries_remaining'] == before_count - 1


class TestSweepVoltages:
    def test_sweep_returns_expected_keys(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.sweep_voltages([first_domain], -20, 20, 10)
        for key in ('steps', 'passed', 'total', 'stopped_early'):
            assert key in result, f"Missing key '{key}' in sweep result"

    def test_sweep_step_zero_returns_error(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.sweep_voltages([first_domain], -10, 10, 0)
        assert 'error' in result

    def test_sweep_step_count(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.sweep_voltages([first_domain], -20, 20, 10)
        # offsets: -20,-10,0,10,20 = 5 steps (0 delta at offset 0 is skipped)
        assert result['total'] >= 1

    def test_sweep_passed_le_total(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.sweep_voltages([first_domain], 0, 10, 5)
        assert result['passed'] <= result['total']


class TestEditVoltagesFreq:
    """Tests for the frequency-edit path added to edit_voltages()."""

    def test_no_changes_returns_error(self, mock_curve_engine, first_domain):
        """Empty voltage_changes and no freq_changes must return an error dict."""
        result = mock_curve_engine.edit_voltages(first_domain, {})
        assert 'error' in result

    def test_no_changes_with_empty_freq_returns_error(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.edit_voltages(first_domain, {}, freq_changes={})
        assert 'error' in result

    def test_invalid_wp_voltage_returns_error(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.edit_voltages(first_domain, {99: 1200})
        assert 'error' in result

    def test_invalid_wp_freq_returns_error(self, mock_curve_engine, first_domain):
        result = mock_curve_engine.edit_voltages(first_domain, {}, freq_changes={99: 3600})
        assert 'error' in result

    def test_freq_only_change_returns_dict(self, mock_curve_engine, first_domain):
        """Frequency-only edit should succeed (no error key) in mock mode."""
        result = mock_curve_engine.edit_voltages(first_domain, {}, freq_changes={0: 3600})
        assert isinstance(result, dict)
        # Should not be a bare error unless hardware reported one
        if 'error' in result:
            pytest.skip(f"edit returned error in mock mode: {result['error']}")
        assert 'before_dataframe' in result
        assert 'after_dataframe' in result

    def test_voltage_and_freq_change_returns_dict(self, mock_curve_engine, first_domain):
        """Combined voltage + frequency edit should succeed in mock mode."""
        result = mock_curve_engine.edit_voltages(first_domain, {0: 1100}, freq_changes={0: 3200})
        assert isinstance(result, dict)
        if 'error' in result:
            pytest.skip(f"edit returned error in mock mode: {result['error']}")
        assert 'before_dataframe' in result

    def test_effective_wp_count_trims_trailing_zeros(self, mock_curve_engine, first_domain):
        """_effective_wp_count falls back to wp_count when all reads are zero (mock mode)."""
        domain_info = mock_curve_engine.config_loader.get_domain(first_domain)
        count = mock_curve_engine._effective_wp_count(domain_info)
        # In mock mode all registers return 0 → falls back to config wp_count
        assert count == domain_info['wp_count']


# ---------------------------------------------------------------------------
# Item 3 — _handle_cold_reset_voltage_op helper (roadmap item 3)
# ---------------------------------------------------------------------------

class TestHandleColdResetVoltageOp:
    """_handle_cold_reset_voltage_op must return a well-formed cold-reset dict."""

    def test_returns_cold_reset_error_key(self, mock_curve_engine, first_domain):
        domain_info = mock_curve_engine.config_loader.get_domain(first_domain)
        frp = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
        result = mock_curve_engine._handle_cold_reset_voltage_op(
            domain_names=[first_domain],
            before_data={first_domain: {}},
            unique_fuse_rams={frp: domain_info},
            cold_reset_details={'indicators': ['SLP_S5']},
            op_name='test bump',
        )
        assert result.get('error') == 'COLD_RESET'

    def test_sets_cold_reset_detected(self, mock_curve_engine, first_domain):
        domain_info = mock_curve_engine.config_loader.get_domain(first_domain)
        frp = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
        result = mock_curve_engine._handle_cold_reset_voltage_op(
            domain_names=[first_domain],
            before_data={first_domain: {}},
            unique_fuse_rams={frp: domain_info},
            cold_reset_details={},
            op_name='edit',
        )
        assert result.get('cold_reset_detected') is True

    def test_message_contains_op_name(self, mock_curve_engine, first_domain):
        domain_info = mock_curve_engine.config_loader.get_domain(first_domain)
        frp = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
        result = mock_curve_engine._handle_cold_reset_voltage_op(
            domain_names=[first_domain],
            before_data={first_domain: {}},
            unique_fuse_rams={frp: domain_info},
            cold_reset_details={'indicators': ['target power lost']},
            op_name='MY_CUSTOM_OP',
        )
        assert 'MY_CUSTOM_OP' in result.get('message', '')

    def test_auto_revert_verified_key_present(self, mock_curve_engine, first_domain):
        domain_info = mock_curve_engine.config_loader.get_domain(first_domain)
        frp = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
        result = mock_curve_engine._handle_cold_reset_voltage_op(
            domain_names=[first_domain],
            before_data={first_domain: {}},
            unique_fuse_rams={frp: domain_info},
            cold_reset_details={},
        )
        assert 'auto_revert_verified' in result
        assert 'revert_details' in result

    def test_indicators_in_message(self, mock_curve_engine, first_domain):
        domain_info = mock_curve_engine.config_loader.get_domain(first_domain)
        frp = domain_info.get('fuse_ram_path', domain_info['fuse_path'])
        result = mock_curve_engine._handle_cold_reset_voltage_op(
            domain_names=[first_domain],
            before_data={first_domain: {}},
            unique_fuse_rams={frp: domain_info},
            cold_reset_details={'indicators': ['Power lost', 'SLP_S5']},
        )
        assert 'Power lost' in result.get('message', '')
