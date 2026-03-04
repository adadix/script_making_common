"""
Process management utilities.

Contains helpers that must run BEFORE Intel toolchain imports
(e.g. terminating stale OpenIPC processes at startup).

Only standard-library / psutil imports are allowed here — no ITP modules.
"""

import logging
import subprocess
import time

log = logging.getLogger(__name__)


def terminate_openipc() -> bool:
    """Terminate any existing OpenIPC/ipccli processes for a clean startup.

    Tries psutil first (cross-platform, preferred), then falls back to a
    Windows-only PowerShell + taskkill approach.

    Returns:
        True if at least one process was killed, False otherwise.
    """
    _OPENIPC_NAMES = {'openipc', 'ipccli'}
    killed = []

    # ── psutil path (preferred, cross-platform) ─────────────────────────────
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = (proc.info['name'] or '').lower()
                if any(k in name for k in _OPENIPC_NAMES):
                    proc.kill()
                    killed.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            log.info("Terminated OpenIPC processes (psutil): PIDs %s", killed)
            time.sleep(3)
        return bool(killed)
    except ImportError:
        pass

    # ── PowerShell / taskkill fallback (Windows only) ───────────────────────
    try:
        result = subprocess.run(
            ['powershell', '-Command',
             "Get-Process | Where-Object {$_.ProcessName -like '*openipc*' -or "
             "$_.ProcessName -like 'ipccli*'} | Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [p.strip() for p in result.stdout.strip().split('\n') if p.strip().isdigit()]
            if pids:
                log.info("Terminating existing OpenIPC processes...")
                for pid in pids:
                    try:
                        subprocess.run(['taskkill', '/F', '/PID', pid],
                                       capture_output=True, timeout=3)
                    except Exception:
                        pass
                log.info("OpenIPC processes terminated")
                time.sleep(1)
                return True
        return False
    except Exception:
        return False
