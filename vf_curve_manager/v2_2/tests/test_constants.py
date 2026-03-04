"""
Tests for utils.constants — shared keyword lists.

Verifies that the canonical TARGET_DOWN_KEYWORDS list is intact and that
both hardware_access and auto_discover_vf_registers reference the same
object (not separate copies).
"""
import sys
import os
import pytest

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault('VF_MOCK_MODE', '1')


class TestTargetDownKeywords:
    """TARGET_DOWN_KEYWORDS in utils.constants."""

    def test_module_importable(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert isinstance(TARGET_DOWN_KEYWORDS, list)

    def test_non_empty(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert len(TARGET_DOWN_KEYWORDS) > 0

    def test_contains_0x8000000f(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert '0x8000000f' in TARGET_DOWN_KEYWORDS

    def test_contains_target_is_powered_down(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert 'target is powered down' in TARGET_DOWN_KEYWORDS

    def test_contains_dci_device_gone(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert 'dci: device gone' in TARGET_DOWN_KEYWORDS

    def test_all_strings(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert all(isinstance(k, str) for k in TARGET_DOWN_KEYWORDS)

    def test_all_lowercase(self):
        """Keywords must be lowercase for case-insensitive matching via 'in str'."""
        from utils.constants import TARGET_DOWN_KEYWORDS
        for kw in TARGET_DOWN_KEYWORDS:
            assert kw == kw.lower(), f"Keyword not lowercase: {kw!r}"

    def test_no_duplicates(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        assert len(TARGET_DOWN_KEYWORDS) == len(set(TARGET_DOWN_KEYWORDS))


class TestHardwareAccessUsesConstants:
    """hardware_access._TARGET_DOWN_KEYWORDS must be the same list as constants."""

    def test_same_contents_as_constants(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        import utils.hardware_access as ha
        assert set(ha._TARGET_DOWN_KEYWORDS) == set(TARGET_DOWN_KEYWORDS)

    def test_attribute_exists(self):
        import utils.hardware_access as ha
        assert hasattr(ha, '_TARGET_DOWN_KEYWORDS')


class TestDiscoveryUsesConstants:
    """auto_discover_vf_registers._DISCOVERY_TARGET_DOWN_KW must match constants."""

    def test_same_contents_as_constants(self):
        from utils.constants import TARGET_DOWN_KEYWORDS
        # Import the discovery module (mock mode avoids Intel toolchain)
        import discovery.auto_discover_vf_registers as adr
        assert set(adr._DISCOVERY_TARGET_DOWN_KW) == set(TARGET_DOWN_KEYWORDS)

    def test_no_divergence(self):
        import utils.hardware_access as ha
        import discovery.auto_discover_vf_registers as adr
        assert set(ha._TARGET_DOWN_KEYWORDS) == set(adr._DISCOVERY_TARGET_DOWN_KW)
