#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""A concurrent clean must not delete an active checkout's build outputs."""
import fcntl
from pathlib import Path
import shutil
import subprocess
import tempfile

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "build.sh", root / "build.sh")
    (root / "out").mkdir()
    marker = root / "out" / "active-build"
    marker.write_text("preserve")
    with (root / ".synveil-build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(["bash", str(root / "build.sh"), "clean"], capture_output=True, text=True)
        assert result.returncode != 0 and "another build command" in result.stderr
        assert marker.read_text() == "preserve"
    subprocess.run(["bash", str(root / "build.sh"), "clean"], check=True)
    assert not (root / "out").exists()
print("[build-lock] concurrent clean rejected; unlocked clean succeeds")
