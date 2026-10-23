#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Real native UEFI/QEMU Weston RGB, independent windows and input acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import native_weston_vm as vm
from qmp_input import (_read_reply, emit_command, send_keys, verified_ppm_test_frame,
                       ALLOWED_TERMINAL_RETURN)

READY = b'SYNVEIL_NATIVE_WESTON_VM_TWO_WINDOWS_READY'
CLOSED = b'SYNVEIL_NATIVE_WESTON_VM_KEYBOARD_CLOSE_OK'
TERMINAL = b'SYNVEIL_NATIVE_WESTON_VM_TERMINAL_INPUT_OK'
FAILED = b'SYNVEIL_NATIVE_WESTON_VM_FAILED'


def pixels(path):
    if not verified_ppm_test_frame(path):
        raise ValueError('invalid/blank native guest RGB scanout')
    data = path.read_bytes()
    match = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', data)
    if not match:
        raise ValueError('unexpected screenshot framing')
    width, height = map(int, match.groups())
    return width, height, data[match.end():]


def original_window(width, height, data):
    # The source-native client's known opaque gradient supplies actual pixel
    # evidence distinct from firmware/boot text or a compositor background.
    def gradient(offset):
        r, g, b = data[offset:offset + 3]
        return b == 116 and 30 <= r <= 75 and 45 <= g <= 80
    for y in range(28, height):
        start = None
        for x in range(width + 1):
            matches = x < width and gradient((y * width + x) * 3)
            if matches and start is None:
                start = x
            if not matches and start is not None:
                if x - start >= 180:
                    bottom = y
                    while bottom + 1 < height and gradient(((bottom + 1) * width + start + 2) * 3):
                        bottom += 1
                    top = y - 28
                    return start, top, x - start, bottom - top + 1
                start = None
    return None


def terminal_pixels_visible(width, height, data, window):
    x, y, w, h = window
    dark = light = 0
    for row in range(32, height):
        for col in range(width):
            if x <= col < x + w and y <= row < y + h:
                continue
            offset = (row * width + col) * 3
            r, g, b = data[offset:offset + 3]
            dark += max(r, g, b) < 30
            light += min(r, g, b) >= 180 and max(r, g, b) - min(r, g, b) <= 20
    return dark >= 3000 and light >= 50


def terminal_point(width, height, data):
    # Locate a visible terminal interior after the original client closes.
    # A long dark run distinguishes its canvas from this fixture's blue
    # background; avoid the shell panel and all window title bars.
    for y in range(80, height - 20):
        start = None
        for x in range(width + 1):
            dark = x < width and max(data[(y * width + x) * 3:(y * width + x) * 3 + 3]) < 20
            if dark and start is None:
                start = x
            if not dark and start is not None:
                if x - start >= 180:
                    return start + 80, y
                start = None
    raise RuntimeError('independent terminal interior not visible')


def qmp_command(endpoint, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(8)
        client.connect(str(endpoint))
        with client.makefile('rb') as reader:
            _read_reply(reader, greeting=True)
            emit_command(client, reader, {'execute': 'qmp_capabilities'})
            emit_command(client, reader, command)


def screenshot(endpoint, folder, number):
    path = folder / ('frame-' + str(number) + '.ppm')
    if path.exists() or path.is_symlink():
        raise ValueError('screenshot path reused')
    try:
        qmp_command(endpoint, {'execute': 'screendump', 'arguments': {'filename': str(path)}})
        return pixels(path)
    finally:
        path.unlink(missing_ok=True)


def pointer(endpoint, width, height, x, y, *, button=None):
    if not 0 <= x < width or not 0 <= y < height or button not in (None, True, False):
        raise ValueError('pointer test coordinates outside guest display')
    events = [{'type': 'abs', 'data': {'axis': axis, 'value': int(value * 32767 / (extent - 1))}}
              for axis, value, extent in [('x', x, width), ('y', y, height)]]
    if button is not None:
        events.append({'type': 'btn', 'data': {'down': button, 'button': 'left'}})
    qmp_command(endpoint, {'execute': 'input-send-event', 'arguments': {'events': events}})
    time.sleep(.15)


def drag(endpoint, width, height, start, finish):
    pointer(endpoint, width, height, *start)
    try:
        pointer(endpoint, width, height, *start, button=True)
        pointer(endpoint, width, height, *finish)
    finally:
        pointer(endpoint, width, height, *finish, button=False)


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run(timeout=180):
    if os.geteuid() == 0 or not 60 <= timeout <= 360:
        raise ValueError('native compositor VM smoke requires non-root and bounded timeout')
    image = vm.OUT / 'synveil-x86_64.img'
    proof = json.loads((vm.OUT / 'validation.json').read_text())
    if (proof.get('schema') != 'synveil.native-weston-vm-image/v1' or
            image.is_symlink() or not image.is_file() or
            hashlib.sha256(image.read_bytes()).hexdigest() != proof.get('image_sha256')):
        raise ValueError('native compositor fixture image changed')
    with tempfile.TemporaryDirectory(prefix='synveil-native-weston-') as temporary:
        folder = Path(temporary)
        endpoint = folder / 'qmp.sock'
        log = folder / 'serial.log'
        env = {key: value for key, value in os.environ.items() if not key.startswith('SYNVEIL_')}
        env.update(SYNVEIL_OUT_DIR=str(vm.OUT), SYNVEIL_QEMU_VIDEO='1',
                   SYNVEIL_QEMU_DISPLAY='none', SYNVEIL_QEMU_NETWORK='0',
                   SYNVEIL_QEMU_MEMORY='768M', SYNVEIL_QEMU_QMP_SOCKET=str(endpoint))
        with log.open('wb') as output:
            process = subprocess.Popen(['bash', str(ROOT / 'build/scripts/qemu.sh')],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                lines = {line.strip() for line in log.read_bytes().splitlines()}
                if FAILED in lines or process.poll() is not None:
                    raise RuntimeError('native guest compositor/session failed: ' + log.read_text(errors='replace')[-6000:])
                if READY in lines:
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('native UEFI compositor timed out: ' + log.read_text(errors='replace')[-6000:])
            count = 0
            while time.monotonic() < deadline:
                width, height, data = screenshot(endpoint, folder, count)
                count += 1
                initial = original_window(width, height, data)
                if initial and terminal_pixels_visible(width, height, data, initial):
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('two native application windows not visibly rendered')
            x, y, w, h = initial
            dx = -80 if x > 100 else 80
            dy = -60 if y > 90 else 60
            drag(endpoint, width, height, (x + 30, y + 14), (x + 30 + dx, y + 14 + dy))
            time.sleep(.3)
            frame = screenshot(endpoint, folder, count); count += 1
            moved = original_window(*frame)
            if not moved or abs(moved[0] - x) < 30 or abs(moved[1] - y) < 20:
                raise RuntimeError('actual pointer-driven native window movement missing')
            x, y, w, h = moved
            dx = 64 if width - x - w > 90 else -64
            dy = 48 if height - y - h > 70 else -48
            drag(endpoint, width, height, (x + w - 10, y + h - 10),
                 (x + w - 10 + dx, y + h - 10 + dy))
            time.sleep(.3)
            frame = screenshot(endpoint, folder, count); count += 1
            resized = original_window(*frame)
            if not resized or abs(resized[2] - w) < 20 or abs(resized[3] - h) < 20:
                raise RuntimeError('actual pointer-driven native window resize/repaint missing')
            x, y, _, _ = resized
            pointer(endpoint, width, height, x + 30, y + 14, button=True)
            pointer(endpoint, width, height, x + 30, y + 14, button=False)
            send_keys(endpoint, ('esc',), timeout=5)
            while time.monotonic() < deadline:
                lines = {line.strip() for line in log.read_bytes().splitlines()}
                if CLOSED in lines:
                    break
                if FAILED in lines or process.poll() is not None:
                    raise RuntimeError('native keyboard close killed compositor or terminal')
                time.sleep(.15)
            else:
                raise RuntimeError('native keyboard did not close focused client')
            frame = screenshot(endpoint, folder, count)
            if original_window(*frame) is not None:
                raise RuntimeError('closed native window remained visible')
            width, height, data = frame
            tx, ty = terminal_point(width, height, data)
            pointer(endpoint, width, height, tx, ty, button=True)
            pointer(endpoint, width, height, tx, ty, button=False)
            send_keys(endpoint, ('e', 'x', 'i', 't', 'ret'), timeout=5,
                      allowed=ALLOWED_TERMINAL_RETURN)
            while time.monotonic() < deadline:
                lines = {line.strip() for line in log.read_bytes().splitlines()}
                if TERMINAL in lines:
                    break
                if FAILED in lines or process.poll() is not None:
                    raise RuntimeError('terminal input or compositor survival failed')
                time.sleep(.15)
            else:
                raise RuntimeError('independent terminal did not execute fixed exit command')
            result = {'schema': 'synveil.native-weston-guest/v1',
                      'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                      'two_visible_windows': True, 'native_rgb_scanout': True,
                      'pointer_move_resize': True, 'keyboard_close': True,
                      'independent_terminal_keyboard_pty': True,
                      'authenticated': False, 'installed_persistence': False,
                      'physical_hardware_tested': False}
            (vm.OUT / 'guest-validation.json').write_text(json.dumps(result, indent=2) + '\n')
            print('SYNVEIL_NATIVE_QEMU_WESTON_RGB_WINDOWS_INPUT_PASS')
        finally:
            stop(process)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    run(args.timeout)
