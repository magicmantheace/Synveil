#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test original Synveil shm window against real headless Weston, not native boot."""
import argparse
import os
import re
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


def valid_trace(s, *, exercise_resize=False):
    # Wayland 1.26 prints object IDs with '#'; older runner libraries use '@'.
    # Normalize only known protocol object names followed by numeric IDs.
    s = re.sub(r"\b(xdg_wm_base|xdg_surface|xdg_toplevel|wl_surface|wl_buffer)#([0-9]+)\b",
               r"\1@\2", s)
    if not all(needle in s for needle in NEEDED):
        return False
    if not exercise_resize:
        return True
    sizes = [(int(w), int(h)) for w, h in re.findall(
        r"^SYNVEIL_XDG_SHM_SIZE ([0-9]+) ([0-9]+)$", s, re.MULTILINE)]
    return (len(sizes) >= 3 and sizes[0] == (400, 240) and
            any(size != sizes[0] for size in sizes[1:-1]) and
            sizes[-1] == sizes[0] and
            "SYNVEIL_XDG_RESIZE_CYCLE_COMPLETE" in s and
            ".set_maximized(" in s and ".unset_maximized(" in s)



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
    return [str(loader), "--inhibit-cache", "--library-path", library_path, str(binary)]


def validate_target_libraries(output, library_path):
    """Reject loader cache/default-directory fallback, including symlink escapes."""
    roots = [Path(p).resolve(strict=True) for p in library_path.split(":")]
    saw_libc = saw_loader = False
    count = 0
    for line in output.splitlines():
        if not line.strip():
            continue
        if re.fullmatch(r"\s*linux-vdso\.so\.1 \(0x[0-9a-fA-F]+\)\s*", line):
            continue
        match = re.fullmatch(
            r"\s*(?:(\S+)\s+=>\s+)?(/\S+)\s+\(0x[0-9a-fA-F]+\)\s*", line)
        if not match:
            raise ValueError("unrecognized target loader dependency: " + line[:200])
        identity, filename = match.groups()
        path = Path(filename).resolve(strict=True)
        if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
            raise ValueError("target loader resolved a library outside Synveil: " + filename)
        saw_libc |= identity == "libc.so.6"
        saw_loader |= path.name == "ld-linux-x86-64.so.2"
        count += 1
    if not saw_libc or not saw_loader or count < 2:
        raise ValueError("target runtime did not resolve both native libc and loader")


def clean_loader_environment(environment):
    # These are disposable public-code probes. Do not allow injected host DSOs
    # or loader configuration to masquerade as execution with target glibc.
    return {k: v for k, v in environment.items()
            if not k.startswith("LD_") and k not in (
                "GLIBC_TUNABLES", "GCONV_PATH", "LOCPATH", "NLSPATH")}


def require_target_runtime(argv, library_path, env):
    result = subprocess.run(argv[:-1] + ["--list", argv[-1]], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("target glibc loader preflight failed: " + result.stdout[-1200:])
    validate_target_libraries(result.stdout, library_path)
    print("SYNVEIL_TARGET_GLIBC_DEPENDENCY_CLOSURE_VERIFIED")


def run(binary, timeout=25, *, loader=None, library_path=None, exercise_resize=False, two_windows=False):
    if os.geteuid() == 0:
        raise ValueError("refusing root Wayland session")
    if not 8 <= timeout <= 60:
        raise ValueError("invalid timeout")
    weston = shutil.which("weston")
    if not weston or not binary.is_file() or binary.is_symlink():
        raise ValueError("missing verified Weston/client executable")
    argv = target_command(binary, loader=loader, library_path=library_path)
    clean_env = clean_loader_environment(os.environ)
    if loader is not None:
        require_target_runtime(argv, library_path, clean_env)
    if exercise_resize:
        argv.append("--exercise-resize")
    with tempfile.TemporaryDirectory(prefix="synveil-window-") as temp:
        root = Path(temp)
        run_dir = root / "run"
        run_dir.mkdir(mode=0o700)
        env = clean_env.copy()
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET",
                    "DBUS_SESSION_BUS_ADDRESS", "WESTON_CONFIG_FILE"):
            env.pop(key, None)
        env.update(XDG_RUNTIME_DIR=str(run_dir), WAYLAND_DISPLAY=SOCKET,
                   HOME=str(root), XDG_CONFIG_HOME=str(root))
        server = client = sibling = None
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
            if two_windows:
                sibling_argv = target_command(binary, loader=loader, library_path=library_path)
                with (root / "interactive.log").open("wb") as log:
                    sibling = subprocess.Popen(sibling_argv + ["--interactive"],
                        env={**env, "WAYLAND_DEBUG": "client"}, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                while time.monotonic() < deadline:
                    if server.poll() is not None or sibling.poll() is not None:
                        raise RuntimeError("interactive window or compositor exited before mapping")
                    if valid_trace((root / "interactive.log").read_text(errors="replace")):
                        break
                    time.sleep(.08)
                else:
                    raise RuntimeError("interactive toplevel did not map")
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
            if client.returncode != 0 or not valid_trace(output, exercise_resize=exercise_resize):
                raise RuntimeError("actual xdg/shm window handshake missing (exit=" + str(client.returncode) + "): " + output[-1200:])
            if two_windows:
                if sibling.pid == client.pid or sibling.poll() is not None:
                    raise RuntimeError("independent interactive window did not survive resize client")
                if not valid_trace((root / "interactive.log").read_text(errors="replace")):
                    raise RuntimeError("independent interactive window handshake missing")
                print("SYNVEIL_HOST_WESTON_TWO_ORIGINAL_WINDOWS_VERIFIED")
            print("SYNVEIL_HOST_WESTON_SHM_XDG_WINDOW_VERIFIED")
            if exercise_resize:
                print("SYNVEIL_HOST_WESTON_RESIZE_REPAINT_VERIFIED")
            print("native_synveil=false; headless=true")
        finally:
            terminate(client)
            terminate(sibling)
            terminate(server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--target-loader", type=Path)
    parser.add_argument("--target-library-path")
    parser.add_argument("--exercise-resize", action="store_true")
    parser.add_argument("--two-windows", action="store_true")
    args = parser.parse_args()
    try:
        run(args.binary.resolve(), args.timeout,
            loader=args.target_loader, library_path=args.target_library_path,
            exercise_resize=args.exercise_resize, two_windows=args.two_windows)
    except (OSError, ValueError, RuntimeError) as error:
        # This isolated probe has no credentials or user data. Preserve its bounded
        # diagnostic in Actions annotations when raw job logs are inaccessible.
        message = str(error)[-2000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::error::" + message, flush=True)
        raise
