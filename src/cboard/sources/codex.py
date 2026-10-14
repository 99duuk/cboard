from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from ..models import Account, Detail, Snapshot, Task, plain
from .rpc import ReadOnlyRpc, RpcError


def message_detail(task: Task, items: list[dict], complete: bool) -> Detail:
    """Items are newest first; only choose messages in the latest user turn."""
    last_user = ""
    assistant = []
    for item in items:
        kind = item.get("type")
        if kind == "userMessage":
            parts = []
            for part in item.get("content", []):
                if part.get("type") == "text":
                    parts.append(plain(part.get("text")))
                else:
                    parts.append(f"[{part.get('type', 'attachment')} 첨부]")
            last_user = "\n".join(parts)
            break
        if kind == "agentMessage":
            assistant.append(item)
    chosen = next((item for item in assistant if item.get("phase") == "final_answer"), assistant[0] if assistant else None)
    return Detail(task, last_user, plain(chosen.get("text")) if chosen else "", chosen.get("phase") or "message" if chosen else "", complete)


class CodexSource:
    def __init__(self, account: Account, timeout: float = 5, initial: Snapshot | None = None):
        self.account = account
        self.rpc = ReadOnlyRpc(account.socket_path, timeout)
        self.snapshot = initial or Snapshot(account.id)
        self._refresh_lock = asyncio.Lock()
        self._items_supported = True
        self._materialized_ids: set[str] = set()

    async def read_task(self, thread_id: str, *, verify_saved: bool = True) -> Task:
        result = await self.rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False})
        task = Task.from_thread(self.account.id, result["thread"], time.time())
        if task.is_child or task.ephemeral:
            return task
        if not verify_saved and thread_id in self._materialized_ids:
            return replace(task, persisted=True)
        # Loaded metadata also exists before the first message creates saved history.
        try:
            await self.rpc.call("thread/turns/list", {"threadId": thread_id, "limit": 1, "itemsView": "summary", "sortDirection": "desc"})
        except RpcError as exc:
            message = str(exc).lower()
            if "not materialized" in message or "before first user message" in message:
                self._materialized_ids.discard(thread_id)
                return replace(task, persisted=False)
            if exc.code in {-32601, -32602} or "not supported" in message:
                return task
            raise
        self._materialized_ids.add(thread_id)
        return replace(task, persisted=True)

    async def _loaded_tasks(self) -> dict[str, Task]:
        result = await self.rpc.call("thread/loaded/list")
        tasks = await asyncio.gather(*(self._read_if_present(thread_id) for thread_id in result.get("data", [])))
        return {t.key.thread_id: t for t in tasks if t is not None}

    async def _read_if_present(self, thread_id: str) -> Task | None:
        try:
            return await self.read_task(thread_id, verify_saved=False)
        except RpcError as exc:
            message = str(exc).lower()
            # A thread may close between the loaded-ID list and its metadata read.
            if "thread" in message and any(term in message for term in ["not found", "not loaded", "no saved"]):
                self._materialized_ids.discard(thread_id)
                return None
            raise

    async def refresh(self) -> Snapshot:
        async with self._refresh_lock:
            tasks, cursor, seen = {}, None, set()
            try:
                while True:
                    params = {"limit": 100, "sortKey": "updated_at", "archived": False, "useStateDbOnly": True}
                    if cursor:
                        params["cursor"] = cursor
                    result = await self.rpc.call("thread/list", params)
                    observed = time.time()
                    for thread in result.get("data", []):
                        task = replace(Task.from_thread(self.account.id, thread, observed), persisted=True)
                        tasks[task.key.thread_id] = task
                    cursor = result.get("nextCursor")
                    if not cursor:
                        break
                    if cursor in seen:
                        raise RpcError("Repeated pagination cursor")
                    seen.add(cursor)
                tasks.update(await self._loaded_tasks())
                self._materialized_ids.intersection_update(tasks)
                self.snapshot = Snapshot(self.account.id, tuple(tasks.values()), time.time(), True)
            except Exception as exc:
                # Preserve the last good statuses. A network failure isn't Inactive.
                if tasks and not self.snapshot.tasks:
                    self.snapshot = Snapshot(self.account.id, tuple(tasks.values()), time.time(), False, plain(exc), True)
                else:
                    self.snapshot = replace(self.snapshot, error=plain(exc), stale=True)
            return self.snapshot

    async def refresh_loaded(self) -> Snapshot:
        if self._refresh_lock.locked() or not self.snapshot.complete or self.snapshot.stale:
            return await self.refresh()
        async with self._refresh_lock:
            try:
                loaded = await self._loaded_tasks()
                now = time.time()
                tasks = {task.key.thread_id: task for task in self.snapshot.tasks}
                # Re-read formerly loaded tasks to confirm notLoaded; don't infer from PID.
                gone = [t for t in tasks.values() if t.raw_status.get("type") != "notLoaded" and t.key.thread_id not in loaded]
                reads = await asyncio.gather(*(self._read_if_present(t.key.thread_id) for t in gone))
                for old, task in zip(gone, reads):
                    if task is None:
                        tasks.pop(old.key.thread_id, None)
                    else:
                        tasks[task.key.thread_id] = task
                tasks.update(loaded)
                self.snapshot = Snapshot(self.account.id, tuple(tasks.values()), now, True)
            except Exception as exc:
                self.snapshot = replace(self.snapshot, error=plain(exc), stale=True)
            return self.snapshot

    async def detail(self, thread_id: str, *, pages: int = 3) -> Detail:
        task = await self.read_task(thread_id, verify_saved=False)
        if task.persisted is False:
            return Detail(task)
        if self._items_supported:
            try:
                items, cursor, complete = [], None, False
                for _ in range(pages):
                    params = {"threadId": thread_id, "limit": 50, "sortDirection": "desc"}
                    if cursor:
                        params["cursor"] = cursor
                    result = await self.rpc.call("thread/items/list", params)
                    items.extend(entry.get("item", entry) for entry in result.get("data", []))
                    cursor = result.get("nextCursor")
                    complete = not cursor or any(item.get("type") == "userMessage" for item in items)
                    if complete:
                        break
                return message_detail(task, items, complete)
            except RpcError as exc:
                if exc.code not in {-32601, -32600, -32602} and "support" not in str(exc).lower():
                    raise
                self._items_supported = False
        result = await self.rpc.call("thread/turns/list", {"threadId": thread_id, "limit": min(pages, 10), "itemsView": "full", "sortDirection": "desc"})
        turns = result.get("data", [])
        items = [item for turn in turns for item in reversed(turn.get("items", []))]
        return message_detail(task, items, not result.get("nextCursor") or any(i.get("type") == "userMessage" for i in items))

    async def close(self):
        await self.rpc.close()
