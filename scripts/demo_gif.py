"""Record the real Textual UI with offline sample data and encode a README GIF."""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from visual_qa import SampleSource
from cboard.config import Config, DEFAULT_ACCOUNTS
from cboard.models import Account, Detail, Status, Task, ThreadKey
from cboard.services import Board
from cboard.ui.app import CboardApp


class DemoSource(SampleSource):
    def __init__(self, account, task, request, answer):
        super().__init__(account, task)
        self.request, self.answer = request, answer

    async def detail(self, thread_id, *, pages=3):
        assert thread_id == self.task.key.thread_id
        phase = "commentary" if self.task.status in {Status.WORKING, Status.NEEDS_YOU} else "final_answer"
        return Detail(self.task, self.request, self.answer, phase)


class DemoLauncher:
    async def open(self, spec):
        raise AssertionError("The demo must never launch a terminal")


async def record(folder: Path):
    home = Path("/Users/demo")
    samples = [
        ("app-workspace/slot-1", "모바일 신청 화면 확인", Status.READY,
         "모바일에서 신청 버튼과 여백을 확인해줘.", "버튼 높이와 여백을 수정했습니다.\n390px 화면에서 가로 스크롤이 없습니다."),
        ("app-workspace/slot-3/frontend", "검색 결과 개선", Status.WORKING,
         "검색 결과에서 중복 항목이 나오는지 확인해줘.", "중복 항목이 생기는 조건을 확인했습니다.\n지금 검색 결과를 정리하고 있습니다."),
        ("app-workspace/main", "배포 설정 정리", Status.INACTIVE,
         "개발 환경과 배포 환경 설정을 정리해줘.", "환경별 설정을 분리했습니다.\n다음 작업은 배포 전 최종 확인입니다."),
        ("notes-workspace/slot-2", "블로그 수집 오류 조사", Status.NEEDS_YOU,
         "블로그 수집이 멈춘 이유를 확인해줘.", "응답 형식이 바뀐 구간을 확인했습니다.\n새 형식으로 수집할지 확인이 필요합니다."),
        ("notes-workspace/main", "최근 로그 확인", Status.INACTIVE,
         "최근 실행 로그를 확인해줘.", "최근 실행에서 오류가 없었습니다.\n다음 실행 때 다시 확인하면 됩니다."),
    ]
    accounts = tuple(Account(ident, ident.upper(), home / directory, folder / f"{ident}.sock") for ident, directory in DEFAULT_ACCOUNTS)
    config = Config(accounts, Path("/bin/echo"), folder / "state", folder / "cache", 3600, 3600, 1)
    sources = {}
    now = int(time.time())
    for index, (account, sample) in enumerate(zip(accounts, samples)):
        relative, title, status, request, answer = sample
        raw = {"type": "active", "activeFlags": ["waitingOnUserInput"] if status == Status.NEEDS_YOU else []} if status in {Status.WORKING, Status.NEEDS_YOU} else {"type": "notLoaded" if status == Status.INACTIVE else "idle"}
        task = Task(ThreadKey(account.id, f"sample-{index}"), title, str(home / "Documents/workspace" / relative), status, now - index * 3600, now, "sample-model", "medium", raw_status=raw, persisted=True)
        sources[account.id] = DemoSource(account, task, request, answer)
    steps = [
        ([], "전체 세션", 2.0),
        (["right"], "→ 작업공간 이동", 1.8),
        (["down"], "↓ 요청과 답변 확인", 1.8),
        (["space"], "Space · 이어갈 작업 선택", 1.2),
        (["up", "space"], "Space · 두 세션 선택", 1.2),
        (["R"], "R · 선택한 세션 확인", 2.2),
        (["escape", "right"], "→ 다음 작업공간", 1.8),
        (["down"], "↓ 다른 세션 확인", 1.8),
        (["left"], "← 이전 작업공간", 1.5),
        (["left", "space", "down", "space", "up"], "전체 세션", 1.8),
    ]
    with patch("pathlib.Path.home", return_value=home):
        app = CboardApp(config, board=Board(config, sources), launcher=DemoLauncher())
        async with app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            assert app._workspace_order == [str(home / "Documents/workspace/app-workspace"), str(home / "Documents/workspace/notes-workspace")]
            frames = []
            for index, (keys, title, duration) in enumerate(steps):
                if keys:
                    await pilot.press(*keys)
                await pilot.pause()
                if index == 5:
                    assert len(app.screen.tasks) == 2
                else:
                    assert app.current_detail is not None
                path = folder / f"frame-{index:02}.svg"
                path.write_text(app.export_screenshot(title="Cboard · " + title))
                png = path.with_suffix(".png")
                subprocess.run(["rsvg-convert", str(path), "-o", str(png)], check=True, capture_output=True)
                frames.append((png.name, duration))
            assert app.workspace_filter == "all" and not app.saved["selected_keys"]
    concat = folder / "frames.txt"
    concat.write_text("".join(f"file '{name}'\nduration {duration}\n" for name, duration in frames) + f"file '{frames[-1][0]}'\n")
    return concat


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/screenshots/cboard-demo.gif")
    args = parser.parse_args()
    for command in ("rsvg-convert", "ffmpeg"):
        if not shutil.which(command):
            parser.error(f"{command} is required; install with brew install ffmpeg librsvg")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cboard-gif-") as directory:
        concat = await record(Path(directory))
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-filter_complex", "scale=1280:-1:flags=lanczos,split[frames][palette];[palette]palettegen=stats_mode=diff[colors];[frames][colors]paletteuse=dither=bayer:bayer_scale=5",
            "-fps_mode", "vfr", "-loop", "0", str(args.output),
        ], check=True, capture_output=True)
    assert args.output.read_bytes()[:6] == b"GIF89a"
    assert 0 < args.output.stat().st_size < 2 * 1024 * 1024
    print(f"Created {args.output} ({args.output.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    asyncio.run(main())
