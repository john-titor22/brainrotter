"""`brainrotter app` - open the dashboard as a desktop window whose lifetime is
tied to the server.

- Close the window  -> the server stops, the port is released.
- Open it again     -> a fresh server starts.
- Run it twice      -> the second one just opens another window; it never
                       fights the first for the server.

The server runs in-process (a uvicorn thread), so there is no child process to
leak.
"""

from __future__ import annotations

import datetime
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import httpx

from .config import get_settings

# Bind on the hostname so BOTH 127.0.0.1 and ::1 are served — on Windows
# `localhost` often resolves to ::1 first, and a v4-only bind then refuses it.
BIND_HOST = "localhost"
PORT = 8000
HOST = "127.0.0.1"                 # what we probe / point the window at
URL = f"http://{HOST}:{PORT}"
_HEALTH = f"{URL}/api/queue"
_LAUNCHER_MUTEX_PORT = 47655   # distinct from the worker mutex (47654)

_LOG: Path | None = None


def _log(msg: str) -> None:
    line = f"{datetime.datetime.now():%H:%M:%S}  {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    if _LOG:
        try:
            with _LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

_BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]


# --- helpers ---------------------------------------------------------------

def _find_browser() -> str | None:
    import shutil

    for c in _BROWSERS:
        if Path(c).is_file():
            return c
    for name in ("chrome", "msedge", "brave"):
        if (p := shutil.which(name)):
            return p
    return None


def _health_ok() -> bool:
    try:
        return httpx.get(_HEALTH, timeout=1.5).status_code == 200
    except Exception:
        return False


def _port_free() -> bool:
    for fam, addr in ((socket.AF_INET, ("127.0.0.1", PORT)),
                      (socket.AF_INET6, ("::1", PORT))):
        s = socket.socket(fam, socket.SOCK_STREAM)
        try:
            if s.connect_ex(addr) == 0:   # something is accepting connections
                return False
        finally:
            s.close()
    return True


def _kill_port(port: int) -> None:
    """Kill whatever is listening on `port` (a stale server from last time)."""
    if sys.platform != "win32":
        return
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                              timeout=8).stdout
        pids = {
            line.split()[-1]
            for line in out.splitlines()
            if f":{port} " in line and "LISTENING" in line
        }
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=8)
    except Exception:
        pass


def _acquire_mutex() -> socket.socket | None:
    s = socket.socket()
    try:
        s.bind((HOST, _LAUNCHER_MUTEX_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def _app_window_open(browser_url: str) -> bool:
    """True while the main --app browser process for our URL is alive."""
    if sys.platform != "win32":
        return _health_ok()
    ps = (
        "Get-CimInstance Win32_Process -Filter "
        "\"name='chrome.exe' or name='msedge.exe' or name='brave.exe'\" | "
        "ForEach-Object { $_.CommandLine }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=8).stdout or ""
    except Exception:
        return True  # be conservative - don't kill the server on a probe error
    for line in out.splitlines():
        if browser_url in line and "--type=" not in line:
            return True
    return False


# --- server --------------------------------------------------------------

def _start_server_thread():
    import uvicorn

    config = uvicorn.Config("brainrotter.server.app:app", host=BIND_HOST, port=PORT,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None   # not the main thread

    def _serve():
        try:
            server.run()
        except Exception:
            _log("server thread crashed:\n" + traceback.format_exc())

    t = threading.Thread(target=_serve, name="brainrotter-uvicorn", daemon=True)
    t.start()
    return server, t


# --- entry point --------------------------------------------------------

def run() -> int:
    global _LOG
    settings = get_settings()
    _LOG = settings.workspace / "launcher.log"
    try:
        _LOG.write_text("")
    except Exception:
        _LOG = None
    _log(f"brainrotter app  |  python {sys.version.split()[0]}  |  {sys.executable}")

    try:
        return _run(settings)
    except Exception:
        _log("FATAL:\n" + traceback.format_exc())
        _log("Something went wrong - details above and in workspace/launcher.log.")
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
        return 1


def _run(settings) -> int:
    mutex = _acquire_mutex()

    if mutex is None:
        if not _health_ok():
            _log("another launcher is starting up - try again in a moment.")
            return 1
        _log("Brainrotter is already running - opening another window.")
        _run_window(settings, server=None)
        return 0

    try:
        if not _port_free():
            _log("clearing a previous session...")
            _kill_port(PORT)
            for _ in range(20):
                if _port_free():
                    break
                time.sleep(0.5)
            else:
                _log(f"port {PORT} is stuck. Reboot, or close the other window, then retry.")
                input("\nPress Enter to close...")
                return 1

        _log("starting Brainrotter...")
        server, thread = _start_server_thread()
        for _ in range(60):
            if _health_ok():
                break
            if not thread.is_alive():
                _log("the dashboard thread stopped before it came up (see above).")
                input("\nPress Enter to close...")
                return 1
            time.sleep(0.5)
        else:
            _log("the dashboard did not respond in time.")
            server.should_exit = True
            input("\nPress Enter to close...")
            return 1

        _log(f"dashboard is up at {URL}")
        _run_window(settings, server=server)
        _log("closing Brainrotter...")
        server.should_exit = True
        thread.join(timeout=10)
        return 0
    finally:
        mutex.close()


def _run_window(settings, *, server) -> None:
    browser = _find_browser()
    if not browser:
        print(f"\nNo Chrome / Edge / Brave found - open {URL} in your browser.")
        print("Press Ctrl+C here to stop." if server else "This window can be closed.")
        try:
            while server is None or not server.should_exit:
                time.sleep(1)
                if server is None and not _health_ok():
                    return
        except KeyboardInterrupt:
            pass
        return

    profile = settings.workspace / "app-profile"
    subprocess.Popen([
        browser, f"--app={URL}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1200,880",
    ])

    # Wait for the app window to appear, then wait for it to go away.
    for _ in range(20):
        if _app_window_open(f"--app={URL}"):
            break
        time.sleep(0.5)
    misses = 0
    try:
        while misses < 2:
            time.sleep(2)
            misses = 0 if _app_window_open(f"--app={URL}") else misses + 1
    except KeyboardInterrupt:
        pass
