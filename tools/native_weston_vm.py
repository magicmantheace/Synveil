#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Package explicitly reviewed native dependency assets into a Synveil VM fixture.

The existing Synveil BusyBox rootfs/kernel/image builders remain authoritative.
Never import the package builder's OS rootfs, accounts, init or bootloader.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import native_libffi as ffi
import native_weston as weston
import native_weston_assets as assets

OUT = ROOT / 'out/native-weston-vm'
FIXTURE = ROOT / 'experiments/native-weston'


def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def regular(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('expected regular source-built file: ' + str(path))
    return path


def copy_asset(source, destination, name, identity):
    relative = Path(name)
    if not assets.allowed(name):
        raise ValueError('forbidden native dependency asset')
    target = destination / relative
    parent = destination
    for part in relative.parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ValueError('asset destination parent is a symlink')
        parent.mkdir(exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ValueError('refusing to replace a native base-system file: ' + name)
    if identity['kind'] == 'symlink':
        assets.safe_link(name, identity['target'])
        target.symlink_to(identity['target'])
    else:
        with target.open('xb') as output:
            output.write((source / relative).read_bytes())
        target.chmod(0o755 if identity['executable'] else 0o644)


def build():
    if os.geteuid() == 0:
        raise ValueError('refusing privileged compositor fixture build')
    if (ROOT / 'out').is_symlink() or OUT.exists() or OUT.is_symlink():
        raise ValueError('native compositor fixture output must be new')
    tools, sysroot = ffi.target_tools()
    receipt = json.loads(regular(weston.OUT / 'validation.json').read_text())
    if (receipt.get('schema') != 'synveil.native-weston-stage/v1' or
            receipt.get('target') != ffi.TARGET or
            receipt.get('recipe_commit') != weston.RECIPE_COMMIT or
            receipt.get('weston_source_sha256') != weston.WESTON_SHA256):
        raise ValueError('native compositor build identity mismatch')
    target = weston.OUT / 'build/target'
    selected = assets.verified(target, weston.OUT / 'runtime-assets.json',
                               receipt.get('runtime_assets_sha256'))
    OUT.mkdir(mode=0o700)
    work = OUT / 'work'
    rootfs = work / 'rootfs'
    env = dict(os.environ)
    # Ignore caller-selected disk/profile/work roots. This command produces only
    # its fixed disposable image and never attaches persistence or networking.
    for key in list(env):
        if key.startswith('SYNVEIL_') and key not in ('SYNVEIL_SOURCE_TRANSPORT', 'SYNVEIL_JOBS'):
            env.pop(key)
    env.update(SYNVEIL_OUT_DIR=str(OUT), SYNVEIL_WORK_DIR=str(work),
               SYNVEIL_ROOTFS_DIR=str(rootfs), SYNVEIL_BOOT_TARGET='qemu')
    run(['bash', str(ROOT / 'build.sh'), 'rootfs'], cwd=ROOT, env=env)
    for name, identity in sorted(selected.items()):
        # Copy aliases after real directory contents. Never traverse a link
        # while writing library assets into the independently built native base.
        if name not in assets.ALIASES:
            copy_asset(target, rootfs, name, identity)
    for name in sorted(assets.ALIASES & selected.keys()):
        copy_asset(target, rootfs, name, selected[name])
    runtime = rootfs / 'usr/libexec/synveil'
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE / 'init', rootfs / 'init')
    (rootfs / 'init').chmod(0o755)
    shutil.copyfile(FIXTURE / 'session', runtime / 'native-weston-vm-session')
    (runtime / 'native-weston-vm-session').chmod(0o755)
    config = rootfs / 'etc/synveil'
    config.mkdir(exist_ok=True)
    shutil.copyfile(FIXTURE / 'weston.ini', config / 'weston-vm.ini')
    (config / 'weston-vm.ini').chmod(0o644)
    (config / 'native-weston-vm-only').write_text('disposable QEMU fixture; not owner authentication\n')
    (config / 'native-weston-vm-only').chmod(0o644)
    # Own fixed, locked fixture identities. The recipe builder's account files
    # are never imported; no credential enrollment or root serial shell exists.
    (rootfs / 'etc/passwd').write_text(
        'root:x:0:0:root:/root:/bin/false\n'
        'synveil:x:1000:1000:Synveil VM fixture:/home/synveil:/bin/sh\n')
    (rootfs / 'etc/group').write_text('root:x:0:\nseat:x:1000:synveil\n')
    # Account databases and the fixture config above are read by the
    # unprivileged session user; like the directory modes, their file
    # modes must not be left to the builder's umask (0660 under 0007
    # made weston-vm.ini unreadable and weston exited fatally).
    (rootfs / 'etc/passwd').chmod(0o644)
    (rootfs / 'etc/group').chmod(0o644)
    (rootfs / 'etc/shadow').write_text('root:!:0:0:99999:7:::\nsynveil:!:0:0:99999:7:::\n')
    (rootfs / 'etc/shadow').chmod(0o600)
    generated = OUT / 'generated'
    generated.mkdir()
    scanner = regular(weston.OUT / 'build/host/bin/wayland-scanner')
    version = subprocess.check_output([str(scanner), '--version'], stderr=subprocess.STDOUT, text=True).strip()
    if version != 'wayland-scanner 1.26.0':
        raise ValueError('native fixture requires pinned scanner source version')
    xml = weston.OUT / 'sources/protocols/tree/wayland-protocols-1.49/stable/xdg-shell/xdg-shell.xml'
    run([scanner, 'client-header', xml, generated / 'xdg-shell-client.h'])
    run([scanner, 'private-code', xml, generated / 'xdg-shell-code.c'])
    # Buildroot's staging alias is authoritative for its cross-built headers;
    # compiler/runtime provenance and target libc byte identity were checked by
    # the component builder before sealing this asset set.
    alias = weston.OUT / 'build/staging'
    if not alias.is_dir() or not alias.resolve().is_relative_to(weston.OUT.resolve()):
        raise ValueError('private native dependency sysroot missing')
    staging = alias.resolve()
    output = rootfs / 'usr/bin/synveil-first-window'
    run([tools['gcc'], '--sysroot=' + str(staging), '-std=c11', '-Wall', '-Wextra', '-Werror',
         '-I' + str(generated), ROOT / 'src/desktop-wayland/first-window.c',
         generated / 'xdg-shell-code.c', '-lwayland-client', '-o', output])
    header = subprocess.check_output([str(tools['readelf']), '-h', str(output)], text=True)
    program = subprocess.check_output([str(tools['readelf']), '-l', str(output)], text=True)
    if 'Advanced Micro Devices X86-64' not in header or '/lib64/ld-linux-x86-64.so.2' not in program:
        raise ValueError('fixture application did not link to native target glibc')
    # Directory modes must not depend on the builder's umask: the base
    # rootfs builder and the asset copies inherit it, so a restrictive
    # umask (e.g. 0007) silently produces 0770 system directories that
    # the unprivileged fixture user cannot traverse (su: can't execute
    # '/bin/sh'). Normalize every directory to the conventional system
    # mode; the fixture init still tightens the private runtime
    # directories (home, /run/user/1000) to 0700 before the session.
    for folder, subdirs, files in os.walk(rootfs):
        Path(folder).chmod(0o755)
        for name in subdirs:
            entry = Path(folder) / name
            if not entry.is_symlink():
                entry.chmod(0o755)
        for name in files:
            entry = Path(folder) / name
            if entry.is_symlink() or entry == rootfs / 'etc/shadow':
                continue
            entry.chmod(0o755 if entry.stat().st_mode & 0o111 else 0o644)
    # Repack only the separately built Synveil root plus explicit component
    # assets. These commands address regular files; there is no loop/mount/dd
    # path to an owner disk, and no generated dependency-system rootfs import.
    with (OUT / 'synveil-initramfs.cpio').open('wb') as archive:
        run(['bash', '-o', 'pipefail', '-c',
             'find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0'],
            cwd=rootfs, stdout=archive, env=env)
    (OUT / 'synveil-initramfs.cpio.zst').unlink(missing_ok=True)
    run(['bash', str(ROOT / 'build.sh'), 'kernel'], cwd=ROOT, env=env)
    kernel_config = (OUT / 'manifests/kernel.config').read_text().splitlines()
    if not all('CONFIG_' + name + '=y' in kernel_config
               for name in ('UNIX98_PTYS', 'DEVTMPFS', 'TMPFS')):
        raise ValueError('native terminal requires kernel PTYs and device/tmp filesystems')
    run(['bash', str(ROOT / 'build.sh'), 'image'], cwd=ROOT, env=env)
    proof = {'schema': 'synveil.native-weston-vm-image/v1', 'target': ffi.TARGET,
             'image_sha256': hashlib.sha256((OUT / 'synveil-x86_64.img').read_bytes()).hexdigest(),
             'native_app_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
             'component_assets_sha256': receipt['runtime_assets_sha256'],
             'authenticated': False, 'persistent': False, 'physical_boot_validated': False}
    (OUT / 'validation.json').write_text(json.dumps(proof, indent=2) + '\n')
    print('SYNVEIL_NATIVE_WESTON_VM_IMAGE_BUILT_NOT_BOOT_VALIDATED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true', required=True)
    parser.parse_args()
    build()
