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


def target_command(binary, *, loader=None, library_path=None):
    """Optional pinned libc loader for executing a Synveil ELF on host Linux.

    Both arguments must be set; never silently fall back to host libc.
    """
    if (loader is None) != (library_path is None):
        raise ValueError("target loader and libc library path must be paired")
    if loader is None:
        return [str(binary)]
    if not loader.is_file():
        raise ValueError("target glibc dynamic loader missing")
    roots = [Path(x) for x in library_path.split(":")]
    if (not roots or any(not p.is_absolute() or not p.is_dir()
                         for p in roots)):
        raise ValueError("target glibc search directories unavailable")
    return [str(loader), "--library-path", library_path, str(binary)]


def run(binary, timeout=25, *, loader=None, library_path=None):
    if os.geteuid() == 0:
        raise ValueError("refusing root Wayland session")
    if not 8 <= timeout <= 60:
        raise ValueError("invalid timeout")
    weston = shutil.which("weston")
    if not weston or not binary.is_file() or binary.is_symlink():
        raise ValueError("missing verified Weston/client executable")
    argv = target_command(binary, loader=loader, library_path=library_path)
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
                client = subprocess.Popen(argv, env={**env, "WAYLAND_DEBUG": "client"},
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
    parser.add_argument("--target-loader", type=Path)
    parser.add_argument("--target-library-path")
    args = parser.parse_args()
    try:
        run(args.binary.resolve(), args.timeout,
            loader=args.target_loader, library_path=args.target_library_path)
    except (OSError, ValueError, RuntimeError) as error:
        # This isolated probe has no credentials or user data. Preserve its bounded
        # diagnostic in Actions annotations when raw job logs are inaccessible.
        message = str(error)[-2000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::error::" + message, flush=True)
        raise
