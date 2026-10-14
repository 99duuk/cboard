"""Synthetic repeatable benchmark. No real accounts, transcripts, or terminal automation."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from textual.widgets import DataTable

import cboard.services as services
from cboard.config import Config
from cboard.models import Account, Detail, Snapshot, Status, Task, ThreadKey
from cboard.services import Board
from cboard.sources.codex import CodexSource
from cboard.ui.app import CboardApp


class SampleSource:
    def __init__(self, account, tasks):
        self.account, self.tasks = account, tasks

    async def refresh(self):
        return Snapshot(self.account.id, tuple(replace(task, observed_at=time.time()) for task in self.tasks), time.time(), True)

    refresh_loaded = refresh

    async def detail(self, ident, *, pages=3):
        return Detail(next(task for task in self.tasks if task.key.thread_id == ident), "샘플 요청", "샘플 답변", "final_answer")

    async def close(self):
        pass


async def measure(count: int, rounds: int):
    with tempfile.TemporaryDirectory(prefix="cboard-bench-") as folder:
        root = Path(folder)
        accounts = tuple(Account(f"a{i}", f"ACCOUNT {i}", root / f"account-{i}", root / f"socket-{i}") for i in range(5))
        config = Config(accounts, Path("/bin/echo"), root / "state", root / "cache", 3600, 3600, 5)
        now = int(time.time()) - 10000
        sources = {}
        for index, account in enumerate(accounts):
            tasks = [Task(ThreadKey(account.id, f"thread-{i}"), f"샘플 작업 {i}", str(root / f"project-{i % 10}"), Status.INACTIVE, now - i * 60, now, raw_status={"type": "notLoaded"}, persisted=True) for i in range(index, count, 5)]
            sources[account.id] = SampleSource(account, tasks)
        board = Board(config, sources)
        cache_writes = 0
        original_save = services.save_snapshot
        def save(*args):
            nonlocal cache_writes
            cache_writes += 1
            original_save(*args)
        services.save_snapshot = save
        try:
            await board.refresh()
            start = time.perf_counter()
            for _ in range(200):
                assert len(board.tasks) == count
            index_ms = (time.perf_counter() - start) * 1000 / 200
            start = time.perf_counter()
            for i in range(30):
                board.visible({}, query=f"작업 {i}")
            search_ms = (time.perf_counter() - start) * 1000 / 30
            app = CboardApp(config, board=board)
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                table = app.query_one("#tasks", DataTable)
                clears = 0
                original_clear = table.clear
                def clear(*args, **kwargs):
                    nonlocal clears
                    clears += 1
                    return original_clear(*args, **kwargs)
                table.clear = clear
                render_samples = []
                for _ in range(rounds):
                    start = time.perf_counter()
                    app._render_tasks()
                    render_samples.append((time.perf_counter() - start) * 1000)
                    await pilot.pause()
                cache_writes = 0
                clears = 0
                poll_samples = []
                cpu_start = time.process_time()
                for _ in range(rounds):
                    start = time.perf_counter()
                    await app.poll(full=False).wait()
                    poll_samples.append((time.perf_counter() - start) * 1000)
                    await pilot.pause()
                poll_cpu_ms = (time.process_time() - cpu_start) * 1000 / rounds
                result = {"rows": count, "rounds": rounds, "index_lookup_ms": index_ms, "search_ms": search_ms, "unchanged_render_median_ms": statistics.median(render_samples), "loaded_poll_median_ms": statistics.median(poll_samples), "loaded_poll_cpu_ms": poll_cpu_ms, "loaded_poll_table_rebuilds": clears, "loaded_poll_cache_writes": cache_writes}
        finally:
            services.save_snapshot = original_save
        source = CodexSource(accounts[0])
        rpc_calls = Counter()
        records = [{"id": f"r{i}", "name": f"샘플 {i}", "cwd": str(root), "source": "cli", "status": {"type": "idle"}, "updatedAt": now} for i in range(20)]
        async def rpc(method, params=None):
            rpc_calls[method] += 1
            await asyncio.sleep(0.001)
            if method == "thread/list":
                return {"data": records}
            if method == "thread/loaded/list":
                return {"data": [record["id"] for record in records]}
            if method == "thread/read":
                return {"thread": next(record for record in records if record["id"] == params["threadId"])}
            if method == "thread/turns/list":
                return {"data": []}
            raise AssertionError(method)
        source.rpc.call = rpc
        try:
            await source.refresh()
            rpc_calls.clear()
            for _ in range(rounds):
                await source.refresh_loaded()
            result["source_poll_rpc_per_round"] = dict((key, value / rounds) for key, value in rpc_calls.items())
        finally:
            await source.close()
        return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    results = [await measure(rows, args.rounds) for rows in (700, 2000)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"kind": "synthetic", "size": [120, 35], "results": results}, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
