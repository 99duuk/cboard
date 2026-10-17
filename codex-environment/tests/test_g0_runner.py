"""Disposable launcher behavior, without claiming PowerShell or Windows execution."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('g0_argv_launcher', Path(__file__).parents[1]/'tools/g0/argv_probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ArgvProbeTests(unittest.TestCase):
    def test_disposable_console_launcher_keeps_literal_arguments(self):
        with tempfile.TemporaryDirectory(prefix='cenv-g0-launcher-') as temporary:
            root = Path(temporary)
            report = probe.prepare(root/'isolated launcher')
            self.assertFalse(report['production_launcher'])
            self.assertEqual(report['native_recorder'], 'python-executable')
            sentinel = root/'UNEXPECTED_EXECUTION'
            args = ['', 'two words', '한글', 'embedded"quote', "single'quote", 'C:\\trailing\\',
                    '&|<>^%!$;', 'line1\nline2', '$(touch '+str(sentinel)+')']
            command = [report['launcher'], *args]
            hostile = root/'hostile'
            hostile.mkdir()
            (hostile/'sitecustomize.py').write_text('raise RuntimeError("inherited startup hook")', encoding='utf-8')
            env = dict(os.environ, PYTHONPATH=str(hostile), PYTHONIOENCODING='ascii', PYTHONUTF8='0')
            result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), args)
            self.assertFalse(sentinel.exists())
            with self.assertRaisesRegex(ValueError, 'already exists'):
                probe.prepare(root/'isolated launcher')


if __name__ == '__main__':
    unittest.main()
