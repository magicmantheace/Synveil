#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Run every existing Python unittest entry point, without guest boots.

Guest drivers are deliberately not invoked without their built images.
Every result, including failures and timeouts, is retained in the summary.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "out/muse-all-host-tests"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for test in sorted((root / "tests").glob("*.py")):
        if test.name == Path(__file__).name:
            continue
        if "unittest.main(" not in test.read_text():
            continue
        try:
            proc = subprocess.run([sys.executable, str(test)], cwd=root,
                                  capture_output=True, timeout=args.timeout)
            data = proc.stdout + proc.stderr
            code = proc.returncode
        except subprocess.TimeoutExpired as error:
            data = (error.stdout or b"") + (error.stderr or b"")
            code = 124
        (output / (test.stem + ".log")).write_bytes(data)
        counts = re.findall(rb"Ran (\d+) tests?", data)
        item = {"suite": test.name, "exit_code": code,
                "reported_tests": sum(int(count) for count in counts)}
        results.append(item)
        print(json.dumps(item), flush=True)
    summary = {"scope": "host unittests; no guest or physical boot",
               "suites": results,
               "passed": sum(item["exit_code"] == 0 for item in results),
               "failed": sum(item["exit_code"] != 0 for item in results)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "suites"}))
    return int(bool(summary["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
