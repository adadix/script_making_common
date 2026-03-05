"""
Configuration loader for VF Curve Manager.

Loads and validates domain configuration from vf_domains.json.
"""

import json
import logging
import os
import shutil

log = logging.getLogger(__name__)


class ConfigLoader:
    """Load and manage VF domains configuration."""
    
    def __init__(self, json_path, auto_discover=False):
        """
        Initialize configuration loader.
        
        Args:
            json_path: Path to vf_domains.json file
            auto_discover: If True and config doesn't exist, auto-generate it
        """
        self.json_path = json_path
        self.auto_discover = auto_discover
        self.config = self._load_config()
    
    def _load_config(self):
        """Load configuration from JSON file."""
        if not os.path.exists(self.json_path):
            # Try auto-discovery if enabled
            if self.auto_discover:
                log.info("Configuration file not found: %s — attempting auto-discovery...", self.json_path)
                try:
                    from .platform_discovery import discover_and_save
                    config = discover_and_save(self.json_path)
                    log.info("Auto-generated configuration: %s", self.json_path)
                    return config.get('domains', {})
                except Exception as e:
                    log.error("Auto-discovery failed: %s", e)
            raise FileNotFoundError(f"Configuration file not found: {self.json_path}")
        
        with open(self.json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_all_domains(self):
        """Get all domains from configuration."""
        return self.config.get('domains', {})
    
    def get_domain(self, domain_name):
        """
        Get configuration for a specific domain.
        
        Args:
            domain_name: Name of domain (e.g., 'ia_core', 'ring')
            
        Returns:
            dict: Domain configuration or None if not found
        """
        return self.config.get('domains', {}).get(domain_name)
    
    def get_domain_list(self):
        """Get list of all domain names."""
        return list(self.config.get('domains', {}).keys())

    def get_scalar_modifiers(self) -> dict:
        """Return the scalar_modifiers section from the loaded config.

        Returns:
            dict: Mapping of register_name → modifier_info dicts.
                  Empty dict if the section is absent.
        """
        return self.config.get('scalar_modifiers', {})

    def has_flatten_support(self, domain_name):
        """
        Check if domain supports frequency flattening.
        
        Args:
            domain_name: Name of domain
            
        Returns:
            bool: True if domain has flatten_freq_ratios defined
        """
        domain = self.get_domain(domain_name)
        if domain is None:
            return False
        return 'flatten_freq_ratios' in domain and isinstance(domain['flatten_freq_ratios'], dict)
    
    def filter_unreachable_domains(self):
        """
        Remove domains whose fuse_path cannot be resolved on the current hardware.

        Fast-path platform check:
          Reads the _platform stamp written by auto_merge_to_vf_domains() and
          compares it with the currently connected hardware platform detected via
          detect_platform_name().  If they differ, ALL domains are cleared
          immediately — no per-domain ITP probing is needed because we already
          know the entire file is stale.  The caller (vf_curve_manager.py /
          vf_curve_manager_cli.py) will trigger auto-rediscovery when it sees
          the empty domain list.

        Per-domain probe (only when platform matches or stamp is absent):
          Silently traverses each domain's fuse_path via the live ITP namespace.
          Domains whose root object or any intermediate attribute is missing are
          dropped.  Handles the case where a platform update removes individual
          fuse containers without changing the platform name.

        Should be called after init_hardware() and before building the UI or
        running any CLI command.

        Returns:
            list[str]: Names of domains that were removed.
        """
        try:
            from utils import hardware_access as _ha
            # In mock mode the ITP namespace is empty — skip filtering entirely
            if getattr(_ha, 'MOCK_MODE', False):
                return []
            namespace = _ha._itp_namespace or {}
        except Exception:
            return []  # Can't probe without a live namespace — leave config as-is

        if not namespace:
            return []  # namespace not populated yet (e.g. called before init_hardware)

        # ── Fast-path: platform stamp check ──────────────────────────────
        # If vf_domains.json was built for a different platform, clear all
        # domains immediately without probing each one via ITP.
        domains_platform = self.config.get('_platform', '').lower()
        if domains_platform:
            try:
                from discovery.auto_discover_vf_registers import detect_platform_name
                current_platform = detect_platform_name().lower()
                if current_platform and domains_platform != current_platform:
                    bad_all = list(self.config.get('domains', {}).keys())
                    self.config['domains'] = {}
                    log.warning("vf_domains.json platform '%s' != connected platform '%s' "
                                "— clearing %d domain(s) for re-discovery",
                                domains_platform, current_platform, len(bad_all))
                    return bad_all
            except Exception as _pe:
                log.debug("filter_unreachable_domains: platform check skipped: %s", _pe)
                # Detection failed — leave the file as-is and do a per-domain probe.

        # ── Per-domain probe ──────────────────────────────────────────────
        # Attempt to resolve each domain's fuse_path through three layers:
        #   1. The injected ITP namespace dict (globals() from the launcher).
        #   2. namednodes — the canonical ITP object tree; covers platforms
        #      where `from pysvtools.pmext.services.regs import *` failed so
        #      `cdie` etc. are NOT in the flat globals dict.
        #   3. discovery.auto_discover_vf_registers.resolve_object — final
        #      fallback that also checks __main__ and call-stack frames.
        # A domain is only removed when ALL three layers fail.
        def _try_resolve(path: str) -> bool:
            """Return True if path is reachable via any resolution strategy."""
            parts = path.split('.')
            root  = parts[0]
            rest  = parts[1:]

            def _walk(obj):
                for p in rest:
                    obj = getattr(obj, p)
                return True

            # Layer 1: injected namespace dict
            if root in namespace:
                try:
                    _walk(namespace[root])
                    return True
                except Exception:
                    pass

            # Layer 2: namednodes
            try:
                import namednodes as _nn
                if hasattr(_nn, root):
                    _walk(getattr(_nn, root))
                    return True
            except Exception:
                pass

            # Layer 3: resolve_object (checks __main__ + call-stack frames)
            try:
                from discovery.auto_discover_vf_registers import resolve_object as _ro
                obj = _ro(path)
                if obj is not None:
                    return True
            except Exception:
                pass

            return False

        domains = self.config.get('domains', {})
        bad = []

        for name, cfg in list(domains.items()):
            path = cfg.get('fuse_path', '')
            if not path:
                continue
            if not _try_resolve(path):
                bad.append(name)

        for name in bad:
            del domains[name]
        self.config['domains'] = domains

        if bad:
            log.info("Skipped %d domain(s) not found on this platform: %s",
                     len(bad), ', '.join(bad))
            # Persist the pruned config back to disk so stale entries don't
            # reappear on the next launch.  Write a .bak first for safety.
            try:
                if os.path.exists(self.json_path):
                    shutil.copy2(self.json_path, self.json_path + '.bak')
                with open(self.json_path, 'w', encoding='utf-8') as _f:
                    json.dump(self.config, _f, indent=2)
                log.info("vf_domains.json updated — removed %d unreachable domain(s). "
                         "Backup saved to vf_domains.json.bak", len(bad))
            except Exception as _e:
                log.warning("Could not save pruned vf_domains.json: %s", _e)

        return bad

    def filter_zero_wp_domains(self):
        """
        Remove domains where every vf_voltage working-point register reads as 0.

        A domain whose WP table is entirely zero has not been programmed for
        this platform (or this fuse path / die index) and should be hidden from
        the UI domain selector and CLI ``list`` command so the user is not
        presented with domains that carry no meaningful data.

        Skipped entirely when running in mock mode — hardware reads always
        return 0 there, which would incorrectly prune every domain.

        Should be called after ``filter_unreachable_domains()`` so the fuse
        namespace is already live and only reachable domains are probed.

        Returns:
            list[str]: Names of domains that were removed.
        """
        try:
            from utils import hardware_access as _ha
            if getattr(_ha, 'MOCK_MODE', False):
                return []
        except Exception:
            return []

        try:
            from utils.fuse_io import load_fuse_ram, read_all_wps
        except Exception as _ie:
            log.debug("filter_zero_wp_domains: cannot import fuse_io — skipping: %s", _ie)
            return []

        domains = self.config.get('domains', {})

        # Load each unique fuse RAM path exactly once so that read_all_wps() returns
        # live values rather than the uninitialised zeros that are there before the
        # first explicit load.  All domains that share the same fuse_ram_path (e.g.
        # every punit_fuses domain on cdie.fuses) only trigger one hardware load.
        loaded_paths: set = set()
        failed_paths: set = set()
        for cfg in domains.values():
            frp = cfg.get('fuse_ram_path', cfg.get('fuse_path', ''))
            if frp and frp not in loaded_paths and frp not in failed_paths:
                try:
                    load_fuse_ram(cfg)
                    loaded_paths.add(frp)
                    log.debug("filter_zero_wp_domains: loaded fuse RAM for path '%s'", frp)
                except Exception as _le:
                    failed_paths.add(frp)
                    log.debug("filter_zero_wp_domains: load_fuse_ram failed for '%s': %s", frp, _le)

        # If EVERY fuse RAM path failed to load, the hardware isn't ready yet.
        # Return without pruning — it is safer to show all domains than to
        # silently hide them because of a transient ITP/power-state issue.
        if failed_paths and not loaded_paths:
            log.warning(
                "filter_zero_wp_domains: all fuse RAM loads failed (%s) — "
                "skipping zero-WP filter to preserve domain list",
                ', '.join(sorted(failed_paths)),
            )
            return []

        bad = []

        for name, cfg in list(domains.items()):
            try:
                wps = read_all_wps(cfg)
                # read_all_wps returns (None, None) tuples when the fuse object
                # cannot be resolved (e.g. cdie not yet in namespace).  Treat
                # an ALL-None voltage result as a READ FAILURE, not as an
                # all-zero domain — do not prune domains we couldn't read.
                voltages = [v for (v, _f) in wps]
                all_none  = all(v is None for v in voltages)
                if all_none:
                    log.debug(
                        "filter_zero_wp_domains: '%s' returned all-None voltages "
                        "(fuse path not resolvable yet) — keeping domain", name
                    )
                    continue
                # Domain is considered active if at least one WP has a non-zero voltage
                has_data = any(
                    v is not None and float(v) != 0.0
                    for v in voltages
                )
                if not has_data:
                    bad.append(name)
                    log.info(
                        "Domain '%s' has all-zero WP voltages — excluded from domain selection",
                        name,
                    )
            except Exception as _e:
                log.debug("filter_zero_wp_domains: could not read WPs for '%s': %s", name, _e)

        for name in bad:
            del domains[name]
        self.config['domains'] = domains

        if bad:
            log.info(
                "Filtered %d zero-WP domain(s) from selection: %s",
                len(bad), ', '.join(bad),
            )

        return bad

    def validate_config(self):
        """
        Validate configuration structure, including type and range constraints.

        Returns:
            tuple: (is_valid, error_message)
        """
        if 'domains' not in self.config:
            return False, "Missing 'domains' key in configuration"

        domains = self.config['domains']
        if not isinstance(domains, dict):
            return False, "'domains' must be a dictionary"

        required_fields = ['label', 'freq_multiplier', 'wp_count', 'fuse_path', 'vf_voltage']

        for domain_name, domain_config in domains.items():
            if not isinstance(domain_config, dict):
                return False, f"Domain '{domain_name}' must be a dict, got {type(domain_config).__name__}"

            # Check required fields exist
            for field in required_fields:
                if field not in domain_config:
                    return False, f"Domain '{domain_name}' missing required field '{field}'"

            # Type validation
            if not isinstance(domain_config['label'], str):
                return False, f"Domain '{domain_name}': 'label' must be a string"
            if not isinstance(domain_config['freq_multiplier'], (int, float)) or domain_config['freq_multiplier'] <= 0:
                return False, f"Domain '{domain_name}': 'freq_multiplier' must be a positive number"
            if not isinstance(domain_config['wp_count'], int) or domain_config['wp_count'] <= 0:
                return False, f"Domain '{domain_name}': 'wp_count' must be a positive integer"
            if not isinstance(domain_config['fuse_path'], str) or not domain_config['fuse_path']:
                return False, f"Domain '{domain_name}': 'fuse_path' must be a non-empty string"
            if not isinstance(domain_config['vf_voltage'], list):
                return False, f"Domain '{domain_name}': 'vf_voltage' must be a list"
            # vf_ratio is optional — delta domains have no ratio registers
            if 'vf_ratio' in domain_config and not isinstance(domain_config['vf_ratio'], list):
                return False, f"Domain '{domain_name}': 'vf_ratio' must be a list"

            # wp_count vs array length
            wp_count = domain_config['wp_count']
            if len(domain_config['vf_voltage']) != wp_count:
                return False, (
                    f"Domain '{domain_name}': vf_voltage has {len(domain_config['vf_voltage'])} entries "
                    f"but wp_count is {wp_count}"
                )
            vf_ratio = domain_config.get('vf_ratio', [])
            if len(vf_ratio) > wp_count:
                return False, (
                    f"Domain '{domain_name}': vf_ratio has {len(vf_ratio)} entries "
                    f"but wp_count is only {wp_count}"
                )

            # Range sanity checks on voltages (0.4 V – 2.0 V is a generous bound)
            # Only applied when the array contains actual numbers.
            # vf_domains.json may store register name strings instead of
            # literal voltage values — skip the range check in that case.
            for idx, v in enumerate(domain_config['vf_voltage']):
                if isinstance(v, str):
                    # Register name reference — skip numeric validation
                    continue
                if not isinstance(v, (int, float)):
                    return False, f"Domain '{domain_name}': vf_voltage[{idx}] is not a number"
                if not (0.3 <= v <= 2.1):
                    return False, (
                        f"Domain '{domain_name}': vf_voltage[{idx}]={v} V is outside the "
                        "expected range [0.3 V, 2.1 V]"
                    )

        return True, "Configuration is valid"
