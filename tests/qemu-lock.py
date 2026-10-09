#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""The smoke launcher must close the checkout lock before spawning QEMU."""
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "tests").mkdir()
    (root / "build/scripts").mkdir(parents=True)
    for name in ("tests/smoke-boot.sh", "build/scripts/common.sh"):
        shutil.copyfile(ROOT / name, root / name)
    (root / "build/scripts/qemu.sh").write_text('''
if [ -e /proc/$$/fd/9 ]; then exit 7; fi
printf 'SYNVEIL_BOOT_OK\\n'
''')
    with (root / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        command = 'exec 9>"$1"; bash "$2"'
        result = subprocess.run(
            ["bash", "-c", command, "lock-test", str(root / "lock"),
             str(root / "tests/smoke-boot.sh")],
            env={**os.environ, "SYNVEIL_OUT_DIR": str(root / "out")},
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, result.stderr
print("[qemu-lock] checkout lock is not inherited by smoke/QEMU child")
