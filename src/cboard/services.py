from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Callable

from .config import Config
from .models import Snapshot, Task, TaskSource
from .state import load_snapshot, save_snapshot
from .workspaces import RECENT_SECONDS, workspace_root


class Board:
    def __init__(self, config: Config, sources: dict[str, TaskSource] | None = None):
        self.config = config
        self.snapshots = {a.id: load_snapshot(config.cache_dir, a.id) for a in config.accounts}
        if sources is None:
            from .sources.codex import CodexSource
            sources = {a.id: CodexSource(a, config.timeout, self.snapshots[a.id]) for a in config.accounts}
        self.sources = sources
        self.errors = {}
        self._indexed_tuples = ()
        self._tasks_index = {}

    async def refresh_account(self, account_id: str, *, loaded: bool = False) -> Snapshot:
        source = self.sources[account_id]
        old = self.snapshots[account_id]
        try:
            result = await (source.refresh_loaded() if loaded else source.refresh())
            if result.account_id != account_id or any(t.key.account_id != account_id for t in result.tasks):
                raise ValueError("Data source returned a different account")
        except Exception as exc:
            result = Snapshot(account_id, old.tasks, old.fetched_at, old.complete, str(exc), True)
        self.snapshots[account_id] = result
        # Full refreshes still save freshness every 15s; changed statuses save immediately.
        if result.complete and not result.stale and (not loaded or old.tasks != result.tasks or old.stale or not old.complete or account_id in self.errors):
            try:
                await asyncio.to_thread(save_snapshot, self.config.cache_dir, result)
                self.errors.pop(account_id, None)
            except OSError as exc:
                self.errors[account_id] = f"목록 저장 실패: {exc}"
        return result

    async def refresh(self, *, loaded: bool = False, on_update: Callable | None = None):
        async def update(account_id):
            await self.refresh_account(account_id, loaded=loaded)
            if on_update:
                on_update()
        await asyncio.gather(*(update(account_id) for account_id in self.sources))

    @property
    def tasks(self) -> dict[str, Task]:
        tuples = tuple(snapshot.tasks for snapshot in self.snapshots.values())
        if len(tuples) != len(self._indexed_tuples) or any(new is not old for new, old in zip(tuples, self._indexed_tuples)):
            self._tasks_index = {t.key.encoded: t for tasks in tuples for t in tasks if not t.is_child}
            self._indexed_tuples = tuples
        return self._tasks_index

    def workspace_counts(self, state: dict, *, show_hidden: bool = False):
        hidden = set(state.get("hidden_keys", []))
        cutoff = time.time() - RECENT_SECONDS
        return Counter(workspace_root(t.cwd) for t in self.tasks.values()
                       if t.updated_at >= cutoff and (show_hidden or t.key.encoded not in hidden))

    def visible(self, state: dict, *, account: str = "all", query: str = "", status: str = "All", show_hidden: bool = False, workspace: str = "all"):
        hidden = set(state.get("hidden_keys", []))
        query = query.casefold()
        labels = {a.id: a.label for a in self.config.accounts}
        candidates = []
        cutoff = time.time() - RECENT_SECONDS
        for task in self.tasks.values():
            if workspace != "all" and (task.updated_at < cutoff or workspace_root(task.cwd) != workspace):
                continue
            if account != "all" and task.key.account_id != account:
                continue
            if not show_hidden and task.key.encoded in hidden:
                continue
            if query and query not in f"{task.title} {task.cwd} {labels.get(task.key.account_id, '')}".casefold():
                continue
            candidates.append(task)
        counts = Counter(t.status.value for t in candidates)
        result = candidates if status == "All" else [t for t in candidates if t.status.value == status]
        return sorted(result, key=lambda t: (-t.updated_at, t.key.encoded)), counts

    async def close(self):
        await asyncio.gather(*(s.close() for s in self.sources.values()), return_exceptions=True)
