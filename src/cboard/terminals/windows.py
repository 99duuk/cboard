from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import sys
from pathlib import Path

from .command import ResumeSpec, TerminalUnavailable, powershell_literal


class WindowsLauncher:
    def __init__(self, state_dir: Path, config_path: Path | None = None):
        self.config_path = config_path

    def _open(self, spec: ResumeSpec) -> str:
        shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not shell:
            raise TerminalUnavailable("PowerShell을 찾을 수 없습니다. 명령 복사를 사용하세요")
        runner = [sys.executable, "-m", "cboard"]
        if self.config_path:
            runner.extend(["--config", str(self.config_path)])
        runner.extend(["_resume", spec.key.encoded])
        script = "& " + " ".join(powershell_literal(value) for value in runner)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        argv = [shell, "-NoLogo", "-NoProfile", "-NoExit", "-EncodedCommand", encoded]
        terminal = shutil.which("wt.exe")
        if terminal:
            argv = [terminal, "new-tab", "--", *argv]
        try:
            subprocess.Popen(argv, cwd=spec.cwd, creationflags=subprocess.CREATE_NEW_CONSOLE if not terminal else 0)
        except OSError as exc:
            raise TerminalUnavailable("Windows 터미널을 열지 못했습니다. 명령 복사를 사용하세요") from exc
        return "started:windows-terminal"

    async def open(self, spec: ResumeSpec) -> str:
        return await asyncio.to_thread(self._open, spec)
