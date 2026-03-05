"""spec_db_refresh_helper.py — print info needed to update fuse_spec_db.json.

Run via the VS Code task "Refresh CoDesign Spec DB", or directly::

    python src/discovery/spec_db_refresh_helper.py

This script:
  1. Reads the current discovery cache (vf_discovery_cache.json)
  2. Checks which platform is present and whether spec data is already loaded
  3. Prints a summary of registers that lack spec data
  4. Writes spec_db_request.json with the info needed to query CoDesign

After running, paste the output into a Copilot chat session and ask:
    "Please query CoDesign for [platform] fuse specs and update
    src/fuse_spec_db.json with the missing entries."
"""

import json
import pathlib
import sys

_SRC = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_SRC))

CACHE_PATH = _SRC / 'vf_discovery_cache.json'
DB_PATH    = _SRC / 'fuse_spec_db.json'


def main():
    # --- Load discovery cache ---
    if not CACHE_PATH.exists():
        print('[!] No discovery cache found at:', CACHE_PATH)
        print('    Run a live discovery first (Discover Registers button or CLI).')
        sys.exit(1)

    with open(CACHE_PATH, encoding='utf-8') as f:
        cache = json.load(f)

    platform  = cache.get('platform', 'unknown')
    registers = cache.get('registers', [])
    print(f'Platform  : {platform}')
    print(f'Registers : {len(registers)}')

    # --- Load spec DB ---
    db_platforms = []
    db_entries   = 0
    if DB_PATH.exists():
        with open(DB_PATH, encoding='utf-8') as f:
            db = json.load(f)
        db_platforms = [k for k in db if not k.startswith('__')]
        db_entries   = sum(len(v) for k, v in db.items() if not k.startswith('__'))

    print(f'Spec DB   : {DB_PATH.name}  —  {db_entries} entries across {db_platforms}')
    print()

    # --- Check for spec coverage ---
    from discovery.spec_db import enrich_records  # noqa: PLC0415

    # Clone records to avoid modifying the originals
    recs_clone = [dict(r) for r in registers]
    enrich_records(platform, recs_clone)

    covered  = sum(1 for r in recs_clone if r.get('spec_description'))
    missing  = [r['name'] for r in recs_clone if not r.get('spec_description')]

    print(f'Spec coverage : {covered}/{len(registers)} registers have HAS descriptions')
    if not missing:
        print('No gaps — fuse_spec_db.json already covers all discovered registers.')
        return

    print(f'Missing spec  : {len(missing)} register(s)')
    print()
    print('=== Registers without spec data (first 50) ===')
    for n in missing[:50]:
        print(' ', n)
    if len(missing) > 50:
        print(f'  ... and {len(missing) - 50} more')

    # --- Write request file ---
    try:
        from discovery.spec_db import write_request  # noqa: PLC0415
        req_path = write_request(platform, missing)
        print()
        print(f'[+] Wrote spec_db_request.json → {req_path}')
    except Exception as exc:
        print(f'[!] Could not write request file: {exc}')

    print()
    print('=== Next steps ===')
    print('1. Open a Copilot chat in VS Code.')
    print(f'2. Ask: "Please query CoDesign for {platform} fuse specs')
    print('   and add the missing entries to src/fuse_spec_db.json."')
    print('3. Copilot will use the CoDesign MCP and update the DB for you.')


if __name__ == '__main__':
    main()
