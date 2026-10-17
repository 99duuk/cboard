"""Usage IPC, failure isolation, rendering and cleanup checks."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from codex_env import usage


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.account = {"id": "test", "label": "test", "home": str(self.home)}
        self.cli = self.home / "fake-codex"
        self.cli_patch = mock.patch.object(usage, "resolve_cli", return_value=self.cli)
        self.cli_patch.start()
        self.addCleanup(self.cli_patch.stop)
        self.home_patch = mock.patch.object(usage, "account_home", side_effect=lambda a: Path(a["home"]))
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def server(self, body):
        self.cli.write_text("#!/usr/bin/env python3\nimport json, os, sys, time\n" + body)
        self.cli.chmod(0o755)

    def test_multiple_responses_in_one_write_and_schema_variants(self):
        result = {"rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 25}}},
                  "rateLimitResetCredits": {"availableCount": 3}}
        # initialize and rate-limit response intentionally coalesced into one pipe write.
        self.server("sys.stdin.readline()\n"
                    + "sys.stdout.write(json.dumps({'id': 0, 'result': {}}) + '\\n' + "
                    + repr(json.dumps({"method": "notification"}) + "\n" + json.dumps({"id": 1, "result": result}) + "\n")
                    + ")\nsys.stdout.flush()\ntime.sleep(30)\n")
        item = usage.fetch_account(self.account)
        self.assertNotIn("error", item)
        self.assertEqual(item["limits"]["primary"]["usedPercent"], 25)
        self.assertEqual(item["resetCredits"]["availableCount"], 3)

    def test_legacy_schema_and_null_fields(self):
        self.server("sys.stdin.readline()\nprint(json.dumps({'id':0, 'result':{}}),flush=True)\n"
                    "sys.stdin.readline()\nsys.stdin.readline()\n"
                    "print(json.dumps({'id':1,'result':{'rateLimits':{'planType':'plus'},"
                    "'rateLimitsByLimitId': []}}),flush=True)\ntime.sleep(30)\n")
        item = usage.fetch_account(self.account)
        self.assertNotIn("error", item)
        self.assertEqual(item["limits"]["planType"], "plus")

    def identity_server(self, result=None, error=None):
        response = {"id": 1, "result": result} if error is None else {"id": 1, "error": error}
        self.server("sys.stdin.readline()\nprint(json.dumps({'id':0,'result':{}}),flush=True)\n"
                    "sys.stdin.readline()\nrequest=json.loads(sys.stdin.readline())\n"
                    "assert request['method']=='account/read'\n"
                    "assert request['params']=={'refreshToken':False}\n"
                    + "print(" + repr(json.dumps(response)) + ",flush=True)\ntime.sleep(30)\n")

    def test_identity_read_uses_official_rpc_and_allowlists_metadata(self):
        self.identity_server({"account": {"type": "chatgpt", "email": "person@example.test",
                                         "planType": "plus", "accessToken": "secret-token",
                                         "credentials": {"password": "secret-password"}},
                              "refreshToken": "secret-refresh", "requiresOpenaiAuth": True})
        identity = usage.read_identity(self.account)
        self.assertEqual(identity, {"type": "chatgpt", "email": "person@example.test", "planType": "plus"})
        self.assertNotIn("secret", json.dumps(identity))

    def test_identity_null_and_api_key_do_not_expose_credentials(self):
        self.identity_server({"account": None, "accessToken": "secret-token"})
        identity = usage.read_identity(self.account)
        self.assertIn("login required", identity["error"])
        self.assertNotIn("secret", json.dumps(identity))
        self.identity_server({"account": {"type": "apiKey", "apiKey": "sk-secret"}})
        identity = usage.read_identity(self.account)
        self.assertEqual(identity, {"type": "apiKey", "email": None, "planType": None})
        self.assertNotIn("sk-secret", json.dumps(identity))

    def test_identity_error_and_malformed_fields_are_sanitized(self):
        self.identity_server(error={"message": "provider rejected sk-secret-token"})
        identity = usage.read_identity(self.account)
        self.assertIn("error", identity)
        self.assertNotIn("sk-secret", json.dumps(identity))
        self.identity_server({"account": {"type": "chatgpt", "email": {"token": "secret"}, "planType": []}})
        self.assertEqual(usage.read_identity(self.account), {"type": "chatgpt", "email": None, "planType": None})

    def test_timeout_reaps_child(self):
        self.server("time.sleep(30)\n")
        children = []
        popen = usage.subprocess.Popen

        def spawn(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child

        with mock.patch.object(usage, "TIMEOUT_SECONDS", 0.2), \
             mock.patch.object(usage.subprocess, "Popen", side_effect=spawn):
            start = time.monotonic()
            item = usage.fetch_account(self.account)
        self.assertIn("no response", item["error"])
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        with self.assertRaises(ProcessLookupError):
            os.kill(children[0].pid, 0)

    def test_eof_and_missing_home(self):
        self.server("sys.exit(0)\n")
        self.assertIn("stopped unexpectedly", usage.fetch_account(self.account)["error"])
        self.account["home"] = str(self.home / "absent")
        self.assertEqual(usage.fetch_account(self.account)["error"], "CODEX_HOME not found")

    def test_missing_rate_limits_is_error_except_reset_query(self):
        self.server("sys.stdin.readline()\nprint(json.dumps({'id':0,'result':{}}),flush=True)\n"
                    "sys.stdin.readline()\nsys.stdin.readline()\n"
                    "print(json.dumps({'id':1,'result':{}}),flush=True)\ntime.sleep(30)\n")
        self.assertIn("no ChatGPT", usage.fetch_account(self.account)["error"])
        self.assertIsNone(usage.fetch_account(self.account, False)["limits"])

    def test_unknown_percent_and_reset_are_unavailable(self):
        for window in ({}, {"usedPercent": None}, {"usedPercent": "nan"},
                       {"usedPercent": True}, {"usedPercent": "wrong"}, "bad"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                usage.render_window(window)
            self.assertIn("unavailable", output.getvalue())
            self.assertNotIn("100%", output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            usage.render_resets([{"name": "test", "limits": None, "resetCredits": None}])
        self.assertIn("unavailable", output.getvalue())
        self.assertNotIn("0 usage", output.getvalue())
        self.assertEqual(usage.reset_label("bad"), "UNKNOWN")
        self.assertEqual(usage.reset_data({"resetCredits": {"availableCount": 0}}), (0, []))

    def test_weekly_only_and_narrow_terminal(self):
        items = [{"name": "test", "limits": {"planType": "plus", "secondary": {
            "usedPercent": 20, "windowDurationMins": 10080, "resetsAt": 123}}}]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             mock.patch.object(output, "isatty", return_value=True), \
             mock.patch.object(usage.shutil, "get_terminal_size", return_value=os.terminal_size((24, 80))):
            usage.render(items)
        self.assertIn("WEEKLY LIMIT: 80% LEFT", output.getvalue())
        self.assertIn("unavailable", output.getvalue())
        self.assertTrue(all(len(line) <= 24 for line in output.getvalue().splitlines()))

    def test_broken_json_is_account_error(self):
        self.server("sys.stdin.readline()\nprint('invalid json',flush=True)\ntime.sleep(30)\n")
        self.assertIn("error", usage.fetch_account(self.account))

    def test_pool_has_at_most_five_workers_and_preserves_order(self):
        lock = threading.Lock()
        active = maximum = 0
        accounts = [{**self.account, "label": str(i)} for i in range(11)]

        def fetch(account, *args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"name": account["label"], "error": "failure"} if account["label"] == "2" else {"name": account["label"]}

        with mock.patch.object(usage, "fetch_account", side_effect=fetch):
            items = usage.snapshot(accounts)
        self.assertEqual([i["name"] for i in items], [str(i) for i in range(11)])
        self.assertLessEqual(maximum, 5)
        self.assertIn("error", items[2])

    def test_interrupt_stops_run_and_main_returns_130(self):
        with mock.patch.object(usage, "wait", side_effect=KeyboardInterrupt), \
             mock.patch.object(usage.QueryRun, "stop", autospec=True) as stop:
            with self.assertRaises(KeyboardInterrupt):
                usage.snapshot([])
            stop.assert_called_once()
        with mock.patch.object(usage, "load_accounts", return_value=[{"id": "fixture"}]), \
             mock.patch.object(usage, "snapshot", side_effect=KeyboardInterrupt), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(usage.main([]), 130)


if __name__ == "__main__":
    unittest.main()
