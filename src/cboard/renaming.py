from __future__ import annotations

import time
from typing import Protocol

from .config import Config
from .models import Task, ThreadKey
from .sources.rpc import ReadOnlyRpc, RpcError


def validate_name(name: str) -> str:
    name = name.strip()
    if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("세션 이름은 비어 있지 않은 한 줄로 입력하세요")
    return name


class SessionRenamer(Protocol):
    async def rename(self, key: ThreadKey, name: str) -> Task: ...


class _RenameRpc(ReadOnlyRpc):
    async def rename_thread(self, thread_id: str, name: str):
        async with self._limit:
            await self._connect()
            try:
                await self._request("thread/name/set", {"threadId": thread_id, "name": name})
            except (TimeoutError, RpcError, OSError) as exc:
                raise RpcError("이름 변경 결과를 확인하지 못했습니다. 목록을 새로고침하세요. 자동 재시도하지 않습니다.") from exc


class CodexSessionRenamer:
    def __init__(self, config: Config):
        self.config = config

    async def rename(self, key: ThreadKey, name: str) -> Task:
        name = validate_name(name)
        account = self.config.account(key.account_id)
        rpc = _RenameRpc(account.socket_path, self.config.timeout)
        try:
            before = await rpc.call("thread/read", {"threadId": key.thread_id, "includeTurns": False})
            task = Task.from_thread(key.account_id, before["thread"], time.time())
            if task.key != key:
                raise ValueError("이름 변경 대상의 대화 ID가 일치하지 않습니다")
            if task.ephemeral:
                raise ValueError("저장되지 않는 임시 대화의 이름은 변경할 수 없습니다")
            await rpc.rename_thread(key.thread_id, name)
            result = await rpc.call("thread/read", {"threadId": key.thread_id, "includeTurns": False})
            task = Task.from_thread(key.account_id, result["thread"], time.time())
            if task.key != key or result["thread"].get("name") != name:
                raise RpcError("서버의 변경된 이름을 확인하지 못했습니다. 목록을 새로고침하세요")
            return task
        finally:
            await rpc.close()
