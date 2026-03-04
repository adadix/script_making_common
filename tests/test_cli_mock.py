"""
CLI integration tests using --mock mode.

These tests invoke the CLI as a subprocess with --mock so they run on any
machine (no Intel ITP toolchain required).
"""
import json
import os
import sys
import subprocess
import pytest

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
_CLI = os.path.join(_SRC, 'vf_curve_manager_cli.py')


def _run(*args, timeout=60):
    """Run the CLI with --mock prepended and return the CompletedProcess."""
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC
    # Prevent hardware_access from importing ipccli/itpii (which opens OpenIPC)
    env['VF_MOCK_MODE'] = '1'
    # Ensure UTF-8 stdout so Unicode characters don't raise UnicodeEncodeError
    env['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.run(
        [sys.executable, _CLI, '--mock'] + list(args),
        capture_output=True,
        text=True,
        encoding='utf-8',
        env=env,
        timeout=timeout,
    )


def _extract_json(stdout: str):
    """
    Extract the first complete JSON object/array from stdout.
    CLI output may contain [MOCK] / [INFO] info lines before the JSON block.
    Uses json.JSONDecoder.raw_decode to find and parse the first valid JSON value.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stdout):
        if ch in '{[':
            try:
                obj, _ = decoder.raw_decode(stdout, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No JSON found in stdout: {stdout!r}")


class TestCliMockList:
    def test_list_exits_zero(self):
        r = _run('list')
        assert r.returncode == 0, f"STDERR: {r.stderr}"

    def test_list_produces_output(self):
        r = _run('list')
        assert len(r.stdout.strip()) > 0

    def test_list_json_exits_zero(self):
        # --json is a global flag — must come before the subcommand
        r = _run('--json', 'list')
        assert r.returncode == 0, f"STDERR: {r.stderr}"

    def test_list_json_is_valid_json(self):
        r = _run('--json', 'list')
        assert r.returncode == 0
        data = _extract_json(r.stdout)
        assert isinstance(data, (list, dict))


class TestCliMockShow:
    @pytest.fixture(scope='class')
    def first_domain(self):
        """Grab first domain name from --mock --json list."""
        r = _run('--json', 'list')
        if r.returncode != 0:
            pytest.skip("list --json failed; cannot determine domain name")
        data = _extract_json(r.stdout)
        # Actual format: {'status': 'ok', 'domains': {'domain_name': {...}, ...}}
        if isinstance(data, dict) and 'domains' in data:
            domains = data['domains']
            if domains:
                return next(iter(domains))
        # Fallback: plain list
        if isinstance(data, list) and data:
            return data[0].get('domain') or data[0].get('name') or data[0]
        pytest.skip("No domains found in list output")

    def test_show_exits_zero(self, first_domain):
        r = _run('show', '--domains', first_domain)
        assert r.returncode == 0, f"STDERR: {r.stderr}"


class TestCliMockErrors:
    def test_unknown_command_nonzero_exit(self):
        r = _run('__not_a_real_command__')
        assert r.returncode != 0

    def test_bump_missing_args_nonzero_exit(self):
        r = _run('bump')
        assert r.returncode != 0

    def test_customize_no_freq_nonzero_exit(self):
        r = _run('customize', '--domain', 'ring')
        assert r.returncode != 0


class TestCliMockRevertNoHistory:
    def test_revert_last_no_history(self):
        """revert-last with no prior operations should report no undo log (exit 0 or 1)."""
        r = _run('revert-last', '--yes')
        # Should not crash (exit code 0 or graceful 1 with error message, never unhandled)
        assert r.returncode in (0, 1)
        assert 'traceback' not in r.stdout.lower()
        assert 'traceback' not in r.stderr.lower()
