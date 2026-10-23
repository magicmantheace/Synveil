#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test original Synveil shm window against real headless Weston, not native boot."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time

SOCKET = "synveil-first-window-0"
NEEDED = ("xdg_wm_base@", "get_xdg_surface", "get_toplevel",
          ".configure(", "ack_configure(", ".attach(", ".commit(",
          "SYNVEIL_XDG_TOPLEVEL_SHM_SUBMITTED")


def valid_trace(s):
    return all(needle in s for needle in NEEDED)


def terminate(proc):
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def run(binary, timeout=25):
    if os.geteuid() == 0:
        raise ValueError("refusing root Wayland session")
    if not 8 <= timeout <= 60:
        raise ValueError("invalid timeout")
    weston = shutil.which("weston")
    if not weston or not binary.is_file() or binary.is_symlink():
        raise ValueError("missing verified Weston/client executable")
    with tempfile.TemporaryDirectory(prefix="synveil-window-") as temp:
        root = Path(temp)
        run_dir = root / "run"
        run_dir.mkdir(mode=0o700)
        env = os.environ.copy()
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET",
                    "DBUS_SESSION_BUS_ADDRESS", "WESTON_CONFIG_FILE"):
            env.pop(key, None)
        env.update(XDG_RUNTIME_DIR=str(run_dir), WAYLAND_DISPLAY=SOCKET,
                   HOME=str(root), XDG_CONFIG_HOME=str(root))
        server = client = None
        try:
            with (root / "weston.log").open("wb") as log:
                server = subprocess.Popen([weston, "--backend=headless",
                    "--renderer=pixman", "--shell=desktop-shell.so",
                    "--width=1024", "--height=768", "--idle-time=0",
                    "--no-config", "--socket=" + SOCKET], env=env,
                    stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + timeout
            endpoint = run_dir / SOCKET
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Weston exited prematurely")
                try:
                    info = endpoint.lstat()
                    if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid():
                        break
                except FileNotFoundError:
                    pass
                time.sleep(.08)
            else:
                raise RuntimeError("private Weston socket missing")
            with (root / "window.log").open("wb") as log:
                client = subprocess.Popen([str(binary)], env={**env, "WAYLAND_DEBUG": "client"},
                    stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            while time.monotonic() < deadline and client.poll() is None:
                if server.poll() is not None:
                    raise RuntimeError("Weston crashed with a window")
                time.sleep(.08)
            if client.poll() is None:
                raise RuntimeError("window failed bounded exit")
            output = (root / "window.log").read_text(errors="replace")
            if client.returncode != 0 or not valid_trace(output):
                raise RuntimeError("actual xdg/shm window handshake missing: " + output[-1200:])
            print("SYNVEIL_HOST_WESTON_SHM_XDG_WINDOW_VERIFIED")
            print("native_synveil=false; headless=true")
        finally:
            terminate(client)
            terminate(server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    run(args.binary.resolve(), args.timeout)
