#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Require real Cairo pthread detection and retain its compiler diagnostics."""
import argparse
from pathlib import Path
import re


def require_pthreads(config, log):
    text = config.read_text()
    if re.search(r'^#define CAIRO_HAS_PTHREAD 1\s*$', text, re.MULTILINE):
        print('SYNVEIL_CAIRO_REAL_PTHREAD_BACKEND_CONFIGURED')
        return
    trace = log.read_text(errors='replace')
    # Meson appends the named test after the corresponding compiler invocation.
    # Keep the surrounding command/source/error blocks, rather than Cairo's
    # later secondary missing-mutex macro errors.
    chunks = []
    for match in re.finditer(r'Checking if .*pthreads.*', trace):
        start = trace.rfind('Running compile:', 0, match.start())
        if start < 0:
            start = max(0, match.start() - 4000)
        chunks.append(trace[start:match.end()])
    message = ('Cairo pthread compile/link detection failed; mutexes remain required.\n' +
               '\n'.join(chunks))[-14000:]
    print(message, flush=True)
    escaped = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print('::error::' + escaped, flush=True)
    raise RuntimeError('native Cairo pthread capability was not detected')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    args = parser.parse_args()
    require_pthreads(args.build / 'config.h', args.build / 'meson-logs/meson-log.txt')
