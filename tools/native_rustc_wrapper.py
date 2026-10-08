#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Keep Cargo host links separate from Synveil target links."""
import os
from pathlib import Path
import sys


def compiler_arguments(arguments, environment):
    args = list(arguments)
    if "--crate-name" not in args:
        return args
    target = "--target" in args
    linker = environment["SYNVEIL_NATIVE_GCC"] if target else "cc"
    args += ["-C", "linker=" + linker]
    if target and "--crate-type" in args and args[args.index("--crate-type") + 1] == "bin":
        name = args[args.index("--crate-name") + 1]
        path = Path(environment["SYNVEIL_NATIVE_MAP_DIR"]) / (name + ".map")
        args += ["-C", "link-arg=-Wl,-Map=" + str(path)]
    return args


if __name__ == "__main__":
    compiler, *arguments = sys.argv[1:]
    os.execvp(compiler, [compiler, *compiler_arguments(arguments, os.environ)])
