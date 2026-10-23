#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import importlib.util
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_cairo_probe', ROOT / 'tools/native_cairo_probe.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class Detection(unittest.TestCase):
    def test_configured_real_pthreads_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, log = root / 'config.h', root / 'meson-log.txt'
            config.write_text('#define CAIRO_HAS_PTHREAD 1\n')
            log.write_text('')
            p.require_pthreads(config, log)
            for rejected in ('#define CAIRO_NO_MUTEX 1\n', '#define CAIRO_HAS_PTHREAD 0\n', ''):
                config.write_text(rejected)
                with redirect_stdout(StringIO()):
                    with self.assertRaises(RuntimeError):
                        p.require_pthreads(config, log)

    def test_underlying_compiler_error_survives_annotation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, log = root / 'config.h', root / 'meson-log.txt'
            config.write_text('')
            log.write_text('Running compile:\nCompiler stderr: native symbol missing\n'
                           'Checking if "pthreads" links: NO\n')
            captured = StringIO()
            with redirect_stdout(captured):
                with self.assertRaises(RuntimeError):
                    p.require_pthreads(config, log)
            self.assertIn('native symbol missing', captured.getvalue())
            self.assertIn('::error::', captured.getvalue())


if __name__ == '__main__':
    unittest.main()
