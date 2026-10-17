#!/usr/bin/env python3
"""Isolated G0 evidence collection; no installed account homes or login requests."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

FRAME_LIMIT = 4 * 1024 * 1024
QUEUE_LIMIT = 32
OUTPUT_LIMIT = 64 * 1024
ARGV = ["", "한글 space", 'a"b', "ends\\", "& | < > ^ % ! $ ;", "first\nsecond"]


def sanitized(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+|(?:token|password|api[_-]?key)[=:]\s*)\S+", r"\1<redacted>", value)
    value = re.sub(r'(?i)("(?:access_token|refresh_token|id_token|api_key|password)"\s*:\s*")[^"]*"', r'\1<redacted>"', value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "<redacted>", value)
    value = re.sub(r"S-1-5-21-(?:\d+-){2}\d+-\d+", "<user-sid>", value)
    for env_key in ("USERPROFILE", "HOME", "USERNAME"):
        personal = os.environ.get(env_key, "")
        if len(personal) > 2:
            value = value.replace(personal, f"<{env_key}>")
    return value[:OUTPUT_LIMIT]


def record_command(argv: list[str], *, env: dict[str, str], timeout: float = 10) -> dict:
    started = time.monotonic()
    result = {"argv": [sanitized(str(x)) for x in argv], "started_utc": datetime.now(timezone.utc).isoformat()}
    # File-backed output avoids unbounded RAM growth from an unexpected executable.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env)
            try:
                result["exit_code"] = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
                result.update(exit_code=child.returncode, error="timeout")
            for name, stream in (("stdout", stdout), ("stderr", stderr)):
                stream.seek(0)
                data = stream.read(OUTPUT_LIMIT + 1)
                result[name] = sanitized(data[:OUTPUT_LIMIT].decode("utf-8", "replace"))
                if len(data) > OUTPUT_LIMIT:
                    result[name + "_truncated"] = True
        except (OSError, subprocess.SubprocessError) as exc:
            result.update(exit_code=None, error=sanitized(str(exc)))
    result["elapsed_seconds"] = round(time.monotonic() - started, 4)
    return result


class JsonlReader:
    """Bounded raw-byte reader used only by these probes, never production RPC."""
    def __init__(self, stream):
        self.stream = stream
        self.items: queue.Queue = queue.Queue(QUEUE_LIMIT)
        self.stop = threading.Event()
        self.accounting = threading.Lock()
        self.buffered_bytes = 0
        self.peak_buffered_bytes = 0
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _put(self, kind, value=None, weight=0):
        while not self.stop.is_set():
            try:
                self.items.put((kind, value, weight), timeout=.05)
                return
            except queue.Full:
                pass

    def _read(self):
        pending = bytearray()
        try:
            while not self.stop.is_set():
                chunk = os.read(self.stream.fileno(), 65536)
                if not chunk:
                    if pending:
                        self._put("error", "incomplete_frame")
                    self._put("eof")
                    return
                with self.accounting:
                    over_budget = self.buffered_bytes + len(chunk) > FRAME_LIMIT
                    if not over_budget:
                        self.buffered_bytes += len(chunk)
                        self.peak_buffered_bytes = max(self.peak_buffered_bytes, self.buffered_bytes)
                if over_budget:
                    first_line_size = len(pending) + len(chunk.partition(b"\n")[0])
                    self._put("error", "frame_limit" if first_line_size > FRAME_LIMIT else "buffer_limit")
                    return
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, tail = pending.partition(b"\n")
                    pending = bytearray(tail)
                    if len(line) > FRAME_LIMIT:
                        self._put("error", "frame_limit")
                        return
                    # Raw frames retain their encoded byte charge until receive().
                    # Blank/whitespace lines also consume the budget before discard.
                    if line.strip():
                        self._put("raw", line, len(line) + 1)
                    else:
                        with self.accounting:
                            self.buffered_bytes -= len(line) + 1
        except (OSError, ValueError) as exc:
            self._put("error", type(exc).__name__)

    def receive(self, timeout):
        try:
            kind, value, weight = self.items.get(timeout=max(0, timeout))
        except queue.Empty:
            return "timeout", None
        with self.accounting:
            self.buffered_bytes -= weight
        if kind != "raw":
            return kind, value
        try:
            return "message", json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return "error", "invalid_json"


def stop_child(child, reader) -> dict:
    started = time.monotonic()
    reader.stop.set()
    if child.poll() is None:
        child.kill()
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return {"reaped": False, "reader_joined": False, "elapsed_seconds": time.monotonic() - started}
    reader.thread.join(timeout=.8)
    joined = not reader.thread.is_alive()
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream and (stream is not child.stdout or joined):
            stream.close()
    return {"reaped": True, "reader_joined": joined, "elapsed_seconds": round(time.monotonic() - started, 4)}


def pipe_case(code: str, env: dict, expected: str) -> dict:
    started = time.monotonic()
    argv = [sys.executable, "-I", "-u", "-c", code]
    child = subprocess.Popen(argv, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    reader = JsonlReader(child.stdout)
    if expected == "buffer_limit":
        reader.thread.join(timeout=1)
        kind, payload = "timeout", None
        for _ in range(QUEUE_LIMIT + 1):
            kind, payload = reader.receive(.1)
            if kind != "message":
                break
    else:
        kind, payload = reader.receive(.7)
    actual = payload if kind == "error" else kind
    cleanup = stop_child(child, reader)
    return {"argv": [sanitized(x) for x in argv], "exit_code": child.returncode,
            "expected": expected, "actual": actual, "message": payload if kind == "message" else None,
            "pass": actual == expected and cleanup["reaped"] and cleanup["reader_joined"],
            "elapsed_seconds": round(time.monotonic() - started, 4), "cleanup": cleanup,
            "peak_buffered_bytes": reader.peak_buffered_bytes}


def pipe_probe(env: dict) -> dict:
    cases = {
        "fragmented_utf8": pipe_case("import os,time; b='雪'.encode(); os.write(1,b'{\"v\":\"'+b[:1]); time.sleep(.03); os.write(1,b[1:]+b'\"}\\n')", env, "message"),
        "eof": pipe_case("pass", env, "eof"),
        "partial_eof": pipe_case("import os; os.write(1,b'{')", env, "incomplete_frame"),
        "invalid_json": pipe_case("print('invalid')", env, "invalid_json"),
        "timeout": pipe_case("import time; time.sleep(30)", env, "timeout"),
        "frame_limit": pipe_case(f"import os; os.write(1,b'x'*{FRAME_LIMIT + 1})", env, "frame_limit"),
        "cumulative_buffer_limit": pipe_case("import os,time; os.write(1,(b'\"'+b'x'*500000+b'\"\\n')*12); time.sleep(30)", env, "buffer_limit"),
        "queue_cancel": pipe_case("import os,time; os.write(1,b'{}\\n'*10000); time.sleep(30)", env, "message"),
    }
    cases["fragmented_utf8"]["pass"] &= cases["fragmented_utf8"]["message"] == {"v": "雪"}
    return {"status": "pass" if all(x["pass"] for x in cases.values()) else "fail",
            "scope": "isolated Python pipe cases; not actual account/rateLimits/read",
            "frame_limit_bytes": FRAME_LIMIT, "receive_budget_bytes": FRAME_LIMIT, "queue_limit": QUEUE_LIMIT, "cases": cases}


def package_entry(entry: Path, expected_name: str, bin_name: str) -> dict:
    entry = entry.resolve(strict=True)
    for parent in entry.parents:
        package_file = parent / "package.json"
        if not package_file.is_file():
            continue
        metadata = json.loads(package_file.read_text(encoding="utf-8"))
        if metadata.get("name") != expected_name:
            continue
        bins = metadata.get("bin", {})
        if isinstance(bins, str) and expected_name.rsplit("/", 1)[-1] != bin_name:
            raise ValueError("String bin requires package basename matching requested command")
        target = bins.get(bin_name) if isinstance(bins, dict) else bins
        if not isinstance(target, str):
            raise ValueError(f"Missing {bin_name} package bin")
        declared = (parent / target).resolve(strict=True)
        if not declared.is_relative_to(parent.resolve()) or declared != entry or not entry.is_file():
            raise ValueError("Package bin mismatch or path escape")
        return {"name": expected_name, "version": metadata.get("version"),
                "entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest()}
    raise ValueError(f"No enclosing {expected_name} package metadata")


def official_probe(executable: Path, env: dict) -> dict:
    if executable.suffix.lower() != ".exe" or not executable.is_file():
        return {"status": "fail", "reason": "--cli requires an explicit existing native .exe"}
    base = [str(executable), "-c", 'cli_auth_credentials_store="file"']
    commands = [record_command([*base, flag], env=env) for flag in ("--version", "--help")]
    child = subprocess.Popen([*base, "app-server"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    reader = JsonlReader(child.stdout)
    started = time.monotonic()
    response = None
    error = None
    try:
        child.stdin.write(b'{"id":1,"method":"initialize","params":{"clientInfo":{"name":"cenv_g0_probe","version":"0.1.0"}}}\n')
        child.stdin.flush()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            kind, value = reader.receive(deadline - time.monotonic())
            if kind == "message" and isinstance(value, dict) and value.get("id") == 1:
                response = "result" if "result" in value else "error"
                break
            if kind != "message":
                error = kind
                break
    except (OSError, ValueError) as exc:
        error = type(exc).__name__
    finally:
        cleanup = stop_child(child, reader)
    ok = all(c.get("exit_code") == 0 and not c.get("error") for c in commands) and response == "result" and cleanup["reader_joined"]
    return {"status": "partial" if ok else "fail", "commands": commands,
            "app_server": {"argv": [sanitized(x) for x in [*base, "app-server"]], "initialize_response": response,
                           "error": error, "elapsed_seconds": round(time.monotonic() - started, 4), "cleanup": cleanup},
            "missing": ["Official native argv echo unavailable; Python recorder is not native CLI argv proof",
                        "account/rateLimits/read not run: authenticated/online requests forbidden"]}


def windows_files_probe(root: Path, env: dict) -> dict:
    import msvcrt
    folder = root / "acl-lock"
    folder.mkdir()
    lock = folder / "lock"
    lock.write_bytes(b"0")
    commands = []
    with lock.open("r+b") as held:
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
        code = ("import msvcrt,sys; f=open(sys.argv[1],'r+b'); "
                "msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)")
        contention = record_command([sys.executable, "-I", "-c", code, str(lock)], env=env)
        commands.append(contention)
        held.seek(0)
        msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
    released = record_command([sys.executable, "-I", "-c", code, str(lock)], env=env)
    commands.append(released)
    target, staged = folder / "target", folder / "staged"
    target.write_text("old", encoding="utf-8")
    staged.write_text("new", encoding="utf-8")
    replacement_blocked = False
    with target.open("rb"):
        try:
            os.replace(staged, target)
        except PermissionError:
            replacement_blocked = True
    if not staged.exists():
        staged.write_text("new", encoding="utf-8")
    os.replace(staged, target)
    replacement_after_release = target.read_text(encoding="utf-8") == "new"
    # Probe ACL changes only inside this invocation's temporary directory.
    who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=5, env=env)
    acl_ok = False
    if who.returncode == 0:
        rows = list(csv.reader(who.stdout.decode("utf-8", "replace").splitlines()))
        sid = rows[0][-1].strip() if rows else ""
        if re.fullmatch(r"S-1-[0-9-]+", sid):
            acl_set = record_command(["icacls", str(folder), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"], env=env)
            acl_read = record_command(["icacls", str(folder)], env=env)
            commands.extend([acl_set, acl_read])
            acl_ok = acl_set.get("exit_code") == 0 and acl_read.get("exit_code") == 0
    observations = {"lock_contention_denied": contention.get("exit_code") not in (None, 0),
                    "lock_released": released.get("exit_code") == 0,
                    "open_target_replacement_blocked": replacement_blocked,
                    "replacement_after_release": replacement_after_release,
                    "temporary_acl_set_and_read": acl_ok}
    return {"status": "partial" if all(observations.values()) else "fail", "observations": observations,
            "commands": commands, "missing": ["ACL effective-access/other-user denial proof", "abrupt-manager-death and journal recovery scenarios"]}


def collect(args) -> dict:
    result = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "environment": {"os": platform.system(), "release": platform.release(), "version": platform.version(),
                              "architecture": platform.machine(), "python": platform.python_version(),
                              "shell": os.environ.get("CENV_G0_SHELL", "not_recorded")},
              "safety": {"installed_profiles_modified": False, "login_requested": False,
                         "account_requests_sent": False, "network_install_requested": False}, "checks": {}}
    checks = result["checks"]
    if sys.platform != "win32":
        for key in ("T01", "T02", "T03", "T04", "T05", "T06"):
            checks[key] = {"status": "not_applicable", "reason": "Windows G0 evidence requires actual Windows execution"}
    else:
        with tempfile.TemporaryDirectory(prefix="cenv-g0-") as temp:
            root = Path(temp)
            isolated = root / "codex-home"
            isolated.mkdir()
            env = dict(os.environ, CODEX_HOME=str(isolated))
            npm_config = root / "empty-npmrc"
            npm_config.write_text("", encoding="utf-8")
            for key in tuple(env):
                if key.lower().startswith("npm_config_"):
                    env.pop(key)
            env.update(NPM_CONFIG_USERCONFIG=str(npm_config), NPM_CONFIG_GLOBALCONFIG=str(npm_config),
                       NPM_CONFIG_CACHE=str(root / "npm-cache"))
            # Prevent preexisting API-key auth from affecting an isolated CLI invocation.
            for key in tuple(env):
                if key.startswith(("OPENAI_", "CODEX_API_")) or key == "CODEX_ACCESS_TOKEN":
                    env.pop(key)
            recorder = record_command([sys.executable, "-I", "-c", 'import json,sys; sys.stdout.buffer.write(json.dumps(sys.argv[1:],ensure_ascii=False).encode("utf-8"))', *ARGV], env=env)
            try:
                argv_ok = json.loads(recorder.get("stdout", "")) == ARGV
            except ValueError:
                argv_ok = False
            checks["T01"] = {"status": "partial", "python_argv_recorder_pass": argv_ok, "recorder": recorder,
                             "official_cli": {"status": "not_run", "reason": "No explicit --cli"}}
            if args.cli:
                try:
                    checks["T01"]["official_cli"] = official_probe(args.cli.resolve(), env)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    checks["T01"]["official_cli"] = {"status": "fail", "error": sanitized(str(exc))}
            if not argv_ok or checks["T01"]["official_cli"]["status"] == "fail":
                checks["T01"]["status"] = "fail"
            checks["T02"] = {"status": "not_run", "reason": "Explicit --node and JS entry points required", "npm_install": "not_run: no network installation permitted"}
            if args.node and (args.codex_js or args.npm_js):
                try:
                    node = args.node.resolve(strict=True)
                    if node.suffix.lower() != ".exe":
                        raise ValueError("--node requires an explicit native .exe")
                    observations = {"node_version": record_command([str(node), "--version"], env=env)}
                    for key, path, package, name in (("codex", args.codex_js, "@openai/codex", "codex"), ("npm", args.npm_js, "npm", "npm")):
                        if path:
                            observations[key] = {"metadata": package_entry(path, package, name), "version": record_command([str(node), str(path.resolve()), *(["-c", 'cli_auth_credentials_store="file"'] if key == "codex" else []), "--version"], env=env)}
                    commands_ok = observations["node_version"].get("exit_code") == 0 and all(v["version"].get("exit_code") == 0 for k, v in observations.items() if k != "node_version")
                    checks["T02"].update(status="partial" if commands_ok else "fail", observations=observations,
                                         reason="Metadata/bin containment and direct version calls only; install not exercised")
                except (OSError, ValueError) as exc:
                    checks["T02"].update(status="fail", error=sanitized(str(exc)))
            checks["T03"] = {"status": "not_run", "reason": "Run PowerShell shell-chain probe separately; no Python-only substitution"}
            if args.shell_report:
                try:
                    raw = args.shell_report.read_bytes()
                    report = json.loads(raw)
                    check = report["checks"]["T03"]
                    if (report.get("schema_version") != 1 or report["environment"].get("os") != "Windows"
                            or not isinstance(report["environment"].get("powershell_version"), str)
                            or check.get("status") not in ("pass", "partial", "fail", "not_run")):
                        raise ValueError("Invalid Windows shell report schema")
                    # External evidence retained as data; never interpreted as commands.
                    checks["T03"] = json.loads(sanitized(json.dumps(check, ensure_ascii=False)))
                    checks["T03"]["external_report_sha256"] = hashlib.sha256(raw).hexdigest()
                    result["environment"]["powershell_version"] = report["environment"]["powershell_version"]
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    checks["T03"] = {"status": "fail", "error": sanitized(str(exc))}
            checks["T04"] = pipe_probe(env)
            checks["T05"] = {"status": "not_run", "reason": "Windows owned-process-tree/Ctrl-C/job-object and abrupt manager exit require dedicated probe"}
            try:
                checks["T06"] = windows_files_probe(root, env)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                checks["T06"] = {"status": "fail", "error": sanitized(str(exc))}
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    result["g0_passed"] = all(checks[key]["status"] == "pass" for key in checks)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--codex-js", type=Path)
    parser.add_argument("--npm-js", type=Path)
    parser.add_argument("--shell-report", type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ required")
    if args.output.exists():
        parser.error("Output already exists; choose a new evidence file")
    result = collect(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Evidence: {args.output}; G0 passed: {result['g0_passed']}")
    return 0 if result["g0_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
