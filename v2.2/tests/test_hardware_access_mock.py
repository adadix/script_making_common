"""
Tests for utils/hardware_access.py — new behaviour added in roadmap items 4 & 6.

All tests run in mock mode (no Intel toolchain required).
"""
import json
import os
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_session_guard():
    """Clear _LOADED_FUSE_RAM_PATHS before/after every test so tests are isolated."""
    import utils.hardware_access as hw
    hw._LOADED_FUSE_RAM_PATHS.clear()
    yield
    hw._LOADED_FUSE_RAM_PATHS.clear()


@pytest.fixture
def hw():
    import utils.hardware_access as _hw
    return _hw


@pytest.fixture
def bs():
    """Return the _boot_stats module (physical owner of adaptive-timeout logic)."""
    import utils._boot_stats as _bs
    return _bs


# ---------------------------------------------------------------------------
# Item 4 — session-guard (_LOADED_FUSE_RAM_PATHS) behaviour
# ---------------------------------------------------------------------------

class TestSessionGuard:
    def test_notify_adds_path(self, hw):
        hw.notify_fuse_ram_loaded('cdie.fuses')
        assert 'cdie.fuses' in hw._LOADED_FUSE_RAM_PATHS

    def test_is_loaded_exact_match(self, hw):
        hw.notify_fuse_ram_loaded('cdie.fuses')
        assert hw._is_fuse_ram_already_loaded('cdie.fuses')

    def test_is_loaded_child_path(self, hw):
        """If 'cdie.fuses' is loaded, child paths count as loaded too."""
        hw.notify_fuse_ram_loaded('cdie.fuses')
        assert hw._is_fuse_ram_already_loaded('cdie.fuses.punit_fuses')
        assert hw._is_fuse_ram_already_loaded('cdie.fuses.punit_fuses.some_reg')

    def test_is_loaded_unrelated_path_returns_false(self, hw):
        hw.notify_fuse_ram_loaded('cdie.fuses')
        assert not hw._is_fuse_ram_already_loaded('soc.fuses')

    def test_is_loaded_prefix_without_dot_returns_false(self, hw):
        """'cdie.fuses_extended' should NOT match guard for 'cdie.fuses'."""
        hw.notify_fuse_ram_loaded('cdie.fuses')
        assert not hw._is_fuse_ram_already_loaded('cdie.fuses_extended')

    def test_flush_discards_from_guard(self, hw, tmp_path, monkeypatch):
        """
        After flush_fuse_ram() succeeds (mock path), the fuse_ram_path
        must be removed from _LOADED_FUSE_RAM_PATHS so the next
        load_fuse_ram() re-reads from hardware (Item 4 fix).
        """
        # Pre-register the path to simulate a previous load
        frp = 'cdie.fuses'
        hw.notify_fuse_ram_loaded(frp)
        assert hw._is_fuse_ram_already_loaded(frp)

        # Domain info that the mock path will use
        domain_info = {
            'fuse_path': 'cdie.fuses.domain',
            'fuse_ram_path': frp,
            'vf_voltage': ['v0', 'v1'],
            'wp_count': 2,
        }

        # In mock mode flush_fuse_ram calls load_fuse_ram_once internally;
        # all we need to verify is that after the call the guard is cleared.
        # Activate mock mode first.
        hw.MOCK_MODE = True
        try:
            hw.flush_fuse_ram(domain_info)
        except Exception:
            pass   # mock may partially raise; we only care about the guard
        finally:
            hw.MOCK_MODE = False

        assert not hw._is_fuse_ram_already_loaded(frp), (
            "flush_fuse_ram must discard frp from _LOADED_FUSE_RAM_PATHS "
            "so the next load_fuse_ram() re-reads from hardware"
        )

    def test_reset_target_clears_guard(self, hw, monkeypatch):
        """reset_target() must clear _LOADED_FUSE_RAM_PATHS (Item 4 fix)."""
        hw.notify_fuse_ram_loaded('cdie.fuses')
        hw.notify_fuse_ram_loaded('soc.fuses')

        # Stub out the three internal calls that need real hardware
        monkeypatch.setattr(hw, 'ENABLE_SUT_VERIFICATION', False)

        # Provide a fake itp with resettarget() so the reset path can run
        class _FakeItp:
            def resettarget(self):
                pass
        monkeypatch.setattr(hw, 'itp', _FakeItp())

        try:
            hw.reset_target(wait_for_boot=False, boot_timeout=0)
        except Exception:
            pass   # any error in post-reset steps is fine

        assert len(hw._LOADED_FUSE_RAM_PATHS) == 0, (
            "reset_target() must clear _LOADED_FUSE_RAM_PATHS after itp.resettarget()"
        )


# ---------------------------------------------------------------------------
# Item 6 — adaptive boot-timeout helpers
# NOTE: functions physically live in utils._boot_stats; monkeypatch must
#       target that module's _BOOT_STATS_PATH to intercept file I/O.
# ---------------------------------------------------------------------------

class TestAdaptiveBootTimeout:
    def test_default_when_no_samples(self, bs, tmp_path, monkeypatch):
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', tmp_path / 'boot_stats.json')
        timeout = bs.get_adaptive_boot_timeout(default=300)
        assert timeout == 300

    def test_default_when_fewer_than_3_samples(self, bs, tmp_path, monkeypatch):
        stats_path = tmp_path / 'boot_stats.json'
        stats_path.write_text(json.dumps({'times': [90.0, 110.0], 'p90': 120}))
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', stats_path)
        timeout = bs.get_adaptive_boot_timeout(default=300)
        assert timeout == 300

    def test_uses_p90_when_enough_samples(self, bs, tmp_path, monkeypatch):
        stats_path = tmp_path / 'boot_stats.json'
        # p90 stored as 180 (already includes safety margin from record_boot_time)
        stats_path.write_text(json.dumps({
            'times': [80.0, 90.0, 100.0, 110.0, 120.0],
            'p90': 180,
        }))
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', stats_path)
        timeout = bs.get_adaptive_boot_timeout(default=300)
        assert timeout == 180   # uses stored p90

    def test_never_below_half_default(self, bs, tmp_path, monkeypatch):
        stats_path = tmp_path / 'boot_stats.json'
        # Artificially small p90
        stats_path.write_text(json.dumps({
            'times': [10.0, 11.0, 12.0, 13.0, 14.0],
            'p90': 5,
        }))
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', stats_path)
        timeout = bs.get_adaptive_boot_timeout(default=300)
        assert timeout >= 150   # floor is default // 2 = 150

    def test_record_boot_time_persists(self, bs, tmp_path, monkeypatch):
        stats_path = tmp_path / 'boot_stats.json'
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', stats_path)
        bs.record_boot_time(95.0)
        stats = json.loads(stats_path.read_text())
        assert 95.0 in stats['times']
        assert 'p90' in stats

    def test_record_boot_time_truncates_to_50(self, bs, tmp_path, monkeypatch):
        stats_path = tmp_path / 'boot_stats.json'
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', stats_path)
        for i in range(60):
            bs.record_boot_time(float(i + 50))
        stats = json.loads(stats_path.read_text())
        assert len(stats['times']) == 50

    def test_record_boot_time_p90_includes_margin(self, bs, tmp_path, monkeypatch):
        stats_path = tmp_path / 'boot_stats.json'
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', stats_path)
        # 10 identical 100s boots → P90 = 100, stored as 100+60=160
        for _ in range(10):
            bs.record_boot_time(100.0)
        stats = json.loads(stats_path.read_text())
        assert stats['p90'] == 160   # 100 + 60 s margin

    def test_load_stats_returns_default_on_missing_file(self, bs, tmp_path, monkeypatch):
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', tmp_path / 'missing.json')
        stats = bs._load_boot_stats()
        assert stats == {'times': [], 'p90': 300}

    def test_load_stats_returns_default_on_corrupt_json(self, bs, tmp_path, monkeypatch):
        bad = tmp_path / 'corrupt.json'
        bad.write_text('NOT JSON {{{{')
        monkeypatch.setattr(bs, '_BOOT_STATS_PATH', bad)
        stats = bs._load_boot_stats()
        assert stats == {'times': [], 'p90': 300}
