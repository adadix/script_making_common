"""
test_cli_integration.py — Integration tests for untested CLI commands.

Exercises show, bump, revert-last, flatten, customize, sweep and discovery
import health.  All tests run in --mock mode (no Intel ITP toolchain required).
"""
import json
import os
import sys
import subprocess
import pytest

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
_CLI = os.path.join(_SRC, 'vf_curve_manager_cli.py')

# A stable domain that always exists in the shipped vf_domains.json
_DOMAIN = 'cluster0_atom'


def _run(*args, timeout=60):
    """Run the CLI with --mock prepended.  Returns CompletedProcess."""
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


def _no_traceback(r):
    assert 'Traceback (most recent call last)' not in r.stderr, (
        f"Unexpected traceback in stderr:\n{r.stderr}"
    )


def _extract_json(stdout: str):
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stdout):
        if ch in '{[':
            try:
                obj, _ = decoder.raw_decode(stdout, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No JSON found in stdout: {stdout!r}")


# ── show ────────────────────────────────────────────────────────────────────

class TestCliIntegrationShow:
    def test_show_exits_zero(self):
        r = _run('show', '--domains', _DOMAIN)
        assert r.returncode == 0, f"STDERR: {r.stderr}"

    def test_show_no_traceback(self):
        r = _run('show', '--domains', _DOMAIN)
        _no_traceback(r)

    def test_show_contains_wp_headers(self):
        r = _run('show', '--domains', _DOMAIN)
        out = r.stdout + r.stderr
        assert 'WP' in out or 'wp' in out.lower(), \
            f"Expected WP data in output, got:\n{out[:500]}"

    def test_show_with_json_flag_exits_zero(self):
        # show emits formatted table output; --json is a global flag but show
        # does not switch to JSON mode.  The command must still exit cleanly.
        r = _run('--json', 'show', '--domains', _DOMAIN)
        assert r.returncode == 0, f"STDERR: {r.stderr}"

    def test_show_with_json_flag_contains_domain(self):
        r = _run('--json', 'show', '--domains', _DOMAIN)
        out = r.stdout + r.stderr
        assert _DOMAIN in out or 'cluster0' in out.lower(), \
            f"Expected domain name in output, got:\n{out[:500]}"

    def test_show_multiple_domains_no_crash(self):
        r = _run('show', '--domains', _DOMAIN, 'cluster0_bigcore')
        _no_traceback(r)


# ── bump ────────────────────────────────────────────────────────────────────

class TestCliIntegrationBump:
    def test_bump_up_exits_zero(self):
        r = _run('bump', '--domains', _DOMAIN, '--value', '6', '--direction', 'up', '--yes')
        assert r.returncode == 0, f"STDERR: {r.stderr}"

    def test_bump_no_traceback(self):
        r = _run('bump', '--domains', _DOMAIN, '--value', '6', '--direction', 'up', '--yes')
        _no_traceback(r)

    def test_bump_down_no_traceback(self):
        r = _run('bump', '--domains', _DOMAIN, '--value', '6', '--direction', 'down', '--yes')
        _no_traceback(r)

    def test_bump_produces_output(self):
        r = _run('bump', '--domains', _DOMAIN, '--value', '6', '--direction', 'up', '--yes')
        out = r.stdout + r.stderr
        assert len(out.strip()) > 0


# ── revert-last ─────────────────────────────────────────────────────────────

class TestCliIntegrationRevertLast:
    def test_revert_after_bump_no_traceback(self):
        # Bump first so there's something to revert
        _run('bump', '--domains', _DOMAIN, '--by', '+1')
        r = _run('revert-last')
        _no_traceback(r)

    def test_revert_exits_cleanly(self):
        r = _run('revert-last')
        # May return 0 (reverted) or non-zero (nothing to revert) — no crash
        _no_traceback(r)


# ── flatten ─────────────────────────────────────────────────────────────────

class TestCliIntegrationFlatten:
    def test_flatten_runs_no_traceback(self):
        # cluster0_atom does not support frequency flattening — exit 1 is
        # correct behaviour.  We only assert there is no Python crash.
        r = _run('flatten', '--domain', _DOMAIN, '--target', 'p0', '--yes')
        _no_traceback(r)

    def test_flatten_no_traceback(self):
        r = _run('flatten', '--domain', _DOMAIN, '--target', 'p0', '--yes')
        _no_traceback(r)


# ── sweep ───────────────────────────────────────────────────────────────────

class TestCliIntegrationSweep:
    def test_sweep_no_traceback(self):
        # Minimal sweep: 1-step range to keep CI fast
        r = _run(
            'sweep', '--domain', _DOMAIN,
            '--from', '800', '--to', '806', '--step', '6',
            '--yes', timeout=90,
        )
        _no_traceback(r)

    def test_sweep_exits_zero(self):
        r = _run(
            'sweep', '--domain', _DOMAIN,
            '--from', '800', '--to', '806', '--step', '6',
            '--yes', timeout=90,
        )
        assert r.returncode == 0, f"STDERR: {r.stderr}"


# ── customize ───────────────────────────────────────────────────────────────

class TestCliIntegrationCustomize:
    def test_customize_help_exits_zero(self):
        r = _run('customize', '--help')
        assert r.returncode == 0


# ── edit ────────────────────────────────────────────────────────────────────

class TestCliIntegrationEdit:
    def test_edit_no_traceback(self):
        # Set WP 0 to 800 mV (mock write -- no-op)
        r = _run('edit', '--domains', _DOMAIN, '--wp', '0', '--value', '800', '--yes')
        _no_traceback(r)


# ── Global --json flag coverage ─────────────────────────────────────────────

class TestCliIntegrationJsonFlag:
    def test_json_show_no_traceback(self):
        # show does not emit JSON; verify --json flag doesn't cause a crash
        r = _run('--json', 'show', '--domains', _DOMAIN)
        _no_traceback(r)
        assert r.returncode == 0, f"STDERR: {r.stderr}"

    def test_json_list_is_valid_dict(self):
        r = _run('--json', 'list')
        data = _extract_json(r.stdout)
        assert isinstance(data, dict)
        assert len(data) > 0


# ── Discovery module split health-check ─────────────────────────────────────

class TestDiscoveryModuleSplit:
    """Verify that the monolith has been correctly split into two sub-modules."""

    def test_discovery_core_importable(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = _SRC
        env['VF_MOCK_MODE'] = '1'
        r = subprocess.run(
            [sys.executable, '-c',
             'import sys; sys.path.insert(0, r"' + _SRC + '"); '
             'from discovery.discovery_core import detect_platform_name, '
             'load_discovery_cache, analyze_fuse_path; '
             'print("core OK")'],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert r.returncode == 0, r.stderr
        assert 'core OK' in r.stdout

    def test_discovery_learn_importable(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = _SRC
        env['VF_MOCK_MODE'] = '1'
        r = subprocess.run(
            [sys.executable, '-c',
             'import sys; sys.path.insert(0, r"' + _SRC + '"); '
             'from discovery.discovery_learn import build_vf_domains_from_discovery, '
             'auto_learn_unknown_patterns, run_discovery_pipeline; '
             'print("learn OK")'],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert r.returncode == 0, r.stderr
        assert 'learn OK' in r.stdout

    def test_shim_still_exports_all_symbols(self):
        """auto_discover_vf_registers must still re-export everything."""
        env = os.environ.copy()
        env['PYTHONPATH'] = _SRC
        env['VF_MOCK_MODE'] = '1'
        r = subprocess.run(
            [sys.executable, '-c',
             'import sys; sys.path.insert(0, r"' + _SRC + '"); '
             'from discovery.auto_discover_vf_registers import ('
             '    detect_platform_name, load_discovery_cache, '
             '    build_vf_domains_from_discovery, run_discovery_pipeline'
             '); print("shim OK")'],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert r.returncode == 0, r.stderr
        assert 'shim OK' in r.stdout

    def test_discovery_core_has_expected_callables(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = _SRC
        env['VF_MOCK_MODE'] = '1'
        syms = [
            'detect_platform_name', 'load_platform_config', 'discover_fuse_paths',
            'get_register_info', 'categorize_register', 'analyze_fuse_path',
            'load_discovery_cache', 'export_discovered_registers_to_excel',
        ]
        check = '; '.join(f'assert callable({s})' for s in syms)
        r = subprocess.run(
            [sys.executable, '-c',
             'import sys; sys.path.insert(0, r"' + _SRC + '"); '
             f'from discovery.discovery_core import {", ".join(syms)}; '
             f'{check}; print("callables OK")'],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert r.returncode == 0, r.stderr
        assert 'callables OK' in r.stdout

    def test_discovery_learn_has_expected_callables(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = _SRC
        env['VF_MOCK_MODE'] = '1'
        syms = [
            'auto_merge_to_vf_domains', 'auto_learn_unknown_patterns',
            'build_vf_domains_from_discovery', 'auto_discover_scalar_modifiers',
            'run_discovery_pipeline',
        ]
        check = '; '.join(f'assert callable({s})' for s in syms)
        r = subprocess.run(
            [sys.executable, '-c',
             'import sys; sys.path.insert(0, r"' + _SRC + '"); '
             f'from discovery.discovery_learn import {", ".join(syms)}; '
             f'{check}; print("learn callables OK")'],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert r.returncode == 0, r.stderr
        assert 'learn callables OK' in r.stdout
