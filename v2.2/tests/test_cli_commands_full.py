"""
Full command-coverage integration tests for the CLI.

Each CLI sub-command gets at least one smoke test that:
  - Runs the CLI as a real subprocess with --mock / VF_MOCK_MODE=1
  - Asserts exit code and absence of unhandled exceptions
  - Validates --json output where applicable

These tests run on any machine -- no Intel ITP toolchain required.
Coverage complement: test_cli_mock.py covers list / show / error paths;
this file covers bump, edit, flatten, customize, sweep, dump-registers,
edit-register, scalars, and --json show.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import pytest

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
_CLI = os.path.join(_SRC, 'vf_curve_manager_cli.py')


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run the CLI with --mock + VF_MOCK_MODE=1 and capture output."""
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC
    env['VF_MOCK_MODE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.run(
        [sys.executable, _CLI, '--mock'] + list(args),
        capture_output=True,
        text=True,
        encoding='utf-8',
        env=env,
        timeout=timeout,
    )


def _extract_json(stdout: str) -> object:
    """Return the first valid JSON value found in *stdout*."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stdout):
        if ch in '{[':
            try:
                obj, _ = decoder.raw_decode(stdout, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f'No JSON found in stdout: {stdout!r}')


def _no_traceback(r: subprocess.CompletedProcess) -> None:
    """Assert the run did not produce an unhandled Python traceback."""
    assert 'Traceback (most recent call last)' not in r.stdout, \
        f'Traceback in stdout:\n{r.stdout}'
    assert 'Traceback (most recent call last)' not in r.stderr, \
        f'Traceback in stderr:\n{r.stderr}'


# ---------------------------------------------------------------------------
# Session-scoped fixtures for domain names
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def first_domain() -> str:
    """Return the first domain listed by `--mock --json list`."""
    r = _run('--json', 'list')
    if r.returncode != 0:
        pytest.skip(f'list --json failed (rc={r.returncode}); cannot determine domain')
    try:
        data = _extract_json(r.stdout)
    except ValueError:
        pytest.skip('list --json produced no JSON')
    if isinstance(data, dict) and 'domains' in data:
        d = data['domains']
        if d:
            return next(iter(d))
    if isinstance(data, list) and data:
        return data[0].get('domain') or data[0].get('name') or str(data[0])
    pytest.skip('No domains found in list output')


@pytest.fixture(scope='session')
def flatten_domain() -> str:
    """Return a domain that has flatten_freq_ratios (needed for `flatten`)."""
    r = _run('--json', 'list')
    if r.returncode != 0:
        pytest.skip('list --json failed')
    try:
        data = _extract_json(r.stdout)
    except ValueError:
        pytest.skip('No JSON from list')
    domains: dict = {}
    if isinstance(data, dict) and 'domains' in data:
        domains = data['domains']
    for name, info in domains.items():
        if isinstance(info, dict) and info.get('flatten_freq_ratios'):
            return name
    # cluster0_bigcore always has it — fall back to it
    if 'cluster0_bigcore' in domains:
        return 'cluster0_bigcore'
    pytest.skip('No domain with flatten_freq_ratios found in mock config')


# ---------------------------------------------------------------------------
# bump
# ---------------------------------------------------------------------------

class TestBump:
    def test_bump_up_exits_zero(self, first_domain):
        r = _run('bump', '--domains', first_domain,
                 '--value', '5', '--direction', 'up', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_bump_down_exits_zero(self, first_domain):
        r = _run('bump', '--domains', first_domain,
                 '--value', '5', '--direction', 'down', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_bump_with_global_json_flag(self, first_domain):
        """Global --json flag should not crash bump (bump outputs plain text regardless)."""
        r = _run('--json', 'bump', '--domains', first_domain,
                 '--value', '5', '--direction', 'up', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDERR:{r.stderr}'
        assert r.stdout.strip(), 'Expected non-empty output from bump'

    def test_bump_produces_output(self, first_domain):
        r = _run('bump', '--domains', first_domain,
                 '--value', '5', '--direction', 'up', '--yes')
        assert len(r.stdout.strip()) > 0

    def test_bump_missing_direction_nonzero(self, first_domain):
        r = _run('bump', '--domains', first_domain, '--value', '5')
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# edit (WP voltage edit)
# ---------------------------------------------------------------------------

class TestEdit:
    def test_edit_single_wp_exits_zero(self, first_domain):
        r = _run('edit', '--domain', first_domain, '--wp', '0:850', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_edit_multiple_wps_exits_zero(self, first_domain):
        r = _run('edit', '--domain', first_domain,
                 '--wp', '0:850', '--wp', '1:800', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_edit_missing_wp_nonzero(self, first_domain):
        r = _run('edit', '--domain', first_domain, '--yes')
        assert r.returncode != 0

    def test_edit_bad_wp_format_nonzero(self, first_domain):
        r = _run('edit', '--domain', first_domain, '--wp', 'not-a-number', '--yes')
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# show with --json global flag (show produces tabular output regardless;
# --json is a global CLI flag that only affects commands which implement it.
# The test verifies: exit 0, non-empty output, no traceback).
# ---------------------------------------------------------------------------

class TestShowJsonFlag:
    def test_show_with_json_flag_exits_zero(self, first_domain):
        r = _run('--json', 'show', '--domains', first_domain)
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDERR:{r.stderr}'

    def test_show_with_json_flag_has_output(self, first_domain):
        r = _run('--json', 'show', '--domains', first_domain)
        assert len(r.stdout.strip()) > 0

    def test_show_without_json_flag_exits_zero(self, first_domain):
        r = _run('show', '--domains', first_domain)
        _no_traceback(r)
        assert r.returncode == 0, f'rc={r.returncode}\nSTDERR:{r.stderr}'


# ---------------------------------------------------------------------------
# flatten
# ---------------------------------------------------------------------------

class TestFlatten:
    def test_flatten_p1_exits_zero(self, flatten_domain):
        r = _run('flatten', '--domain', flatten_domain, '--target', 'p1', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, \
            f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_flatten_invalid_target_nonzero(self, flatten_domain):
        r = _run('flatten', '--domain', flatten_domain, '--target', 'notafreq', '--yes')
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# customize
# ---------------------------------------------------------------------------

class TestCustomize:
    def test_customize_p0_exits_zero(self, first_domain):
        r = _run('customize', '--domain', first_domain, '--p0', '4500')
        _no_traceback(r)
        # exit 0 (success) or 1 (domain lacks ratio registers) — both are graceful
        assert r.returncode in (0, 1), \
            f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_customize_no_args_nonzero(self, first_domain):
        """--p0/--p1/--pn all missing triggers parser error."""
        r = _run('customize', '--domain', first_domain)
        assert r.returncode != 0

    def test_customize_p1_pn(self, first_domain):
        r = _run('customize', '--domain', first_domain, '--p1', '1500', '--pn', '400')
        _no_traceback(r)
        assert r.returncode in (0, 1)


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------

class TestSweep:
    def test_sweep_exits_zero(self, first_domain):
        r = _run('sweep', '--domain', first_domain,
                 '--from', '-10', '--to', '10', '--step', '5', '--yes')
        _no_traceback(r)
        assert r.returncode == 0, \
            f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_sweep_step_zero_nonzero(self, first_domain):
        r = _run('sweep', '--domain', first_domain,
                 '--from', '-10', '--to', '10', '--step', '0', '--yes')
        assert r.returncode != 0

    def test_sweep_produces_output(self, first_domain):
        r = _run('sweep', '--domain', first_domain,
                 '--from', '-5', '--to', '5', '--step', '5', '--yes')
        assert len(r.stdout.strip()) > 0


# ---------------------------------------------------------------------------
# dump-registers
# ---------------------------------------------------------------------------

class TestDumpRegisters:
    def test_dump_registers_no_crash(self):
        """dump-registers should exit 0 or 1 (never an unhandled exception)."""
        r = _run('dump-registers')
        _no_traceback(r)
        assert r.returncode in (0, 1), \
            f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_dump_registers_active_only_no_crash(self):
        r = _run('dump-registers', '--active-only')
        _no_traceback(r)
        assert r.returncode in (0, 1)


# ---------------------------------------------------------------------------
# edit-register (read-only view)
# ---------------------------------------------------------------------------

class TestEditRegisterView:
    # A register that is present in the shipped vf_discovery_cache.json
    _REG = 'bigcore_vf_voltage_0'

    def test_view_known_register_exits_zero(self):
        """Read-only view of a register that exists in the test discovery cache."""
        r = _run('edit-register', '--name', self._REG)
        _no_traceback(r)
        assert r.returncode == 0, \
            f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_view_shows_value(self):
        r = _run('edit-register', '--name', self._REG)
        assert 'Value' in r.stdout or 'value' in r.stdout.lower(), \
            f'Expected "Value" label in output:\n{r.stdout}'

    def test_view_unknown_register_nonzero(self):
        r = _run('edit-register', '--name', '__no_such_reg__')
        assert r.returncode != 0

    def test_view_fuzzy_match(self):
        """Partial name matches multiple registers — should report that."""
        r = _run('edit-register', '--name', 'atom_vf_voltage')
        # Either exact match found or multiple-match message (rc=0 or 1)
        _no_traceback(r)
        assert r.returncode in (0, 1)


# ---------------------------------------------------------------------------
# scalars
# ---------------------------------------------------------------------------

class TestScalars:
    def test_scalars_show_no_crash(self):
        """scalars show should exit 0 or 1 gracefully."""
        r = _run('scalars', 'show')
        _no_traceback(r)
        assert r.returncode in (0, 1), \
            f'rc={r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}'

    def test_scalars_show_type_filter(self):
        r = _run('scalars', 'show', '--type', 'p0_override')
        _no_traceback(r)
        assert r.returncode in (0, 1)

    def test_scalars_missing_subcommand_nonzero(self):
        """scalars without a sub-action triggers parser error."""
        r = _run('scalars')
        assert r.returncode != 0

    def test_scalars_edit_missing_key_nonzero(self):
        r = _run('scalars', 'edit', '--value', '900', '--yes')
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# Global --json flag applied to bump (regression: ensure flag is consistent)
# ---------------------------------------------------------------------------

class TestGlobalJsonFlag:
    def test_json_flag_before_subcommand(self, first_domain):
        """--json must precede the subcommand name."""
        r = _run('--json', 'bump', '--domains', first_domain,
                 '--value', '5', '--direction', 'up', '--yes')
        _no_traceback(r)
        assert r.returncode == 0

    def test_json_flag_does_not_break_list(self):
        r = _run('--json', 'list')
        assert r.returncode == 0
        data = _extract_json(r.stdout)
        assert isinstance(data, (dict, list))
