from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from .config import Config
from .models import Task, ThreadKey
from .sources.rpc import ReadOnlyRpc, RpcError

# Empty sourceKinds means interactive sources only, so include subagents explicitly.
_ALL_SOURCES = ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]


@dataclass(frozen=True)
class DeletePlan:
    task: Task
    descendants: tuple[Task, ...] = ()

    @property
    def keys(self) -> set[str]:
        return {task.key.encoded for task in (self.task, *self.descendants)}


class SessionDeleter(Protocol):
    async def prepare(self, key: ThreadKey) -> DeletePlan: ...
    async def delete(self, plan: DeletePlan) -> None: ...


class _DeleteRpc(ReadOnlyRpc):
    """A separate connection with one explicit mutation, never used by observers."""

    async def delete_thread(self, thread_id: str):
        async with self._limit:
            await self._connect()
            try:
                await self._request("thread/delete", {"threadId": thread_id})
            except (TimeoutError, RpcError, OSError) as exc:
                # A lost response can follow a successful deletion; never retry here.
                raise RpcError("삭제 결과를 확인하지 못했습니다. 목록을 새로고침하세요. 자동 재시도하지 않습니다.") from exc


class CodexSessionDeleter:
    def __init__(self, config: Config):
        self.config = config

    async def _read(self, rpc: ReadOnlyRpc, key: ThreadKey) -> Task:
        result = await rpc.call("thread/read", {"threadId": key.thread_id, "includeTurns": False})
        task = Task.from_thread(key.account_id, result["thread"], time.time())
        if task.key != key:
            raise ValueError("삭제 대상의 대화 ID가 일치하지 않습니다")
        return task

    def _check_idle(self, task: Task):
        if task.raw_status.get("type") not in {"idle", "notLoaded"}:
            raise ValueError("작업 중이거나 상태를 확인할 수 없는 대화가 있어 삭제를 중단했습니다. 해당 Codex에서 작업을 중단해 Ready 상태로 만든 뒤 다시 삭제하세요")

    async def _prepare(self, rpc: ReadOnlyRpc, key: ThreadKey) -> DeletePlan:
        task = await self._read(rpc, key)
        if task.is_child or task.ephemeral:
            raise ValueError("저장된 최상위 대화만 삭제할 수 있습니다")
        self._check_idle(task)
        descendants = {}
        for archived in (False, True):
            cursor, seen = None, set()
            while True:
                params = {"ancestorThreadId": key.thread_id, "archived": archived, "sourceKinds": _ALL_SOURCES, "limit": 100, "useStateDbOnly": True}
                if cursor:
                    params["cursor"] = cursor
                page = await rpc.call("thread/list", params)
                for raw in page.get("data", []):
                    child = Task.from_thread(key.account_id, raw, time.time())
                    if child.key == key:
                        raise ValueError("삭제 범위 조회가 올바르지 않습니다")
                    if child.ephemeral:
                        raise ValueError("저장되지 않은 임시 하위 대화가 있어 삭제를 중단했습니다")
                    self._check_idle(child)
                    descendants[child.key.thread_id] = child
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError("하위 대화 목록 조회가 반복되어 삭제를 중단했습니다")
                seen.add(cursor)
        loaded = (await rpc.call("thread/loaded/list")).get("data", [])
        # Persisted lists omit ephemeral descendants. Check loaded ancestry as well.
        metadata = {key.thread_id: task, **descendants}
        for thread_id in loaded:
            loaded_task = await self._read(rpc, ThreadKey(key.account_id, thread_id))
            metadata[thread_id] = loaded_task
            current, visited = thread_id, set()
            while current:
                if current == key.thread_id:
                    for related_id in {thread_id, *visited}:
                        related = metadata[related_id]
                        if related.ephemeral:
                            raise ValueError("저장되지 않은 임시 하위 대화가 있어 삭제를 중단했습니다")
                        self._check_idle(related)
                        if related_id != key.thread_id:
                            descendants[related_id] = related
                    break
                if current in visited or len(visited) >= 128:
                    raise ValueError("하위 대화 관계를 확인할 수 없어 삭제를 중단했습니다")
                visited.add(current)
                if current not in metadata:
                    metadata[current] = await self._read(rpc, ThreadKey(key.account_id, current))
                current = metadata[current].parent_thread_id
        return DeletePlan(metadata[key.thread_id], tuple(descendants[k] for k in sorted(descendants)))

    async def prepare(self, key: ThreadKey) -> DeletePlan:
        account = self.config.account(key.account_id)
        rpc = ReadOnlyRpc(account.socket_path, self.config.timeout)
        try:
            return await self._prepare(rpc, key)
        finally:
            await rpc.close()

    async def delete(self, plan: DeletePlan) -> None:
        account = self.config.account(plan.task.key.account_id)
        rpc = _DeleteRpc(account.socket_path, self.config.timeout)
        try:
            fresh = await self._prepare(rpc, plan.task.key)
            if fresh.keys != plan.keys or (fresh.task.title, fresh.task.cwd) != (plan.task.title, plan.task.cwd):
                raise ValueError("삭제 대상 또는 범위가 바뀌었습니다. 확인창을 다시 열어주세요")
            await rpc.delete_thread(plan.task.key.thread_id)
        finally:
            await rpc.close()
