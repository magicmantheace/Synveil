#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Stage pinned GCC libatomic and require a real 128-bit atomic ELF link."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import native_libffi as ffi
from native_cxx_runtime import digest, target_elf

OUT = ROOT / 'out/native-atomic'
NAMES = ('libatomic.so', 'libatomic.so.1', 'libatomic.a')


def verify():
    tools, sysroot = ffi.target_tools()
    proof = json.loads((OUT / 'validation.json').read_text())
    if proof.get('schema') != 'synveil.native-atomic/v1' or proof.get('target') != ffi.TARGET:
        raise ValueError('native atomic runtime identity mismatch')
    for name in NAMES:
        path = sysroot / 'usr/lib64' / name
        if path.is_symlink() or digest(path) != proof['runtime_sha256'].get(name):
            raise ValueError('native atomic runtime bytes changed')
    target_elf(tools['readelf'], sysroot / 'usr/lib64/libatomic.so.1')
    if digest(OUT / 'atomic-check') != proof['linked_elf_sha256']:
        raise ValueError('native atomic link proof changed')
    return proof


def stage(source):
    if os.geteuid() == 0 or OUT.exists() or OUT.is_symlink() or (ROOT / 'out').is_symlink():
        raise ValueError('atomic runtime needs non-root and fresh private output')
    tools, sysroot = ffi.target_tools()
    expected = ROOT / 'build/work/toolchain/atomic/.libs'
    if source.resolve() != expected.resolve() or source.is_symlink():
        raise ValueError('atomic runtime must come from its source-native build')
    identities = {}
    for name in NAMES:
        path = (source / name).resolve(strict=True)
        if not path.is_relative_to(source.resolve()) or not path.is_file():
            raise ValueError('atomic source escapes its native build')
        if name.endswith(('.so', '.so.1')):
            target_elf(tools['readelf'], path)
        output = sysroot / 'usr/lib64' / name
        if output.is_symlink():
            raise ValueError('atomic destination must not be a symlink')
        shutil.copyfile(path, output)
        identities[name] = digest(output)
    OUT.mkdir(mode=0o700)
    source_c = OUT / 'atomic-check.c'
    source_c.write_text('''#include <stdint.h>
static unsigned __int128 value;
int main(void) {
    unsigned __int128 expected = 0;
    unsigned __int128 desired = ((unsigned __int128)1 << 80) | 7;
    if (!__atomic_compare_exchange_n(&value, &expected, desired, 0,
                                    __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) return 1;
    return __atomic_load_n(&value, __ATOMIC_SEQ_CST) != desired;
}
''')
    binary = OUT / 'atomic-check'
    subprocess.run([str(tools['gcc']), '--sysroot=' + str(sysroot), '-std=c11',
                    '-Wall', '-Wextra', '-Werror', str(source_c), '-latomic',
                    '-o', str(binary)], check=True)
    target_elf(tools['readelf'], binary)
    dynamic = subprocess.check_output([str(tools['readelf']), '-d', str(binary)], text=True)
    if '[libatomic.so.1]' not in dynamic:
        raise ValueError('atomic proof did not link the native shared runtime')
    proof = {'schema': 'synveil.native-atomic/v1', 'target': ffi.TARGET,
             'runtime_sha256': identities, 'linked_elf_sha256': digest(binary),
             'runtime_executed': False}
    (OUT / 'validation.json').write_text(json.dumps(proof, indent=2) + '\n')
    verify()
    print('SYNVEIL_NATIVE_ATOMIC_RUNTIME_STAGED_AND_LINKED_NOT_EXECUTED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.stage and not args.verify:
        stage(args.stage)
    elif args.verify and not args.stage:
        verify()
    else:
        parser.error('choose --stage or --verify')
