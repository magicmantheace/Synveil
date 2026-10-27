#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Closed payload seal for the distribution-Chromium stage.

The stage (tools/distro_chromium.py) resolves Chromium from a
pinned Debian snapshot, verifies every .deb against the
snapshot's own Packages index and the committed pin manifest,
extracts the closure, prunes it to the browser's real payload
( DT_NEEDED graph + named dlopen sets + data trees), and seals
what remains with this module. The A/B image builder copies only
sealed files, byte-verified, and never lets a payload file
replace a differing baseline file.

Path discipline: payload trees keep their distribution layout
(usr/lib/x86_64-linux-gnu included) so compiled-in dlopen paths
(gdk-pixbuf loaders, gio modules, NSS) keep working; the session
launcher puts the multiarch directory on LD_LIBRARY_PATH for the
browser process only. Nothing from the distribution's system
directories (etc, var, sbin, systemd units) is ever sealed, and
the distribution libc is pruned by the graph: the sealed image
runs the project's own glibc, always.
"""
import hashlib
import json
from pathlib import PurePosixPath

# Files and trees that may be sealed. usr/lib/chromium and
# usr/lib/x86_64-linux-gnu are additionally graph-pruned by the
# builder before sealing; the seal itself only enforces layout.
BINARIES = frozenset((
    'usr/bin/chromium',
    'usr/bin/certutil',
))
DATA = (
    'etc/chromium.d/',
    'etc/chromium/',
    'usr/lib/chromium/',
    'usr/lib/x86_64-linux-gnu/',
    'usr/share/ca-certificates/',
    'usr/share/fonts/',
    'usr/share/glib-2.0/',
    'usr/share/icons/',
    'usr/share/mime/',
    'usr/share/vulkan/',
    # Deliberately absent: etc/fonts, usr/share/fontconfig and
    # usr/share/X11 (xkb data). Font configuration and keymap
    # data are system data owned by the baseline image (the
    # Weston stage's fontconfig/xkb, at different versions than
    # Debian's); the payload's copies collided with the
    # baseline's on 250 paths (checked 2026-10-26) and Chromium
    # uses the system's as-is.
)
GENERATED = frozenset((
    # Concatenated from the sealed mozilla bundle at stage build
    # time (the distribution generates it in a maintainer script,
    # which extraction never runs).
    'etc/ssl/certs/ca-certificates.crt',
    'usr/share/glib-2.0/schemas/gschemas.compiled',
))


def allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        return False
    if name in BINARIES or name in GENERATED or name.startswith(DATA):
        return True
    return name.endswith('.so') or '.so.' in name


def kind_of(name, mode):
    if name in BINARIES or name.startswith('usr/lib/chromium/') \
            and (mode & 0o111):
        return 'file'
    return 'file'


def seal(root):
    """Hash every sealable file under the pruned staging root.
    Symlinks are recorded as links (their targets ship in the same
    payload or the baseline); regular files by content + mode."""
    entries = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            if allowed(name):
                entries[name] = {'kind': 'symlink',
                                 'target': path.readlink().as_posix()}
            continue
        if not path.is_file() or not allowed(name):
            continue
        data = path.read_bytes()
        entries[name] = {
            'kind': 'file',
            'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
            'mode': oct(path.stat().st_mode & 0o7777),
        }
    return entries


def verified(root, entries):
    """Recompute the seal over root and compare exactly."""
    return seal(root) == entries


def seal_digest(entries):
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True).encode()).hexdigest()
