from pathlib import Path
import tempfile

import pytest

from cboard.config import Config
from cboard.models import Account, Snapshot, Task, ThreadKey, Status, Detail


@pytest.fixture
def config(tmp_path):
    cwd = tmp_path / "project space ' $(touch NEVER) `echo nope`"
    cwd.mkdir()
    account_home = tmp_path / "account"
    account_home.mkdir()
    with tempfile.TemporaryDirectory(prefix="cbtest-", dir="/private/tmp") as short_dir:
        account = Account("a", "Account A", account_home, Path(short_dir) / "server.sock")
        yield Config((account,), Path("/bin/echo"), tmp_path / "state", tmp_path / "cache", 100, 100, 1)


def task(config, thread_id="one", status=Status.READY, account_id="a"):
    return Task(ThreadKey(account_id, thread_id), f"작업 {thread_id}", str(next(config.accounts[0].home.parent.glob('project*'))), status, 100, 100, "model", "medium", "vscode", raw_status={"type": "idle"})


class FakeSource:
    def __init__(self, tasks):
        self.tasks = tasks
        self.delay = {}
        self.closed = False

    async def refresh(self):
        return Snapshot(self.tasks[0].key.account_id if self.tasks else "a", tuple(self.tasks), 100, True)

    async def refresh_loaded(self):
        return await self.refresh()

    async def detail(self, thread_id, *, pages=3):
        import asyncio
        await asyncio.sleep(self.delay.get(thread_id, 0))
        t = next(t for t in self.tasks if t.key.thread_id == thread_id)
        return Detail(t, "요청 " + thread_id, "답변 " + thread_id, "final_answer")

    async def read_task(self, thread_id):
        return next(t for t in self.tasks if t.key.thread_id == thread_id)

    async def close(self):
        self.closed = True
