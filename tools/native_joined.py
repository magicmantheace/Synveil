#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build the joined authenticated Weston image: one bootable image whose
graphical login/enrollment gates the multiwindow Weston desktop, with
state on the image's own designated state partition (ADR 0017).

This is a packaging step over the existing authoritative builders, in
the same shape as tools/native_weston_vm.py but with the production
authenticated profile instead of the disposable fixture: the native
bundle is rebuilt at the current source revision, the rootfs is the
production native+graphics+persistence+autostart+auth-desktop profile,
and the Weston payload (sealed source-built assets, the
weston-session marker and scripts, the seat group, and the first
window) is overlaid onto that rootfs. The QEMU hardware-identity
gate of experiments/native-weston exists only in that fixture's own
init and is never part of this image: the production init is used
unchanged.

Nothing here proves the image boots; tests/smoke-native-joined.py is
the acceptance driver and docs/validation records what it observed.
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

# Fixed GPT identity of the state partition inside the image (ADR 0017).
STATE_PARTUUID = "7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053"
TARGET = "x86_64-synveil-linux-gnu"


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


def base_env(out, work):
    env = dict(os.environ)
    # Ignore caller-selected disk/profile/work roots: this command
    # produces only its fixed output tree and never attaches foreign
    # persistence or networking profiles.
    for key in list(env):
        if key.startswith('SYNVEIL_') and key not in (
                'SYNVEIL_SOURCE_TRANSPORT', 'SYNVEIL_JOBS'):
            env.pop(key)
    env.setdefault('RUSTUP_HOME', str(Path.home() / 'workspace/synveil-tools/rustup'))
    env.setdefault('CARGO_HOME', str(Path.home() / 'workspace/synveil-tools/cargo'))
    env['PATH'] = env['CARGO_HOME'] + '/bin:' + env.get('PATH', '')
    env['TAR_OPTIONS'] = '--no-same-owner'
    env.update(SYNVEIL_OUT_DIR=str(out), SYNVEIL_WORK_DIR=str(work))
    return env


def build_native_bundle(out, work):
    """Rebuild the static Rust bundle at the current revision into
    <out>/native, through the stage-1-layout toolchain copy the native
    preflight requires (see tools/native_build.py)."""
    stage1 = ROOT / 'build/work/toolchain-stage1-copy'
    toolchain_out = ROOT / 'build/toolchain/out'
    stage1_libgcc = (ROOT / 'build/work/toolchain/gcc-stage1' / TARGET /
                     'libgcc/libgcc.a')
    if not toolchain_out.is_dir() or not stage1_libgcc.is_file():
        raise ValueError('toolchain outputs missing; run the toolchain build first')
    if stage1.exists():
        shutil.rmtree(stage1)
    shutil.copytree(toolchain_out, stage1, symlinks=True)
    compiler = stage1 / 'bin' / (TARGET + '-gcc')
    printed = subprocess.check_output(
        [str(compiler), '-print-libgcc-file-name'], text=True).strip()
    shutil.copyfile(stage1_libgcc, printed)
    run([sys.executable, str(ROOT / 'tools/native_build.py'),
         '--toolchain-dir', str(stage1),
         '--sysroot', str(stage1 / TARGET / 'sysroot'),
         '--target', TARGET,
         '--work-dir', str(ROOT / 'build/work/native'),
         '--out-dir', str(out)],
        cwd=ROOT, env=base_env(out, work))


def build(boot_target, esp_size_mb, state_size_mb):
    if boot_target not in ('qemu', 'hardware'):
        raise ValueError('boot target must be qemu or hardware')
    out = ROOT / 'out' / ('native-joined' if boot_target == 'qemu'
                          else 'native-joined-hardware')
    if (ROOT / 'out').is_symlink() or out.exists() or out.is_symlink():
        raise ValueError('joined image output must be new: ' + str(out))
    tools, _sysroot = ffi.target_tools()
    receipt = json.loads(regular(weston.OUT / 'validation.json').read_text())
    if (receipt.get('schema') != 'synveil.native-weston-stage/v1' or
            receipt.get('target') != ffi.TARGET or
            receipt.get('recipe_commit') != weston.RECIPE_COMMIT or
            receipt.get('weston_source_sha256') != weston.WESTON_SHA256):
        raise ValueError('native compositor build identity mismatch')
    target = weston.OUT / 'build/target'
    selected = assets.verified(target, weston.OUT / 'runtime-assets.json',
                               receipt.get('runtime_assets_sha256'))
    out.mkdir(mode=0o700)
    work = out / 'work'
    rootfs = work / 'rootfs'
    tmp = out / 'tmp'
    tmp.mkdir()

    build_native_bundle(out, work)

    env = base_env(out, work)
    env.update(SYNVEIL_ROOTFS_DIR=str(rootfs),
               SYNVEIL_NATIVE_ROOTFS='1', SYNVEIL_GRAPHICS_ROOTFS='1',
               SYNVEIL_PERSISTENCE_ROOTFS='1', SYNVEIL_DESKTOP_AUTOSTART='1',
               SYNVEIL_AUTH_DESKTOP_ROOTFS='1',
               TMPDIR=str(tmp))
    run(['bash', str(ROOT / 'build.sh'), 'rootfs'], cwd=ROOT, env=env)

    # Sealed Weston payload, copied with the fixture builder's rules:
    # real files first, directory aliases last, never replacing a file
    # the production rootfs already owns.
    for name, identity in sorted(selected.items()):
        if name not in assets.ALIASES:
            copy_asset(target, rootfs, name, identity)
    for name in sorted(assets.ALIASES & selected.keys()):
        copy_asset(target, rootfs, name, selected[name])

    runtime = rootfs / 'usr/libexec/synveil'
    runtime.mkdir(parents=True, exist_ok=True)
    for script in ('weston-session', 'weston-seat-prepare', 'weston-seat-cleanup'):
        shutil.copyfile(ROOT / 'build/rootfs' / script, runtime / script)
        (runtime / script).chmod(0o755)
    config = rootfs / 'etc/synveil'
    config.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'build/rootfs/weston.ini', config / 'weston.ini')
    (config / 'weston.ini').chmod(0o644)
    # The marker veil-seat validates before it will launch the Weston
    # session: empty, regular, root-owned by construction, mode 0644.
    (config / 'weston-session').write_text('')
    (config / 'weston-session').chmod(0o644)
    # seatd's group must exist and be the session user's primary group
    # (the production passwd entry already pins synveil to 1000:1000).
    group = rootfs / 'etc/group'
    group_text = group.read_text()
    if 'seat:' not in group_text:
        group.write_text(group_text + 'seat:x:1000:synveil\n')
    group.chmod(0o644)
    (rootfs / 'etc/passwd').chmod(0o644)

    generated = out / 'generated'
    generated.mkdir()
    scanner = regular(weston.OUT / 'build/host/bin/wayland-scanner')
    version = subprocess.check_output([str(scanner), '--version'],
                                      stderr=subprocess.STDOUT, text=True).strip()
    if version != 'wayland-scanner 1.26.0':
        raise ValueError('joined build requires pinned scanner source version')
    xml = (weston.OUT / 'sources/protocols/tree/wayland-protocols-1.49'
           '/stable/xdg-shell/xdg-shell.xml')
    run([scanner, 'client-header', xml, generated / 'xdg-shell-client.h'])
    run([scanner, 'private-code', xml, generated / 'xdg-shell-code.c'])
    alias = weston.OUT / 'build/staging'
    if not alias.is_dir() or not alias.resolve().is_relative_to(weston.OUT.resolve()):
        raise ValueError('private native dependency sysroot missing')
    staging = alias.resolve()
    output = rootfs / 'usr/bin/synveil-first-window'
    run([tools['gcc'], '--sysroot=' + str(staging), '-std=c11', '-Wall',
         '-Wextra', '-Werror', '-I' + str(generated),
         ROOT / 'src/desktop-wayland/first-window.c',
         generated / 'xdg-shell-code.c', '-lwayland-client', '-o', output])
    header = subprocess.check_output([str(tools['readelf']), '-h', str(output)], text=True)
    program = subprocess.check_output([str(tools['readelf']), '-l', str(output)], text=True)
    if 'Advanced Micro Devices X86-64' not in header or \
            '/lib64/ld-linux-x86-64.so.2' not in program:
        raise ValueError('joined application did not link to native target glibc')

    # Directory and file modes must not depend on the builder's umask
    # (same normalization as the fixture builder): system directories
    # 0755, files keep only their executable bit distinction.
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

    with (out / 'synveil-initramfs.cpio').open('wb') as archive:
        run(['bash', '-o', 'pipefail', '-c',
             'find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0'],
            cwd=rootfs, stdout=archive, env=env)
    (out / 'synveil-initramfs.cpio.zst').unlink(missing_ok=True)

    kernel_env = dict(env)
    kernel_env['SYNVEIL_BOOT_TARGET'] = boot_target
    if boot_target == 'qemu':
        # Reuse the base kernel objects; the seed identity (config +
        # bzImage digest) is verified by kernel.sh itself. The hardware
        # profile changes the config and always builds in full.
        kernel_env['SYNVEIL_KERNEL_SEED_WORK'] = str(ROOT / 'build/work')
    run(['bash', str(ROOT / 'build.sh'), 'kernel'], cwd=ROOT, env=kernel_env)
    kernel_config = (out / 'manifests/kernel.config').read_text().splitlines()
    if not all('CONFIG_' + name + '=y' in kernel_config
               for name in ('UNIX98_PTYS', 'DEVTMPFS', 'TMPFS')):
        raise ValueError('joined image requires kernel PTYs and device/tmp filesystems')
    built_target = (out / 'manifests/kernel-boot-target.txt').read_text().strip()
    if built_target != boot_target:
        raise ValueError('kernel boot target mismatch: ' + built_target)

    image_env = dict(env)
    image_env.update(SYNVEIL_ESP_SIZE_MB=str(esp_size_mb),
                     SYNVEIL_STATE_SIZE_MB=str(state_size_mb))
    run(['bash', str(ROOT / 'build.sh'), 'image'], cwd=ROOT, env=image_env)

    proof = {'schema': 'synveil.native-joined-image/v1', 'target': ffi.TARGET,
             'boot_target': boot_target,
             'image_sha256': hashlib.sha256(
                 (out / 'synveil-x86_64.img').read_bytes()).hexdigest(),
             'native_app_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
             'component_assets_sha256': receipt['runtime_assets_sha256'],
             'esp_size_mb': esp_size_mb, 'state_size_mb': state_size_mb,
             'state_partuuid': STATE_PARTUUID,
             'authenticated': True, 'persistent': True,
             'physical_boot_validated': False}
    (out / 'validation.json').write_text(json.dumps(proof, indent=2) + '\n')
    print('SYNVEIL_NATIVE_JOINED_IMAGE_BUILT_NOT_BOOT_VALIDATED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true', required=True)
    parser.add_argument('--boot-target', choices=('qemu', 'hardware'),
                        default='qemu')
    parser.add_argument('--esp-size-mb', type=int, default=256)
    parser.add_argument('--state-size-mb', type=int, default=128)
    args = parser.parse_args()
    build(args.boot_target, args.esp_size_mb, args.state_size_mb)
