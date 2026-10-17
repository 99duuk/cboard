"""Isolated harness tests; no native account or production configuration access."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("windows_g0_probe", Path(__file__).parents[1] / "tools/g0/windows_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class G0ProbeTests(unittest.TestCase):
    def test_bounded_pipe_cases(self):
        result = probe.pipe_probe(dict(os.environ))
        self.assertEqual(result["status"], "pass", result)
        for case in result["cases"].values():
            self.assertTrue(case["cleanup"]["reader_joined"])
            self.assertLess(case["cleanup"]["elapsed_seconds"], 3)

    def test_cumulative_raw_receive_budget_and_release(self):
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", "import os,time; os.write(1,(b'\"'+b'x'*500000+b'\"\\n')*12); time.sleep(30)"],
            stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        reader = probe.JsonlReader(child.stdout)
        try:
            reader.thread.join(timeout=2)
            self.assertFalse(reader.thread.is_alive(), "Reader must reject queued raw bytes above budget")
            self.assertLessEqual(reader.peak_buffered_bytes, probe.FRAME_LIMIT)
            before = reader.buffered_bytes
            kind, value = reader.receive(.2)
            self.assertEqual(kind, "message")
            self.assertLess(reader.buffered_bytes, before)
            error = None
            for _ in range(32):
                kind, value = reader.receive(.2)
                if kind == "error":
                    error = value
                    break
            self.assertEqual(error, "buffer_limit")
        finally:
            cleanup = probe.stop_child(child, reader)
            self.assertTrue(cleanup["reader_joined"])

    def test_package_entry_requires_declared_bin_within_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            entry = root / "bin" / "codex.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("// fake", encoding="utf-8")
            metadata = root / "package.json"
            metadata.write_text(json.dumps({"name": "@openai/codex", "version": "0.0.0", "bin": {"codex": "bin/codex.js"}}))
            result = probe.package_entry(entry, "@openai/codex", "codex")
            self.assertEqual(result["version"], "0.0.0")
            metadata.write_text(json.dumps({"name": "@openai/codex", "bin": "bin/codex.js"}))
            self.assertEqual(probe.package_entry(entry, "@openai/codex", "codex")["name"], "@openai/codex")
            with self.assertRaises(ValueError):
                probe.package_entry(entry, "@openai/codex", "unrelated")
            metadata.write_text(json.dumps({"name": "wrong", "bin": {"codex": "bin/codex.js"}}))
            with self.assertRaises(ValueError):
                probe.package_entry(entry, "@openai/codex", "codex")
            metadata.write_text(json.dumps({"name": "@openai/codex", "bin": {"codex": "../outside.js"}}))
            (root.parent / "outside.js").write_text("// outside")
            try:
                with self.assertRaises(ValueError):
                    probe.package_entry(entry, "@openai/codex", "codex")
            finally:
                (root.parent / "outside.js").unlink()

    @unittest.skipIf(os.name == "nt", "Non-Windows evidence rejection")
    def test_non_windows_does_not_claim_g0(self):
        args = argparse.Namespace(cli=None, node=None, codex_js=None, npm_js=None, shell_report=None)
        result = probe.collect(args)
        self.assertFalse(result["g0_passed"])
        self.assertEqual(len(result["checks"]), 6)
        self.assertTrue(all(v["status"] == "not_applicable" for v in result["checks"].values()))
        self.assertFalse(result["safety"]["account_requests_sent"])

    def test_redacts_credentials(self):
        text = probe.sanitized("Bearer abc token=abc api_key=xyz sk-example-secret")
        self.assertNotIn("abc", text)
        self.assertNotIn("xyz", text)
        self.assertNotIn("sk-example", text)
        self.assertNotIn("hidden", probe.sanitized('{"access_token": "hidden", "refresh_token":"hidden"}'))


if __name__ == "__main__":
    unittest.main()
