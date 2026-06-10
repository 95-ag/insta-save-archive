"""
X display setup for headed Playwright runs on WSL2 + VcXsrv.

## Usage
    display = ensure_display()   # call before launching a headed browser
    # ... run browser ...
    close_display()              # call after browser closes (stops VcXsrv if we launched it)

## How it works
    WSL2 reaches Windows via a virtual network adapter (vEthernet WSL Hyper-V firewall).
    The Windows-side IP of that adapter is the default gateway inside WSL — readable from
    `ip route show default`. This IP is stable within a Windows session but can change
    across Windows restarts if the subnet shifts.

    VcXsrv listens on TCP port 6000+DISPLAY_NUM (6001 for display :1). We TCP-probe that
    port to check reachability before and after launch.

## Debug directions (if --headed fails)
    1. Wrong IP: run `ip route show default` in WSL — compare with DISPLAY printed above.
       The Windows adapter IP is NOT the same as the /etc/resolv.conf nameserver (NAT gateway).
    2. VcXsrv not running: run `tasklist.exe /fi "imagename eq vcxsrv.exe"` from WSL via
       `/mnt/c/Windows/System32/tasklist.exe`. If missing, launch failed silently.
    3. VcXsrv on wrong display: confirm command line shows `:1` via
       `Get-WmiObject Win32_Process -Filter 'Name="vcxsrv.exe"' | Select-Object CommandLine`
       in PowerShell. XLaunch uses `-displayfd` (dynamic) — always launch directly.
    4. Port blocked: test with `timeout 2 bash -c "echo > /dev/tcp/<host>/6001"`.
       If "Connection refused" with VcXsrv running: Windows Firewall or Hyper-V NAT is
       blocking. The standard firewall rule (Any/Allow/6001) applies to the Hyper-V adapter,
       not the NAT path. /etc/resolv.conf nameserver (10.x.x.x) is the NAT gateway — it will
       always be blocked. Use the gateway IP instead.
    5. Access control: VcXsrv must be launched with `-ac` flag. Without it, all remote X
       clients are rejected regardless of firewall rules.
    6. PowerShell launch failure: `Start-Process` from WSL spawns on the Windows Desktop
       session (Session 1/Console). If Windows UAC prompt appears, the process is blocked.
       Check with `tasklist.exe` after 5s.
"""

import os
import re
import socket
import subprocess
import time

VCXSRV_EXE = r"C:\Program Files\VcXsrv\vcxsrv.exe"
DISPLAY_NUM = 1
_X_PORT = 6000 + DISPLAY_NUM  # 6001
_LAUNCH_TIMEOUT = 15  # seconds to wait for VcXsrv to become reachable
_PS_EXE = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# Set to True by ensure_display() when we launched VcXsrv ourselves.
# close_display() uses this to decide whether to stop the process.
_we_launched = False


def _windows_host_ip() -> str:
    # Use default gateway, not /etc/resolv.conf nameserver.
    # The nameserver is the NAT gateway (10.x.x.x) — unreachable for X.
    # The default gateway is the WSL adapter IP on Windows — where VcXsrv listens.
    result = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True)
    match = re.search(r"default via ([\d.]+)", result.stdout)
    if not match:
        raise RuntimeError("display: could not determine Windows host IP from 'ip route show default'")
    return match.group(1)


def _x_reachable(host: str) -> bool:
    try:
        with socket.create_connection((host, _X_PORT), timeout=2):
            return True
    except OSError:
        return False


def _launch_vcxsrv(host: str) -> None:
    args = f":{DISPLAY_NUM} -multiwindow -ac -noclipboard"
    subprocess.Popen(
        [_PS_EXE, "-Command", f"Start-Process '{VCXSRV_EXE}' -ArgumentList '{args}'"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + _LAUNCH_TIMEOUT
    while time.time() < deadline:
        time.sleep(1)
        if _x_reachable(host):
            return
    raise RuntimeError(
        f"display: launched VcXsrv but display :{DISPLAY_NUM} never became reachable "
        f"at {host}:{_X_PORT}. "
        f"Check VcXsrv is installed at '{VCXSRV_EXE}' and Windows Firewall allows port {_X_PORT}."
    )


def _stop_vcxsrv() -> None:
    subprocess.run(
        [_PS_EXE, "-Command", "Stop-Process -Name vcxsrv -Force -ErrorAction SilentlyContinue"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_display() -> str:
    """
    Ensure a headed X display is available. Sets os.environ['DISPLAY'].

    Detects the Windows host IP via default gateway, TCP-probes VcXsrv,
    and auto-launches it if not running. Call close_display() when done.

    Returns the DISPLAY string (e.g. '172.22.48.1:1.0').
    Raises RuntimeError if VcXsrv cannot be reached after a launch attempt.
    """
    global _we_launched
    host = _windows_host_ip()
    display = f"{host}:{DISPLAY_NUM}.0"

    if _x_reachable(host):
        os.environ["DISPLAY"] = display
        _we_launched = False
        return display

    print(f"display: VcXsrv not reachable at {host}:{_X_PORT} — launching...")
    _launch_vcxsrv(host)
    os.environ["DISPLAY"] = display
    _we_launched = True
    print(f"display: VcXsrv ready — DISPLAY={display}")
    return display


def close_display() -> None:
    """
    Stop VcXsrv if we launched it. No-op if VcXsrv was already running before ensure_display().
    Call this after the browser closes.
    """
    global _we_launched
    if _we_launched:
        print("display: stopping VcXsrv (launched by this session)")
        _stop_vcxsrv()
        _we_launched = False
