#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Unprivileged Weston/Pixman headless preflight with two xdg_toplevel clients.

This tests Wayland protocol, NOT native Synveil, visible RGB or physical boot.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time

CLIENTS = ("weston-terminal", "weston-simple-shm")
SOCKET = "synveil-weston-probe-0"
OPTIONS = ("--backend=headless", "--renderer=pixman",
           "--shell=desktop-shell.so", "--width=1024", "--height=768",
           "--idle-time=0", "--no-config", "--socket=" + SOCKET)


def secure_runtime(path):
    try:
        meta = path.lstat()
        return (stat.S_ISDIR(meta.st_mode) and meta.st_uid == os.geteuid()
                and stat.S_IMODE(meta.st_mode) == 0o700)
    except OSError:
        return False


def has_toplevel(trace):
    return all(s in trace for s in
               ("xdg_wm_base@", "xdg_surface@", "get_toplevel",
                "xdg_toplevel@", ".configure("))


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def probe(seconds):
    if os.geteuid() == 0:
        raise RuntimeError("refusing root compositor test")
    if not 8 <= seconds <= 120:
        raise ValueError("unbounded test duration")
    paths = {x: shutil.which(x) for x in ("weston", *CLIENTS)}
    if not all(paths.values()):
        raise RuntimeError("missing Weston demo client package")
    with tempfile.TemporaryDirectory(prefix="synveil-weston-") as temp:
        root = Path(temp)
        run = root / "run"
        home = root / "home"
        run.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        if not secure_runtime(root) or not secure_runtime(run):
            raise RuntimeError("unsafe Wayland socket directory")
        env = dict(os.environ)
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET",
                    "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DEBUG", "WESTON_CONFIG_FILE"):
            env.pop(key, None)
        env.update(XDG_RUNTIME_DIR=str(run), HOME=str(home),
                   XDG_CONFIG_HOME=str(home), WAYLAND_DISPLAY=SOCKET)
        logs = {x: root / (x + ".log") for x in ("weston", *CLIENTS)}
        server = None
        clients = []
        try:
            with logs["weston"].open("wb") as output:
                server = subprocess.Popen([paths["weston"], *OPTIONS],
                    stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, env=env, start_new_session=True)
            deadline = time.monotonic() + seconds
            endpoint = run / SOCKET
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Weston exited before socket readiness")
                try:
                    st = endpoint.lstat()
                    if stat.S_ISSOCK(st.st_mode) and st.st_uid == os.geteuid():
                        break
                except FileNotFoundError:
                    pass
                time.sleep(0.08)
            else:
                raise RuntimeError("no private Wayland server socket")
            env["WAYLAND_DEBUG"] = "client"
            for name in CLIENTS:
                with logs[name].open("wb") as output:
                    clients.append(subprocess.Popen([paths[name]],
                        stdin=subprocess.DEVNULL, stdout=output,
                        stderr=subprocess.STDOUT, env=env, start_new_session=True))
            while time.monotonic() < deadline:
                if server.poll() is not None or any(p.poll() is not None for p in clients):
                    raise RuntimeError("compositor or client exited before window proof")
                ready = []
                for name in CLIENTS:
                    if logs[name].stat().st_size > 2 * 1024 * 1024:
                        raise RuntimeError("oversized client trace")
                    ready.append(has_toplevel(logs[name].read_text(errors="replace")))
                if all(ready):
                    time.sleep(0.25)
                    if server.poll() is not None or any(p.poll() is not None for p in clients):
                        raise RuntimeError("windows did not remain alive")
                    return dict(result="passed", compositor="weston",
                        backend="headless", renderer="pixman", windows=2,
                        native_synveil=False, rgb_scanout=False, physical_boot=False)
                time.sleep(0.10)
            raise RuntimeError("two configured xdg_toplevel windows not observed")
        finally:
            for p in reversed(clients):
                stop(p)
            stop(server)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=35)
    args = parser.parse_args()
    try:
        result = probe(args.timeout)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(result="failed", reason=str(error))))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
