"""Export deterministic sample UI screenshots; never connects to real accounts."""
import asyncio
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path

os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

from cboard.config import load_config
from cboard.models import Detail, Snapshot, Status, Task, ThreadKey
from cboard.services import Board
from cboard.ui.app import CboardApp


class SampleSource:
    def __init__(self, account, task):
        self.account, self.task = account, task

    async def refresh(self):
        return Snapshot(self.account.id, (self.task,), time.time(), True)

    async def refresh_loaded(self):
        return await self.refresh()

    async def detail(self, thread_id, *, pages=3):
        return Detail(self.task, "모바일에서 신청 버튼과 여백을 확인해줘.", "모바일 신청 버튼을 확인했습니다.\n\n- 버튼 높이와 좌우 여백을 수정했습니다.\n- 390px 화면에서 가로 스크롤이 없습니다.\n- 다음에는 실제 휴대폰에서 확인하면 됩니다.", "final_answer")

    async def close(self):
        pass


async def main():
    base = load_config()
    output = Path(__file__).resolve().parents[1] / "artifacts/screenshots"
    output.mkdir(parents=True, exist_ok=True)
    titles = ["견학 신청 화면 모바일 확인", "블로그 수집 오류 조사", "배포 설정 정리", "검색 결과 개선", "최근 로그 확인"]
    statuses = [Status.READY, Status.WORKING, Status.INACTIVE, Status.NEEDS_YOU, Status.INACTIVE]
    with tempfile.TemporaryDirectory(prefix="cboard-visual-", dir="/private/tmp") as directory:
        config = replace(base, state_dir=Path(directory) / "state", cache_dir=Path(directory) / "cache", loaded_interval=100, list_interval=100)
        sources = {}
        for index, account in enumerate(config.accounts):
            cwd = Path.home() / "Documents/workspace" / ("bottling-workspace" if index < 3 else "mnemosyne-workspace") / ("slot-3/frontend" if index == 1 else "slot-1")
            task = Task(ThreadKey(account.id, f"sample-{index}"), titles[index % len(titles)], str(cwd), statuses[index % len(statuses)], int(time.time()) - index * 3600, time.time(), "gpt-6-luna", "medium", "vscode", raw_status={"type": "idle"})
            sources[account.id] = SampleSource(account, task)
        for size in [(160, 45), (120, 35), (80, 24)]:
            app = CboardApp(config, board=Board(config, sources))
            async with app.run_test(size=size) as pilot:
                await pilot.pause(0.3)
                app._set_workspace("all")
                app.query_one("#tasks").focus()
                await pilot.pause()
                (output / f"cboard-{size[0]}x{size[1]}.svg").write_text(app.export_screenshot(title="Cboard · QA sample"))
                await pilot.press("right")
                await pilot.pause()
                (output / f"cboard-{size[0]}x{size[1]}-workspace.svg").write_text(app.export_screenshot(title="Cboard · QA workspace"))
                if size[0] < 100:
                    await pilot.press("d")
                    await pilot.pause()
                    (output / f"cboard-{size[0]}x{size[1]}-detail.svg").write_text(app.export_screenshot(title="Cboard · QA sample detail"))
                print(f"Exported {size[0]}×{size[1]}")


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    asyncio.run(main())
