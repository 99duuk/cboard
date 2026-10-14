import shlex
from dataclasses import replace

import pytest

from cboard.models import ThreadKey
from cboard.terminals.command import resume_spec
from conftest import task


def test_resume_preserves_literal_path_and_account(config):
    t = task(config)
    spec = resume_spec(config, t)
    tokens = shlex.split(spec.command)
    assert tokens == ["env", f"CODEX_HOME={config.accounts[0].home}", str(config.executable), "-C", t.cwd, "resume", "one"]
    assert "--last" not in spec.argv
    assert spec.environment()["CODEX_HOME"] == str(config.accounts[0].home)
    assert not (config.accounts[0].home.parent / "NEVER").exists()


@pytest.mark.parametrize("change", [{"cwd": "/does/not/exist"}, {"ephemeral": True}, {"persisted": False}, {"key": ThreadKey("a", "--last")}])
def test_invalid_resume_is_rejected(config, change):
    with pytest.raises(ValueError):
        resume_spec(config, replace(task(config), **change))


def test_terminal_timeout_has_concise_user_message(config, monkeypatch):
    import subprocess
    from cboard.terminals.iterm import ItermLauncher
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 20)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="iTerm2 응답이 지연됩니다") as error:
        ItermLauncher(config.state_dir)._open(resume_spec(config, task(config)))
    assert "tell application" not in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "apple_event", "unknown_result", "os_error"])
def test_terminal_failure_blocks_later_requests(config, monkeypatch, failure):
    import subprocess
    from cboard.terminals.command import TerminalUnavailable
    from cboard.terminals.iterm import ItermLauncher

    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 20)
        if failure == "os_error":
            raise OSError("cannot execute osascript")
        if failure == "apple_event":
            return subprocess.CompletedProcess(argv, 1, "", "AppleEvent timed out. (-1712)")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    launcher = ItermLauncher(config.state_dir)
    with pytest.raises(TerminalUnavailable):
        launcher._open(resume_spec(config, task(config)))
    with pytest.raises(TerminalUnavailable, match="자동 열기가 중단"):
        launcher._open(resume_spec(config, task(config, "two")))
    assert len(calls) == 1


async def test_concurrent_terminal_requests_stop_after_first_timeout(config, monkeypatch):
    import asyncio
    import subprocess
    import time
    from cboard.terminals.command import TerminalUnavailable
    from cboard.terminals.iterm import ItermLauncher

    calls = []
    def timeout(argv, **kwargs):
        calls.append(argv)
        time.sleep(0.05)
        raise subprocess.TimeoutExpired(argv, 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    launcher = ItermLauncher(config.state_dir)
    results = await asyncio.gather(
        *(launcher.open(resume_spec(config, task(config, key))) for key in ["one", "two", "three"]),
        return_exceptions=True,
    )
    assert all(isinstance(result, TerminalUnavailable) for result in results)
    assert len(calls) == 1


def test_separate_launchers_serialize_terminal_automation(config, monkeypatch):
    import subprocess
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from cboard.terminals.iterm import ItermLauncher

    first_entered, release_first, second_entered = (threading.Event() for _ in range(3))
    def run(argv, **kwargs):
        if "a/one" in argv[3]:
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        return subprocess.CompletedProcess(argv, 0, "opened:test-session", "")

    monkeypatch.setattr(subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(ItermLauncher(config.state_dir)._open, resume_spec(config, task(config)))
        assert first_entered.wait(2)
        second = executor.submit(ItermLauncher(config.state_dir)._open, resume_spec(config, task(config, "two")))
        try:
            assert not second_entered.wait(0.05)
        finally:
            release_first.set()
        assert first.result(timeout=2) == second.result(timeout=2) == "opened:test-session"
    assert second_entered.is_set()


def test_cli_reports_terminal_failure_without_traceback(config, monkeypatch, capsys):
    import sys
    from cboard import cli
    from cboard.terminals.command import TerminalUnavailable

    async def unavailable(*args):
        raise TerminalUnavailable("자동 열기가 중단됐습니다. 명령 복사를 사용하세요.")

    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(cli, "inspect_board", unavailable)
    monkeypatch.setattr(sys, "argv", ["cboard", "open", "a/one"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "명령 복사" in output.err
    assert "Traceback" not in output.err


def test_repeated_open_requests_each_create_a_new_tab(config, monkeypatch):
    import subprocess
    from cboard.terminals.iterm import ItermLauncher
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"opened:tab-{len(calls)}", "")
    monkeypatch.setattr(subprocess, "run", run)
    launcher = ItermLauncher(config.state_dir)
    spec = resume_spec(config, task(config))
    assert launcher._open(spec) == "opened:tab-1"
    assert launcher._open(spec) == "opened:tab-2"
    assert len(calls) == 2
    for argv in calls:
        assert len(argv) == 5
        assert argv[4] == ""  # No configured profile keeps the default.
        outer = shlex.split(argv[3])
        assert outer[:2] == ["/bin/zsh", "-lc"]
        runner, shell = outer[2].rsplit("; ", 1)
        assert shlex.split(runner)[-2:] == ["_resume", "a/one"]
        assert shell == "exec /bin/zsh -l"


def test_focused_existing_tab_is_not_accepted_as_a_new_tab(config, monkeypatch):
    import subprocess
    from cboard.terminals.command import TerminalUnavailable
    from cboard.terminals.iterm import ItermLauncher
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "focused:old-tab", ""))
    with pytest.raises(TerminalUnavailable, match="탭 생성 결과"):
        ItermLauncher(config.state_dir)._open(resume_spec(config, task(config)))


@pytest.mark.parametrize("profile,background", [
    ("Alpha", "001e3a"), ("Alpha", "38220b"), ("Charlie", "28152e"),
    ("Bravo", "103019"), ("Delta", "3a1015"),
    ("Profile ' \" $(touch NEVER) `echo nope`", None),
])
def test_terminal_profile_and_color_are_scoped_to_account(config, monkeypatch, profile, background):
    import subprocess
    from cboard.terminals.iterm import ItermLauncher, _OPEN
    account = replace(config.accounts[0], iterm_profile=profile, iterm_background=background)
    cfg = replace(config, accounts=(account,))
    spec = resume_spec(cfg, task(cfg))
    calls = []
    real_run = subprocess.run
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "opened:colored-tab", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert ItermLauncher(cfg.state_dir)._open(spec) == "opened:colored-tab"
    argv = calls[0]
    assert argv[4] == profile
    assert profile not in _OPEN  # Untrusted names stay in argv, not script source.
    command = shlex.split(argv[3])
    assert command[:2] == ["/bin/zsh", "-lc"]
    body = command[2]
    if background:
        prefix, body = body.split("; ", 1)
        # Only the color printf runs here, in a shell without startup files.
        output = real_run(["/bin/zsh", "-f", "-c", prefix], capture_output=True, check=True)
        assert output.stdout == b"\x1b]1337;SetColors=bg=" + background.encode() + b"\x07"
    else:
        assert "SetColors" not in body
    runner, shell = body.rsplit("; ", 1)
    assert shlex.split(runner)[-2:] == ["_resume", "a/one"]
    assert shell == "exec /bin/zsh -l"
    assert spec.environment()["CODEX_HOME"] == str(account.home)
    assert not (account.home.parent / "NEVER").exists()
