from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class Status(StrEnum):
    NEEDS_YOU = "Needs you"
    WORKING = "Working"
    READY = "Ready"
    INACTIVE = "Inactive"
    ERROR = "Error"
    UNKNOWN = "Unknown"


STATUS_ORDER = {status: i for i, status in enumerate(Status)}
_ESCAPE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def plain(value: object) -> str:
    text = _ESCAPE.sub("", str(value or ""))
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))


@dataclass(frozen=True)
class ThreadKey:
    account_id: str
    thread_id: str

    @property
    def encoded(self) -> str:
        return f"{self.account_id}/{self.thread_id}"

    @classmethod
    def decode(cls, value: str) -> ThreadKey:
        account_id, thread_id = value.split("/", 1)
        if not account_id or not thread_id:
            raise ValueError("invalid thread key")
        return cls(account_id, thread_id)


@dataclass(frozen=True)
class Account:
    id: str
    label: str
    home: Path
    socket_path: Path
    iterm_profile: str | None = None
    iterm_background: str | None = None


def map_status(raw: dict) -> Status:
    kind = raw.get("type")
    if kind == "active":
        flags = set(raw.get("activeFlags") or [])
        if flags - {"waitingOnApproval", "waitingOnUserInput"}:
            return Status.UNKNOWN
        return Status.NEEDS_YOU if flags else Status.WORKING
    return {"idle": Status.READY, "notLoaded": Status.INACTIVE, "systemError": Status.ERROR}.get(kind, Status.UNKNOWN)


@dataclass(frozen=True)
class Task:
    key: ThreadKey
    title: str
    cwd: str
    status: Status
    updated_at: int
    observed_at: float = field(compare=False)
    model: str | None = None
    reasoning_effort: str | None = None
    source: str = "unknown"
    parent_thread_id: str | None = None
    ephemeral: bool = False
    raw_status: dict = field(default_factory=dict)
    persisted: bool | None = None

    @classmethod
    def from_thread(cls, account_id: str, thread: dict, observed_at: float) -> Task:
        source = thread.get("source", "unknown")
        if isinstance(source, dict):
            source = "subAgent" if "subAgent" in source else "custom"
        title = thread.get("name") or thread.get("preview") or thread.get("id") or "Untitled"
        title = plain(title).splitlines()[0] if plain(title).splitlines() else "Untitled"
        raw = thread.get("status") or {}
        return cls(
            ThreadKey(account_id, thread["id"]), title, plain(thread.get("cwd")),
            map_status(raw), int(thread.get("recencyAt") or thread.get("updatedAt") or 0),
            observed_at, thread.get("model"), thread.get("reasoningEffort"), str(source),
            thread.get("parentThreadId"), bool(thread.get("ephemeral")), raw,
        )

    @property
    def is_child(self) -> bool:
        return self.parent_thread_id is not None or self.source == "subAgent"

    @property
    def project(self) -> str:
        return str(Path(self.cwd).resolve()) if self.cwd else "Unassigned"

    def to_json(self) -> dict:
        result = asdict(self)
        result["status"] = self.status.value
        return result

    @classmethod
    def from_json(cls, data: dict) -> Task:
        values = dict(data)
        values["key"] = ThreadKey(**values["key"])
        values["status"] = Status(values["status"])
        return cls(**values)


@dataclass(frozen=True)
class Detail:
    task: Task
    last_user: str = ""
    last_assistant: str = ""
    assistant_phase: str = ""
    complete: bool = True
    token_usage: dict | None = None


@dataclass(frozen=True)
class Snapshot:
    account_id: str
    tasks: tuple[Task, ...] = ()
    fetched_at: float = 0
    complete: bool = False
    error: str | None = None
    stale: bool = False


class TaskSource(Protocol):
    async def refresh(self) -> Snapshot: ...
    async def refresh_loaded(self) -> Snapshot: ...
    async def detail(self, thread_id: str, *, pages: int = 3) -> Detail: ...
    async def read_task(self, thread_id: str) -> Task: ...
    async def close(self) -> None: ...
