from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Protocol

from ..config import Config
from ..models import Task, ThreadKey


@dataclass(frozen=True)
class ResumeSpec:
    key: ThreadKey
    argv: tuple[str, ...]
    cwd: str
    account_home: str
    iterm_profile: str | None = None
    iterm_background: str | None = None

    @property
    def command(self):
        return shlex.join(["env", f"CODEX_HOME={self.account_home}", *self.argv])

    def environment(self):
        return os.environ | {"CODEX_HOME": self.account_home}


class TerminalUnavailable(RuntimeError):
    """Stop opening further tabs when terminal automation fails."""


class TerminalLauncher(Protocol):
    async def open(self, spec: ResumeSpec) -> str: ...


def resume_spec(config: Config, task: Task) -> ResumeSpec:
    if task.ephemeral:
        raise ValueError("저장되지 않는 임시 대화는 재개할 수 없습니다")
    if task.persisted is False:
        raise ValueError("첫 사용자 메시지 전의 빈 세션은 아직 저장되지 않아 재개할 수 없습니다. 기존 열린 세션에서 작업을 시작하거나 새 세션을 사용하세요")
    if not task.cwd or not os.path.isdir(task.cwd):
        raise ValueError(f"프로젝트 폴더를 찾을 수 없습니다: {task.cwd}")
    if not config.executable.is_file() or not os.access(config.executable, os.X_OK):
        raise ValueError(f"Codex 실행 파일을 찾을 수 없습니다: {config.executable}")
    if not task.key.thread_id or task.key.thread_id.startswith("-") or "\x00" in task.key.thread_id:
        raise ValueError("Invalid thread ID")
    account = config.account(task.key.account_id)
    return ResumeSpec(task.key, (str(config.executable), "-C", task.cwd, "resume", task.key.thread_id), task.cwd, str(account.home), account.iterm_profile, account.iterm_background)
