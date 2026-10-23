#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Stage only source-built native C++ runtimes and verify a real C++ ELF link."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_libffi as ffi

RUNTIMES = ("libgcc_s.so.1", "libstdc++.so.6")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_elf(readelf, path):
    output = subprocess.check_output([str(readelf), "-h", str(path)], text=True, timeout=15)
    if "Advanced Micro Devices X86-64" not in output:
        raise ValueError("C++ runtime is not a native x86_64 ELF: " + path.name)


def verify():
    tools, sysroot = ffi.target_tools()
    folder = ROOT / "out/native-cxx"
    receipt = json.loads((folder / "validation.json").read_text())
    if (receipt.get("schema") != "synveil.native-cxx/v1" or
            receipt.get("target") != ffi.TARGET or
            "cxx=1" not in (ffi.TOOLCHAIN / "SYNVEIL-TOOLCHAIN").read_text().splitlines()):
        raise ValueError("native C++ identity mismatch")
    for name in RUNTIMES:
        path = sysroot / "usr/lib64" / name
        if digest(path) != receipt.get("runtime_sha256", {}).get(name):
            raise ValueError("cached native runtime changed")
        target_elf(tools["readelf"], path)
    binary = folder / "runtime-check"
    if digest(binary) != receipt.get("linked_elf_sha256"):
        raise ValueError("native C++ link proof changed")
    target_elf(tools["readelf"], binary)
    print("SYNVEIL_NATIVE_CXX_CACHE_IDENTITY_VERIFIED_NOT_EXECUTED")


def stage():
    if os.geteuid() == 0:
        raise ValueError("refusing privileged native C++ runtime staging")
    tools, sysroot = ffi.target_tools()
    compiler = ffi.TOOLCHAIN / "bin" / (ffi.TARGET + "-g++")
    if not compiler.is_file() or compiler.is_symlink():
        raise ValueError("source-built native g++ missing")
    env = dict(os.environ)
    for key in ("CPLUS_INCLUDE_PATH", "C_INCLUDE_PATH", "CPATH", "LIBRARY_PATH",
                "COMPILER_PATH", "GCC_EXEC_PREFIX", "CFLAGS", "CXXFLAGS", "LDFLAGS"):
        env.pop(key, None)
    identities = {}
    destination = sysroot / "usr/lib64"
    for name in RUNTIMES:
        value = subprocess.check_output([str(compiler), "-print-file-name=" + name],
                                        env=env, text=True, timeout=15).strip()
        source = Path(value).resolve(strict=True)
        if not source.is_relative_to(ffi.TOOLCHAIN.resolve()) or not source.is_file():
            raise ValueError("C++ runtime resolved outside native toolchain")
        target_elf(tools["readelf"], source)
        output = destination / name
        # Never follow an existing output link into the host or outside sysroot.
        if output.is_symlink():
            if not output.resolve().is_relative_to(sysroot.resolve()):
                raise ValueError("unsafe C++ runtime destination")
            output.unlink()
        if source != output:
            shutil.copyfile(source, output)
            output.chmod(0o755)
        identities[name] = digest(output)
    output = ROOT / "out/native-cxx"
    if (ROOT / "out").is_symlink() or output.exists() or output.is_symlink():
        raise ValueError("refusing existing or symlinked C++ verification output")
    output.mkdir(parents=True, mode=0o700)
    source = output / "runtime-check.cc"
    binary = output / "runtime-check"
    source.write_text("""#include <string>
#include <thread>
#include <vector>
#include <stdexcept>
int main() {
    std::string result;
    std::thread worker([&result] { result = "Synveil"; });
    worker.join();
    std::vector<int> values{1, 2, 3};
    try { if (values.size() == 3) throw std::runtime_error(result); }
    catch (const std::runtime_error& error) {
        return std::string(error.what()) == "Synveil" ? 0 : 2;
    }
    return 1;
}
""")
    subprocess.run([str(compiler), "--sysroot=" + str(sysroot), "-std=c++17",
                    "-Wall", "-Wextra", "-Werror", "-pthread", str(source),
                    "-o", str(binary)], env=env, check=True, timeout=120)
    target_elf(tools["readelf"], binary)
    segments = subprocess.check_output([str(tools["readelf"]), "-l", str(binary)],
                                       text=True, timeout=15)
    if "/lib64/ld-linux-x86-64.so.2" not in segments:
        raise ValueError("C++ program does not use native glibc loader")
    dynamic = subprocess.check_output([str(tools["readelf"]), "-d", str(binary)],
                                      text=True, timeout=15)
    if not all("[" + name + "]" in dynamic for name in RUNTIMES):
        raise ValueError("C++ link did not exercise both native shared runtimes")
    receipt = {"schema": "synveil.native-cxx/v1", "target": ffi.TARGET,
               "runtime_sha256": identities, "linked_elf_sha256": digest(binary),
               "runtime_executed": False, "boot_image_changed": False}
    (output / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    marker = ffi.TOOLCHAIN / "SYNVEIL-TOOLCHAIN"
    lines = [line for line in marker.read_text().splitlines() if not line.startswith("cxx=")]
    marker.write_text("\n".join(lines + ["cxx=1"]) + "\n")
    print("SYNVEIL_NATIVE_CXX_RUNTIME_STAGED_AND_LINKED_NOT_EXECUTED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--stage", action="store_true")
    modes.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify() if args.verify else stage()
