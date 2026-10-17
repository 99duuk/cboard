"""G0 probe structure only; no Windows success inferred from local fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('process_g0_probe', Path(__file__).parents[1] / 'tools/g0/process_probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProcessProbeTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Non-Windows contract only')
    def test_non_windows_cannot_claim_windows_pass(self):
        with patch.object(probe, 'windows_probe', side_effect=AssertionError('Windows call on POSIX')):
            report = probe.collect()
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['checks']['T05']['status'], 'not_applicable')
        self.assertFalse(report['safety']['console_events_sent'])
        self.assertFalse(report['safety']['credentials_accessed'])

    def test_output_is_required(self):
        with self.assertRaises(SystemExit) as error:
            probe.main([])
        self.assertEqual(error.exception.code, 2)

    def test_output_is_json_and_never_clobbered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'process.json'
            result = {'schema_version': 1, 'checks': {'T05': {'status': 'partial'}}}
            with patch.object(probe, 'collect', return_value=result) as collect:
                self.assertEqual(probe.main(['--output', str(path)]), 1)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), result)
                with self.assertRaises(FileExistsError):
                    probe.main(['--output', str(path)])
            self.assertEqual(collect.call_count, 1)


if __name__ == '__main__':
    unittest.main()
