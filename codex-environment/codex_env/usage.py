#!/usr/bin/env python3
"""Show rate-limit usage for multiple Codex accounts."""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
import json
import os
import re
import queue
import subprocess
import sys
import time
import math
import threading
import shutil
import textwrap

from codex_env.core import load_accounts, resolve_cli, account_home, local_dir


TIMEOUT_SECONDS = 15
BOX_WIDTH = 84
BAR_WIDTH = 18
PALETTE = ("\033[38;5;45m", "\033[38;5;214m", "\033[38;5;141m", "\033[38;5;81m", "\033[38;5;203m")
GREEN = "\033[38;5;82m"
YELLOW = "\033[38;5;220m"
RED = "\033[38;5;203m"
WHITE = "\033[97m"
DIM = "\033[38;5;244m"
BOLD = "\033[1m"
RESET = "\033[0m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
COLOR = (sys.stdout.isatty() or os.environ.get("CLICOLOR_FORCE") == "1") and os.environ.get("TERM") != "dumb" and "NO_COLOR" not in os.environ


def paint(text, *styles):
    return f"{''.join(styles)}{text}{RESET}" if COLOR else text


class QueryRun:
    """Own the processes for one snapshot, including interruption cleanup."""

    def __init__(self):
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.processes = set()

    def stop(self):
        with self.lock:
            self.stopped.set()
            for proc in self.processes:
                if proc.poll() is None:
                    proc.kill()


def send(proc, message):
    proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
    proc.stdin.flush()


class PipeReader:
    """Read anonymous pipes without select(), which only supports sockets on Windows."""
    LIMIT = 4 * 1024 * 1024

    def __init__(self, stream):
        self.stream = stream
        self.items = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.buffered = 0
        self.thread = threading.Thread(target=self._read, name="cusage-pipe", daemon=True)
        self.thread.start()

    def _put(self, kind, value, weight=0):
        while not self.stopped.is_set():
            try:
                self.items.put((kind, value, weight), timeout=.05)
                return
            except queue.Full:
                pass

    def _read(self):
        pending = bytearray()
        try:
            while not self.stopped.is_set():
                chunk = os.read(self.stream.fileno(), 65536)
                if not chunk:
                    self._put('error', 'app-server stopped unexpectedly')
                    return
                with self.lock:
                    overflow = self.buffered + len(chunk) > self.LIMIT
                    if not overflow:
                        self.buffered += len(chunk)
                if overflow:
                    self._put('error', 'app-server response exceeds 4 MiB')
                    return
                pending.extend(chunk)
                while b'\n' in pending:
                    line, _, tail = pending.partition(b'\n')
                    pending = bytearray(tail)
                    self._put('line', line, len(line) + 1)
        except (OSError, ValueError):
            self._put('error', 'app-server pipe closed')

    def next(self, timeout):
        kind, value, weight = self.items.get(timeout=timeout)
        with self.lock:
            self.buffered -= weight
        if kind == 'error':
            raise RuntimeError(value)
        return value


def receive(proc, request_id, deadline, run):
    reader = proc._usage_reader
    while True:
        if run.stopped.is_set():
            raise RuntimeError("snapshot cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no response within {TIMEOUT_SECONDS}s")
        try:
            line = reader.next(min(remaining, .2))
        except queue.Empty:
            continue
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise RuntimeError('invalid app-server JSON') from exc
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"]
            raise RuntimeError(error.get("message", "unknown app-server error")
                               if isinstance(error, dict) else str(error))
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("invalid app-server result")
        return result


def account_request(account, method, params=None, _run=None):
    """Run one account RPC with the shared initialization and cleanup lifecycle."""
    run = _run or QueryRun()
    home = str(account_home(account))
    if not os.path.isdir(home):
        raise RuntimeError("CODEX_HOME not found")
    proc = None
    try:
        codex = resolve_cli()
        env = os.environ.copy()
        env["CODEX_HOME"] = home
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with run.lock:
            if run.stopped.is_set():
                raise RuntimeError("snapshot cancelled")
            proc = subprocess.Popen(
                [str(codex), "app-server"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                bufsize=0,
            )
            run.processes.add(proc)
            proc._usage_reader = PipeReader(proc.stdout)
        send(proc, {
            "method": "initialize", "id": 0,
            "params": {"clientInfo": {
                "name": "codex_usage", "title": "Codex Usage", "version": "2.0.0",
            }},
        })
        receive(proc, 0, deadline, run)
        send(proc, {"method": "initialized", "params": {}})
        request = {"method": method, "id": 1}
        if params is not None:
            request["params"] = params
        send(proc, request)
        return receive(proc, 1, deadline, run)
    finally:
        if proc:
            reader = getattr(proc, "_usage_reader", None)
            if reader:
                reader.stopped.set()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            else:
                proc.wait()
            proc.stdin.close()
            if reader:
                reader.thread.join(timeout=1)
            proc.stdout.close()
            with run.lock:
                run.processes.discard(proc)


def fetch_account(account, require_limits=True, _run=None):
    display = account.get('label', account['id'])
    if display != account['id']:
        display += ' ['+account['id']+']'
    base = {"name": display, "home": str(account_home(account))}
    cache = local_dir()/'identities.json'
    try:
        saved = json.loads(cache.read_text()).get(account['id'], {}) if cache.exists() else {}
        email = saved.get('identity', {}).get('email') if saved.get('confirmed') else None
        if isinstance(email, str):
            safe_email = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', email)
            base['identity'] = safe_email+' (cached '+str(saved.get('checked_at', 'unknown'))+')'
    except (ValueError, OSError):
        pass
    try:
        result = account_request(account, "account/rateLimits/read", _run=_run)
        by_id = result.get("rateLimitsByLimitId")
        limits = (by_id.get("codex") if isinstance(by_id, dict) else None) or result.get("rateLimits")
        if not isinstance(limits, dict):
            limits = None
        if not limits and require_limits:
            raise RuntimeError("no ChatGPT rate-limit data returned; check account login")
        return {
            **base, "limits": limits,
            "ordinaryUsageAllowed": result.get("ordinaryUsageAllowed", True),
            "resetCredits": result.get("rateLimitResetCredits"),
        }
    except Exception as exc:
        return {**base, "error": str(exc)}


def read_identity(account):
    """Read official account metadata without exposing authentication material."""
    try:
        result = account_request(account, "account/read", {"refreshToken": False})
    except Exception as exc:
        # Server error text can contain provider details; keep credential reads allowlisted.
        return {"error": "account identity unavailable (" + type(exc).__name__ + ")"}
    identity = result.get("account")
    if not isinstance(identity, dict) or not isinstance(identity.get("type"), str):
        return {"error": "account identity unavailable; login required"}
    return {key: identity.get(key) if isinstance(identity.get(key), str) else None
            for key in ("type", "email", "planType")}


def frame(content="", color=DIM):
    visible_length = len(ANSI.sub("", content))
    padding = " " * max(0, BOX_WIDTH - visible_length)
    print(paint("│", color) + content + padding + paint("│", color))


def cap(name, detail, color, bottom=False):
    if bottom:
        print(paint("└" + "─" * BOX_WIDTH + "┘", color))
        return
    left = f"─ {name.upper()} "
    right = f" {detail} {'─' * max(0, 19 - len(detail))}─"
    middle = "─" * max(1, BOX_WIDTH - len(left) - len(right))
    print(paint("┌" + left + middle + right + "┐", color, BOLD))


def percent(value):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def percent_text(value):
    value = percent(value)
    return f"{value:.0f}%" if value.is_integer() else f"{value:.1f}%"


def remaining_color(value):
    value = percent(value)
    return RED if value <= 10 else YELLOW if value <= 30 else GREEN


def bar(value):
    value = percent(value)
    filled = round(BAR_WIDTH * value / 100)
    return (
        paint("[", DIM)
        + paint("█" * filled, remaining_color(value), BOLD)
        + paint("░" * (BAR_WIDTH - filled), DIM)
        + paint("]", DIM)
    )


def window_label(window):
    minutes = window.get("windowDurationMins")
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool):
        minutes = None
    if minutes == 10080:
        return "WEEKLY LIMIT:"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}H LIMIT:"
    return f"{minutes}M LIMIT:" if minutes else "LIMIT:"


def reset_label(timestamp):
    if not timestamp:
        return "UNKNOWN"
    try:
        value = datetime.fromtimestamp(timestamp).astimezone()
    except (ValueError, TypeError, OverflowError, OSError):
        return "UNKNOWN"
    today = datetime.now().astimezone().date()
    days = (value.date() - today).days
    if days == 0:
        return value.strftime("%H:%M")
    return f"{value:%H:%M} on {value.day} {value:%b}"


def window_usage(window):
    raw = window.get("usedPercent") if isinstance(window, dict) else None
    try:
        if raw is not None and not isinstance(raw, bool) and math.isfinite(float(raw)):
            return percent(raw)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def render_window(window):
    used = window_usage(window)
    if used is None:
        frame(paint("  --      unavailable", DIM))
        return
    left = 100 - used
    reset = "(resets " + reset_label(window.get("resetsAt")) + ")"
    content = (
        f"  {paint(window_label(window).ljust(16), WHITE, BOLD)} "
        f"{bar(left)}  "
        f"{paint(percent_text(left).rjust(6), remaining_color(left), BOLD)} LEFT  "
        f"{paint(reset, DIM)}"
    )
    frame(content)


def render_account(item, index):
    color = PALETTE[index % len(PALETTE)]
    if "error" in item:
        cap(item["name"], "ERROR", color)
        frame("  " + paint("!", RED, BOLD) + "  " + paint(item["error"][:72], RED))
        frame("  " + paint(item["home"], DIM))
        cap("", "", color, bottom=True)
        return

    limits = item.get("limits") or {}
    plan = str(limits.get("planType") or "CHATGPT").upper()
    credits, _ = reset_data(item)
    detail = plan + (f" · RESET ×{credits}" if credits else "")
    if not item.get("ordinaryUsageAllowed", True):
        detail += " · LIMITED"
    cap(item["name"], detail, color)
    if item.get("identity"):
        frame("  "+item["identity"][:78])
    render_window(limits.get("primary"))
    render_window(limits.get("secondary"))
    cap("", "", color, bottom=True)


def compact(items, resets=False):
    width = max(1, shutil.get_terminal_size().columns)

    def line(value):
        for part in textwrap.wrap(str(value), width=width, replace_whitespace=True) or [""]:
            print(part)

    line("CODEX ACCOUNT USAGE")
    line(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    for item in items:
        print()
        line(item["name"].upper())
        if item.get("identity"):
            line(item["identity"])
        if "error" in item:
            line("ERROR: " + item["error"])
            continue
        limits = item.get("limits") or {}
        line(str(limits.get("planType") or "CHATGPT").upper())
        if item.get("ordinaryUsageAllowed") is False:
            line("LIMITED")
        if resets:
            available, entries = reset_data(item)
            line(f"Resets available: {available}" if available is not None else "Resets unavailable")
            for credit in entries:
                if isinstance(credit, dict) and credit.get("status") == "available":
                    line(str(credit.get("title") or "Full reset"))
                    line("Expires " + reset_label(credit.get("expiresAt")))
            continue
        for window in (limits.get("primary"), limits.get("secondary")):
            used = window_usage(window)
            if used is None:
                line("LIMIT: unavailable")
                continue
            line(window_label(window) + " " + percent_text(100 - used) + " LEFT")
            line("Resets " + reset_label(window.get("resetsAt")))


def render(items):
    if sys.stdout.isatty() and shutil.get_terminal_size().columns < BOX_WIDTH + 2:
        compact(items)
        return
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    title = " CODEX ACCOUNT USAGE "
    top = "╔" + title + "═" * (BOX_WIDTH - len(title)) + "╗"
    print(paint(top, "\033[38;5;51m", BOLD))
    frame("  LIVE RATE-LIMIT SNAPSHOT" + " " * 30 + now, "\033[38;5;51m")
    print(paint("╚" + "═" * BOX_WIDTH + "╝", "\033[38;5;51m", BOLD))
    print()
    for index, item in enumerate(items):
        render_account(item, index)
        if index != len(items) - 1:
            print()


def reset_data(item):
    data = item.get("resetCredits")
    if isinstance(data, dict):
        count = data.get("availableCount")
        entries = data.get("credits")
        return (count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None,
                entries if isinstance(entries, list) else [])
    return (data if isinstance(data, int) and not isinstance(data, bool) and data >= 0 else None, [])


def render_resets(items):
    if sys.stdout.isatty() and shutil.get_terminal_size().columns < BOX_WIDTH + 2:
        compact(items, resets=True)
        return
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    title = " CODEX ACCOUNT USAGE "
    print(paint("╔" + title + "═" * (BOX_WIDTH - len(title)) + "╗", "\033[38;5;51m", BOLD))
    frame("  LIVE RATE-LIMIT SNAPSHOT" + " " * 30 + now, "\033[38;5;51m")
    print(paint("╚" + "═" * BOX_WIDTH + "╝", "\033[38;5;51m", BOLD))
    print()
    for index, item in enumerate(items):
        color = PALETTE[index % len(PALETTE)]
        if item.get("identity"):
            frame("  "+item["identity"][:78])
        if "error" in item:
            cap(item["name"], "ERROR", color)
            frame("  " + paint(item["error"][:72], RED))
        else:
            available, entries = reset_data(item)
            limits = item.get("limits") or {}
            plan = str(limits.get("planType") or "CHATGPT").upper()
            detail = plan + (f" · RESET ×{available}" if available is not None else "")
            cap(item["name"], detail, color)
            frame(f"  {available} usage limit resets available." if available is not None
                  else "  Usage limit resets unavailable.")
            for credit in entries:
                if not isinstance(credit, dict) or credit.get("status") != "available":
                    continue
                frame(f"  {credit.get('title') or 'Full reset'}  Expires {reset_label(credit.get('expiresAt'))}")
        cap("", "", color, bottom=True)
        if index != len(items) - 1:
            print()


def demo():
    now = int(time.time())
    sample = []
    for name, used, weekly, plan in zip(
        ("codex", "cwork", "ccomm"), (18, 76, 94), (34, 71, 88), ("plus", "team", "plus"),
    ):
        sample.append({
            "name": name, "home": "~/." + name,
            "ordinaryUsageAllowed": True, "resetCredits": {"availableCount": 1},
            "limits": {
                "planType": plan,
                "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": now + 3600},
                "secondary": {"usedPercent": weekly, "windowDurationMins": 10080, "resetsAt": now + 86400 * 4},
            },
        })
    render(sample)


def snapshot(accounts, require_limits=True):
    run = QueryRun()
    pool = ThreadPoolExecutor(max_workers=5)
    futures = []
    try:
        for account in accounts:
            futures.append(pool.submit(fetch_account, account, require_limits, run))
        _, pending = wait(futures, timeout=max(0, math.ceil(len(accounts) / 5) * 18 - 1))
        if pending:
            run.stop()
            wait(pending, timeout=3)
        items = []
        for account, future in zip(accounts, futures):
            if future not in pending and future.done() and not future.cancelled():
                items.append(future.result())
            else:
                future.cancel()
                items.append({"name": account["label"], "home": str(account_home(account)),
                              "error": "snapshot deadline exceeded"})
        return items
    except KeyboardInterrupt:
        run.stop()
        wait(futures, timeout=3)
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--demo"]:
        demo()
        return 0
    resets = '--resets' in args
    names = [item for item in args if item != '--resets']
    if len(names) > 1 or any(item.startswith('-') for item in names) or args.count('--resets') > 1:
        print("usage: cusage [ACCOUNT] [--resets|--demo]", file=sys.stderr)
        return 2
    try:
        accounts = load_accounts()
        if names:
            accounts = [account for account in accounts if account['id'] == names[0] or names[0] in account['aliases']]
            if not accounts:
                print('cusage: unknown account: '+names[0], file=sys.stderr)
                return 1
        if not accounts:
            print('No registered accounts; use cnew personal')
            return 0
        items = snapshot(accounts, require_limits=not resets)
        (render_resets if resets else render)(items)
        return 1 if any("error" in item for item in items) else 0
    except KeyboardInterrupt:
        print("Snapshot cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"cusage: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
