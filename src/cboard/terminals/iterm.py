from __future__ import annotations

import asyncio
import fcntl
import shlex
import subprocess
import sys
import threading
from pathlib import Path

from .command import ResumeSpec, TerminalUnavailable

_STOPPED = "자동 열기가 중단됐습니다. 명령 복사를 사용하세요. iTerm2 상태를 확인한 뒤 Cboard를 다시 실행하세요."

# Native iTerm scripting also works when its optional Python API is disabled.
# All user data is passed as argv, never interpolated into AppleScript source.
_OPEN = '''on run argv
    set launchCommand to item 1 of argv
    set profileName to item 2 of argv
    with timeout of 10 seconds
    tell application "iTerm2"
        if (count of windows) is 0 then
            if profileName is "" then
                set targetWindow to create window with default profile command launchCommand
            else
                set targetWindow to create window with profile profileName command launchCommand
            end if
            set openedTab to current tab of targetWindow
        else
            set targetWindow to current window
            tell targetWindow
                if profileName is "" then
                    set openedTab to create tab with default profile command launchCommand
                else
                    set openedTab to create tab with profile profileName command launchCommand
                end if
            end tell
        end if
        set openedSession to current session of openedTab
        select openedTab
        select openedSession
        activate
        return "opened:" & (unique ID of openedSession)
    end tell
    end timeout
end run'''


class ItermLauncher:
    def __init__(self, state_dir: Path, config_path: Path | None = None):
        self.state_dir = state_dir
        self.config_path = config_path
        self._launch_lock = threading.Lock()
        self._unavailable = False

    def _open(self, spec: ResumeSpec) -> str:
        # A queued request must also see the failure of the preceding request.
        with self._launch_lock:
            if self._unavailable:
                raise TerminalUnavailable(_STOPPED)
            try:
                return self._open_once(spec)
            except TerminalUnavailable:
                self._unavailable = True
                raise

    def _open_once(self, spec: ResumeSpec) -> str:
        lock_dir = self.state_dir / "launch-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        runner = [sys.executable, "-m", "cboard"]
        if self.config_path:
            runner.extend(["--config", str(self.config_path)])
        runner.extend(["_resume", spec.key.encoded])
        commands = []
        if spec.iterm_background:
            commands.append(shlex.join(["printf", r"\033]1337;SetColors=bg=%s\a", spec.iterm_background]))
        # Return to a normal shell after Codex exits, as an ordinary terminal tab does.
        commands.extend([shlex.join(runner), "exec /bin/zsh -l"])
        command = shlex.join(["/bin/zsh", "-lc", "; ".join(commands)])
        # Serialize automation across conversations and Cboard processes.
        with (lock_dir / "iterm.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                result = subprocess.run(
                    ["/usr/bin/osascript", "-e", _OPEN, command, spec.iterm_profile or ""],
                    capture_output=True, text=True, timeout=20,
                )
            except subprocess.TimeoutExpired as exc:
                raise TerminalUnavailable("iTerm2 응답이 지연됩니다. " + _STOPPED) from exc
            except OSError as exc:
                raise TerminalUnavailable("iTerm2 자동화를 실행하지 못했습니다. " + _STOPPED) from exc
        if result.returncode:
            raise TerminalUnavailable("iTerm2 연결 실패: " + result.stderr.strip()[:300] + "\n" + _STOPPED)
        response = result.stdout.strip()
        if not response.startswith("opened:") or not response.removeprefix("opened:").strip():
            # The tab may already exist; retrying can create another one.
            raise TerminalUnavailable("iTerm2의 탭 생성 결과를 확인하지 못했습니다. " + _STOPPED)
        return response

    async def open(self, spec: ResumeSpec) -> str:
        return await asyncio.to_thread(self._open, spec)
