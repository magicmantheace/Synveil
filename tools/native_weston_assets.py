#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Explicit dependency assets for a disposable native compositor VM only."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat

DATA = ('usr/share/weston/', 'usr/share/fonts/', 'usr/share/X11/xkb/',
        'usr/share/libinput/', 'usr/share/hwdata/', 'usr/share/fontconfig/',
        'etc/fonts/', 'etc/udev/', 'lib/udev/', 'usr/lib/udev/')
BINARIES = frozenset(('usr/bin/weston', 'usr/bin/weston-terminal',
    'usr/bin/weston-simple-shm', 'usr/libexec/weston-desktop-shell',
    'usr/libexec/weston-keyboard', 'usr/bin/seatd', 'usr/sbin/seatd',
    'sbin/seatd', 'bin/seatd', 'bin/udevd', 'sbin/udevd', 'usr/bin/udevd', 'usr/sbin/udevd',
    'bin/udevadm', 'sbin/udevadm', 'usr/bin/udevadm', 'usr/sbin/udevadm'))
ALIASES = frozenset(('lib64', 'usr/lib64'))


def allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        return False
    if name in BINARIES or name in ALIASES or name.startswith(DATA):
        return True
    return (name.startswith(('lib/', 'lib64/', 'usr/lib/', 'usr/lib64/')) and
            re.search(r'\.so(?:\.[0-9]+)*$', path.name) is not None)


def safe_link(name, target):
    if not target or '\0' in target:
        raise ValueError('invalid dependency symlink')
    if target == '/dev/null' and name.startswith(('etc/udev/rules.d/', 'lib/udev/rules.d/', 'usr/lib/udev/rules.d/')):
        return
    resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/')
                                 else posixpath.join(posixpath.dirname(name), target))
    if resolved == '..' or resolved.startswith('../') or not allowed(resolved):
        # Directory aliases are allowed only to the actual library directories.
        if not (name in ALIASES and resolved in ('lib', 'usr/lib')):
            raise ValueError('dependency symlink escapes component asset policy')


def entry(path, name):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        target = path.readlink().as_posix()
        safe_link(name, target)
        return {'kind': 'symlink', 'target': target}
    if name in ALIASES:
        raise ValueError('library directory alias must be a symlink')
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('dependency asset is not a file or symlink')
    return {'kind': 'file', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'executable': bool(info.st_mode & 0o111)}


def seal(target, destination):
    assets = {}
    for path in target.rglob('*'):
        name = path.relative_to(target).as_posix()
        if allowed(name) and (path.is_symlink() or path.is_file()):
            assets[name] = entry(path, name)
    required = ('usr/bin/weston', 'usr/bin/weston-terminal',
                'usr/libexec/weston-desktop-shell')
    if not all(name in assets for name in required):
        raise ValueError('native desktop assets incomplete')
    if not any(name in assets for name in ('usr/bin/seatd', 'usr/sbin/seatd', 'sbin/seatd', 'bin/seatd')):
        raise ValueError('source-built seat daemon missing')
    if not any(name.endswith('/udevd') for name in assets):
        raise ValueError('source-built device daemon missing')
    if not any(name.endswith('/udevadm') for name in assets):
        raise ValueError('source-built device management utility missing')
    document = {'schema': 'synveil.native-weston-assets/v1', 'assets': assets}
    destination.write_text(json.dumps(document, sort_keys=True, indent=2) + '\n')
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def verified(target, manifest, expected):
    if manifest.is_symlink() or hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
        raise ValueError('native dependency asset manifest changed')
    document = json.loads(manifest.read_text())
    if document.get('schema') != 'synveil.native-weston-assets/v1':
        raise ValueError('unreviewed native dependency asset schema')
    assets = document['assets']
    for name, identity in assets.items():
        for parent in (target / name).parents:
            if parent == target:
                break
            if parent.is_symlink():
                raise ValueError('dependency source parent is a symlink')
        if not allowed(name) or entry(target / name, name) != identity:
            raise ValueError('native dependency asset changed or is forbidden: ' + name)
    return assets
