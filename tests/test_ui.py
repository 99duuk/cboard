from cboard.services import Board
from cboard.state import StateStore
from cboard.ui.app import CboardApp
from textual.widgets import DataTable, Select
from conftest import FakeSource, task
import pytest


async def test_slow_detail_does_not_replace_new_selection(config):
    source = FakeSource([task(config, "one"), task(config, "two")])
    source.delay["one"] = 0.2
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#tasks", DataTable)
        row = app._row_keys.index("a/two")
        table.move_cursor(row=row)
        await pilot.pause(0.4)
        assert app.current_detail.task.key.thread_id == "two"
        assert app.current_detail.last_assistant == "답변 two"
    assert source.closed


async def test_selection_hide_restart_and_compact_detail(config):
    source = FakeSource([task(config, "one"), task(config, "two")])
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert len(app.saved["selected_keys"]) == 1
        await pilot.press("h")
        assert len(app.saved["hidden_keys"]) == 1
        assert app.saved["selected_keys"] == []
        await pilot.press("d")
        await pilot.pause()
        assert app.has_class("detail-only")
        assert app.query_one("#detail").display
        assert not app.query_one("#tasks").display
        await pilot.press("escape")
        await pilot.pause()
        assert not app.has_class("detail-only")
        assert app.query_one("#tasks").display
        assert not app.query_one("#detail").display
        app.query_one("#status", Select).value = "Inactive"
        await pilot.pause()
        assert app._row_keys == []
    restored = StateStore(config.state_dir).read()
    assert len(restored["hidden_keys"]) == 1
    assert restored["filters"]["status"] == "Inactive"


async def test_modal_refresh_keeps_selection_outside_filter(config):
    source = FakeSource([task(config, "one"), task(config, "two")])
    store = StateStore(config.state_dir)
    store.select("a/one", True)
    store.select("a/two", True)
    app = CboardApp(config, board=Board(config, {"a": source}), store=store)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        app.query_one("#search").value = "one"
        await pilot.pause()
        app.action_resume_selected()
        await pilot.pause()
        assert len(app.screen.tasks) == 2
        app.poll(full=True)
        await pilot.pause()
        await pilot.press("escape")
        assert app._current_key() == "a/one"


async def test_partial_resume_keeps_success_and_reports_failure(config):
    source = FakeSource([task(config, "one"), task(config, "two")])
    app = CboardApp(config, board=Board(config, {"a": source}))
    calls, notices = [], []
    class Launcher:
        async def open(self, spec):
            calls.append(spec.key.thread_id)
            if spec.key.thread_id == "two":
                raise RuntimeError("test launch failure")
            return "opened:owned-test-tab"
    app.launcher = Launcher()
    app.notify = lambda message, **kwargs: notices.append(str(message))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        worker = app.launch_tasks(["a/one", "a/two"], "open")
        await worker.wait()
        assert calls == ["one", "two"]
        assert any("대화 연결 확인" in n for n in notices)
        assert any("test launch failure" in n for n in notices)
        assert not app._launch_busy


async def test_terminal_failure_stops_batch_and_keeps_copy_available(config, monkeypatch):
    import asyncio
    from cboard.terminals.command import TerminalUnavailable, resume_spec
    from textual.widgets import Button

    tasks = [task(config, key) for key in ["one", "two", "three"]]
    source = FakeSource(tasks)
    store = StateStore(config.state_dir)
    keys = [t.key.encoded for t in tasks]
    for key in keys:
        store.select(key, True)
    saved_selection = store.read()["selected_keys"]
    calls, notices, copied = [], [], []
    class Launcher:
        async def open(self, spec):
            calls.append(spec.key.thread_id)
            if spec.key.thread_id == "two":
                raise TerminalUnavailable("iTerm2 응답 없음 · 명령 복사를 사용하세요")
            return "opened:test-session"
    class Clipboard:
        returncode = 0
        async def communicate(self, data):
            copied.append(data.decode())
            return b"", b""
    async def clipboard(*argv, **kwargs):
        assert argv == ("/usr/bin/pbcopy",)
        return Clipboard()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", clipboard)
    app = CboardApp(config, board=Board(config, {"a": source}), store=store, launcher=Launcher())
    app.notify = lambda message, **kwargs: notices.append(str(message))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await app.launch_tasks(keys, "open").wait()
        assert calls == ["one", "two"]
        assert any("대화 연결 확인" in notice for notice in notices)
        assert any("나머지 항목의 자동 열기도 중단" in notice for notice in notices)
        assert store.read()["selected_keys"] == saved_selection
        assert not app._launch_busy
        await app.launch_tasks(keys, "open").wait()
        assert calls == ["one", "two"]
        app.action_resume_selected()
        await pilot.pause()
        assert app.screen.query_one("#open", Button).disabled
        assert not app.screen.query_one("#copy", Button).disabled
        await pilot.click("#copy")
        await pilot.pause()
        await app.workers.wait_for_complete()
        by_key = {t.key.encoded: t for t in tasks}
        assert copied == ["\n".join(resume_spec(config, by_key[key]).command for key in saved_selection) + "\n"]
        assert store.read()["selected_keys"] == saved_selection


async def test_two_thousand_tasks_remain_searchable(config):
    import time
    source = FakeSource([task(config, str(i)) for i in range(2000)])
    app = CboardApp(config, board=Board(config, {"a": source}))
    start = time.monotonic()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        assert len(app.board.tasks) == 2000
        app.query_one("#search").value = "작업 1999"
        await pilot.pause()
        assert app._current_key() == "a/1999"
        assert time.monotonic() - start < 5


async def test_hiding_current_row_preserves_other_restore_selections(config):
    source = FakeSource([task(config, "one"), task(config, "two")])
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        table = app.query_one("#tasks", DataTable)
        table.move_cursor(row=app._row_keys.index("a/one"))
        await pilot.press("space")
        table.move_cursor(row=app._row_keys.index("a/two"))
        await pilot.press("h")
        assert app.saved["selected_keys"] == ["a/one"]
        assert app.saved["hidden_keys"] == ["a/two"]


async def test_quit_cancels_background_refresh_cleanly(config):
    source = FakeSource([task(config, "one")])
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.poll(full=True)
        await pilot.press("q")
    assert source.closed


async def test_recent_rows_across_accounts_projects_and_refresh(config):
    import time
    from dataclasses import replace
    from cboard.models import Account, Status
    from textual.widgets import Static

    now = int(time.time())
    account_b = Account("b", "Account B", config.accounts[0].home.parent / "b", config.accounts[0].socket_path.parent / "b.sock")
    cfg = replace(config, accounts=(*config.accounts, account_b))
    source_a = FakeSource([
        replace(task(cfg, "old"), updated_at=now - 320 * 86400, status=Status.NEEDS_YOU),
        replace(task(cfg, "day"), updated_at=now - 86400, status=Status.READY),
    ])
    source_b = FakeSource([
        replace(task(cfg, "recent", account_id="b"), cwd="/project-b", updated_at=now - 3600, status=Status.INACTIVE),
        replace(task(cfg, "between", account_id="b"), cwd="/project-b", updated_at=now - 2 * 86400, status=Status.WORKING),
    ])
    app = CboardApp(cfg, board=Board(cfg, {"a": source_a, "b": source_b}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        assert app.grouping == "recent"
        assert app._row_keys == ["b/recent", "a/day", "b/between", "a/old"]
        table = app.query_one("#tasks", DataTable)
        assert [table.get_row(key)[-1] for key in app._row_keys] == ["1h ago", "1d ago", "2d ago", "320d ago"]
        table.move_cursor(row=app._row_keys.index("a/day"))
        source_a.tasks[0] = replace(source_a.tasks[0], updated_at=now - 60)
        await app.poll(full=True).wait()
        assert app._row_keys == ["a/old", "b/recent", "a/day", "b/between"]
        assert app._current_key() == "a/day"
        for mode in ["project", "account"]:
            await pilot.press("g")
            await pilot.pause()
            assert app.grouping == mode
            assert any(key.startswith("group:") for key in app._row_keys)
            timestamps = []
            newest = []
            for key in app._row_keys:
                if key.startswith("group:"):
                    assert timestamps == sorted(timestamps, reverse=True)
                    timestamps = []
                else:
                    timestamp = app.board.tasks[key].updated_at
                    if not timestamps:
                        newest.append(timestamp)
                    timestamps.append(timestamp)
            assert timestamps == sorted(timestamps, reverse=True)
            assert newest == sorted(newest, reverse=True)
        await pilot.press("g")
        await pilot.pause()
        assert app._row_keys == ["a/old", "b/recent", "a/day", "b/between"]
        assert StateStore(cfg.state_dir).read()["grouping"] == "recent"
        assert "전체 최근순" in str(app.query_one("#counts", Static).render())


@pytest.mark.parametrize("size", [(120, 35), (80, 24)])
async def test_delete_arrow_confirmation_and_preserves_other_selections(config, size):
    from dataclasses import replace
    from cboard.deletion import DeletePlan
    from cboard.models import Status
    from textual.widgets import Button, Input

    root = replace(task(config, "one"), status=Status.INACTIVE, raw_status={"type": "notLoaded"})
    child = replace(task(config, "child"), parent_thread_id="one", status=Status.INACTIVE, raw_status={"type": "notLoaded"})
    source = FakeSource([root, child, task(config, "two")])
    store = StateStore(config.state_dir)
    for key in ["a/one", "a/two"]:
        store.select(key, True)
    store.hide({"a/child"}, True)
    calls, prepares = [], []
    class Deleter:
        async def prepare(self, key):
            assert key == root.key
            prepares.append(key)
            return DeletePlan(root, (child,))
        async def delete(self, plan):
            calls.append(plan.keys)
            source.tasks = [t for t in source.tasks if t.key.encoded not in plan.keys]

    app = CboardApp(config, board=Board(config, {"a": source}), store=store, deleter=Deleter())
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        app.query_one("#tasks", DataTable).move_cursor(row=app._row_keys.index("a/one"))
        await pilot.press("delete")
        await pilot.pause()
        assert not app.screen.query(Input)
        assert not app.screen.query_one("#delete-confirm-button", Button).disabled
        assert app.screen.focused.id == "delete-cancel"
        assert not calls
        dialog = app.screen
        await pilot.press("delete")
        await pilot.pause()
        assert app.screen is dialog
        assert len(prepares) == 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is app.default_screen
        assert not calls
        await pilot.press("delete")
        await pilot.pause()
        await pilot.press("escape")
        assert not calls
        await pilot.press("delete")
        await pilot.pause()
        for key, expected in [("left", "delete-confirm-button"), ("right", "delete-cancel"), ("up", "delete-confirm-button"), ("down", "delete-cancel"), ("left", "delete-confirm-button")]:
            await pilot.press(key)
            assert app.screen.focused.id == expected
            assert not calls
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == [{"a/one", "a/child"}]
        assert store.read()["selected_keys"] == ["a/two"]
        assert store.read()["hidden_keys"] == []
        assert app._row_keys == ["a/two"]
        assert not app._delete_busy


async def test_delete_failure_keeps_selection_and_does_not_retry(config):
    from cboard.deletion import DeletePlan

    root = task(config)
    store = StateStore(config.state_dir)
    store.select(root.key.encoded, True)
    calls, notices = [], []
    class Deleter:
        async def prepare(self, key):
            return DeletePlan(root)
        async def delete(self, plan):
            calls.append(plan)
            raise RuntimeError("삭제 결과 불명")

    app = CboardApp(config, board=Board(config, {"a": FakeSource([root])}), store=store, deleter=Deleter())
    app.notify = lambda message, **kwargs: notices.append(str(message))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("delete")
        await pilot.pause()
        await pilot.click("#delete-confirm-button")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(calls) == 1
        assert any("삭제 결과 불명" in notice for notice in notices)
        assert store.read()["selected_keys"] == [root.key.encoded]
        assert not app._delete_busy


async def test_task_columns_fit_without_horizontal_scroll_after_resize(config):
    from dataclasses import replace
    source = FakeSource([replace(task(config, str(i)), title="매우 긴 한글 세션명 " * 40) for i in range(100)])
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(80, 24)) as pilot:
        for size in [(80, 24), (100, 30), (120, 35), (160, 45), (80, 24)]:
            await pilot.resize_terminal(*size)
            await pilot.pause()
            table = app.query_one("#tasks", DataTable)
            assert not table.show_horizontal_scrollbar
            assert table.max_scroll_x == 0
            assert table.virtual_size.width <= table.scrollable_content_region.width
            assert len(table.columns) == 4
        app.action_group()
        await pilot.pause()
        table = app.query_one("#tasks", DataTable)
        assert table.max_scroll_x == 0


@pytest.mark.parametrize("size", [(120, 35), (80, 24)])
@pytest.mark.parametrize("action", ["open", "copy", "cancel"])
async def test_resume_dialog_arrow_navigation_and_enter(config, monkeypatch, size, action):
    import asyncio
    calls, copied = [], []
    class Launcher:
        async def open(self, spec):
            calls.append(spec.key.encoded)
            return "opened:test-tab"
    class Clipboard:
        returncode = 0
        async def communicate(self, data):
            copied.append(data)
            return b"", b""
    async def clipboard(*args, **kwargs):
        assert args == ("/usr/bin/pbcopy",)
        return Clipboard()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", clipboard)
    app = CboardApp(config, board=Board(config, {"a": FakeSource([task(config)])}), launcher=Launcher())
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.focused.id == "open"
        for key, expected in [("left", "cancel"), ("up", "copy"), ("right", "cancel"), ("down", "open")]:
            await pilot.press(key)
            assert app.screen.focused.id == expected
        if action == "copy":
            await pilot.press("right")
        elif action == "cancel":
            await pilot.press("right", "down")
        assert app.screen.focused.id == action
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == (["a/one"] if action == "open" else [])
        assert len(copied) == (1 if action == "copy" else 0)


async def test_resume_dialog_skips_disabled_buttons(config):
    app = CboardApp(config, board=Board(config, {"a": FakeSource([task(config)])}))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.focused.id == "copy"
        await pilot.press("left")
        assert app.screen.focused.id == "cancel"
        await pilot.press("right")
        assert app.screen.focused.id == "copy"
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.screen is app.default_screen


async def test_keyboard_rename_refreshes_list_and_detail_without_timestamp_change(config):
    from dataclasses import replace
    from textual.widgets import Input
    source = FakeSource([task(config)])
    store = StateStore(config.state_dir)
    store.select("a/one", True)
    calls = []
    class Renamer:
        async def rename(self, key, name):
            calls.append((key.encoded, name))
            source.tasks[0] = replace(source.tasks[0], title=name)
            return source.tasks[0]
    app = CboardApp(config, board=Board(config, {"a": source}), store=store, renamer=Renamer())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        old_timestamp = app.board.tasks["a/one"].updated_at
        await pilot.press("f2")
        await pilot.pause()
        assert app.screen.focused.id == "rename-name"
        await pilot.press("escape")
        assert calls == []
        await pilot.press("f2")
        await pilot.pause()
        app.screen.query_one("#rename-name", Input).value = "  "
        await pilot.press("enter")
        await pilot.pause()
        assert calls == []
        assert app.screen.focused.id == "rename-name"
        app.screen.query_one("#rename-name", Input).value = "새로운 세션 이름 🚀"
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == [("a/one", "새로운 세션 이름 🚀")]
        assert app.board.tasks["a/one"].updated_at == old_timestamp
        assert app.current_detail.task.title == "새로운 세션 이름 🚀"
        assert "새로운 세션 이름" in str(app.query_one("#tasks", DataTable).get_row("a/one")[0])
        assert store.read()["selected_keys"] == ["a/one"]
        assert not app._rename_busy


async def test_rename_failure_preserves_title_and_selection_without_retry(config):
    from textual.widgets import Input
    calls, notices = [], []
    source = FakeSource([task(config)])
    store = StateStore(config.state_dir)
    store.select("a/one", True)
    class Renamer:
        async def rename(self, key, name):
            calls.append((key.encoded, name))
            raise RuntimeError("이름 변경 결과 불명")
    app = CboardApp(config, board=Board(config, {"a": source}), store=store, renamer=Renamer())
    app.notify = lambda message, **kwargs: notices.append(str(message))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        app.screen.query_one("#rename-name", Input).value = "새 이름"
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == [("a/one", "새 이름")]
        assert app.board.tasks["a/one"].title == "작업 one"
        assert store.read()["selected_keys"] == ["a/one"]
        assert any("이름 변경 결과 불명" in notice for notice in notices)
        assert not app._rename_busy


async def test_unmaterialized_session_cannot_open_or_copy_a_broken_resume(config):
    from dataclasses import replace
    from textual.widgets import Button, Static
    root = replace(task(config), persisted=False)
    app = CboardApp(config, board=Board(config, {"a": FakeSource([root])}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        row = app.query_one("#tasks", DataTable).get_row("a/one")
        assert "미저장" in str(row[2])
        assert "아직 저장 안 됨" in str(app.query_one("#task-info", Static).render())
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#open", Button).disabled
        assert app.screen.query_one("#copy", Button).disabled
        assert app.screen.focused.id == "cancel"
        await pilot.press("right", "enter")
        await pilot.pause()
        assert app.screen is app.default_screen


async def test_unchanged_poll_keeps_rows_cursor_scroll_and_refreshes_detail_metadata(config, monkeypatch):
    import time
    from dataclasses import replace
    from textual.widgets import Markdown, Static
    from cboard.models import Status
    now = time.time() - 1234
    source = FakeSource([replace(task(config, str(i)), updated_at=now) for i in range(100)])
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        table = app.query_one("#tasks", DataTable)
        table.move_cursor(row=50, animate=False)
        await pilot.pause()
        key, scroll = app._current_key(), table.scroll_y
        clears = []
        original_clear = table.clear
        def clear(*args, **kwargs):
            clears.append(True)
            return original_clear(*args, **kwargs)
        monkeypatch.setattr(table, "clear", clear)
        source.tasks = [replace(t, observed_at=t.observed_at + 1) for t in source.tasks]
        await app.poll(full=False).wait()
        await pilot.pause()
        assert not clears
        assert app._current_key() == key
        assert table.scroll_y == scroll
        index = next(i for i, t in enumerate(source.tasks) if t.key.encoded == key)
        source.tasks[index] = replace(source.tasks[index], cwd="/new/project", model="new-model")
        await app.poll(full=False).wait()
        await pilot.pause()
        assert not clears  # These fields belong to the detail panel.
        assert app.current_detail.task.cwd == "/new/project"
        assert "new-model" in str(app.query_one("#task-info", Static).render())
        body = app.query_one("#messages", Markdown)._markdown
        assert body.index("## 마지막 요청") < body.index("## 마지막 답변")
        assert "요청 " in body and "답변 " in body
        source.tasks[index] = replace(source.tasks[index], title="changed", status=Status.WORKING)
        await app.poll(full=False).wait()
        await pilot.pause()
        assert len(clears) == 1
        assert app._current_key() == key
        assert "changed" in str(table.get_row(key)[0])
        assert "Working" in str(table.get_row(key)[2])


def recent_workspace_tasks(config):
    import time
    from dataclasses import replace
    from pathlib import Path
    now = time.time() - 60
    parent = config.accounts[0].home.parent
    bottling, memo = parent / "bottling-workspace", parent / "mnemosyne-workspace"
    records = [
        ("bot-1", bottling / "slot-1", now),
        ("bot-3", bottling / "slot-3/frontend", now - 60),
        ("bot-main", bottling / "main", now - 120),
        ("bot-old", bottling / "slot-4", now - 15 * 86400),
        ("memo-1", memo / "slot-1", now - 180),
        ("memo-2", memo / "main", now - 240),
        ("home", Path.home(), now - 300),
        ("desktop", Path.home() / "Desktop/temp", now - 360),
        ("downloads", Path.home() / "Downloads/test", now - 420),
    ]
    return [replace(task(config, key), cwd=str(cwd), updated_at=updated) for key, cwd, updated in records], str(bottling), str(memo)


@pytest.mark.parametrize("size", [(120, 35), (80, 24)])
async def test_workspace_arrows_dropdown_relative_paths_and_restart(config, size):
    from pathlib import Path
    tasks, bottling, memo = recent_workspace_tasks(config)
    store = StateStore(config.state_dir)
    store.select("a/bot-old", True)
    source = FakeSource(tasks)
    app = CboardApp(config, board=Board(config, {"a": source}), store=store)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app._workspace_order[:2] == [bottling, memo]
        assert "a/bot-old" in app._row_keys
        await pilot.press("right")
        await pilot.pause()
        assert app.workspace_filter == bottling
        assert app.query_one("#workspace", Select).value == bottling
        from textual.widgets import Static
        summary = app.query_one("#workspace-current", Static)
        assert summary.render().plain.splitlines()[0] == "▶ bottling-workspace"
        assert "1/5 · 최근 14일 3개" in summary.render().plain
        assert bottling in summary.tooltip
        assert summary.size.width > 30
        assert app._row_keys == ["a/bot-1", "a/bot-3", "a/bot-main"]
        assert "작업공간 내 최근순" in str(app.query_one("#counts", Static).render())
        table = app.query_one("#tasks", DataTable)
        assert "[slot-3/frontend]" in str(table.get_row("a/bot-3")[0])
        assert table.max_scroll_x == 0
        await pilot.press("right")
        assert app.workspace_filter == memo
        assert summary.render().plain.splitlines()[0] == "▶ mnemosyne-workspace"
        assert "2/5 · 최근 14일 2개" in summary.render().plain
        assert app._row_keys == ["a/memo-1", "a/memo-2"]
        await pilot.press("left", "left")
        assert app.workspace_filter == "all"
        assert summary.render().plain.splitlines()[0] == "▶ 전체 작업공간"
        assert "a/bot-old" in app._row_keys
        await pilot.press("left")
        assert app.workspace_filter == app._workspace_order[-1]
        await pilot.press("right")
        assert app.workspace_filter == "all"
        # Exercise the actual dropdown keyboard path, independently of task arrows.
        await pilot.click("#workspace")
        await pilot.press("home", "down", "enter")
        await pilot.pause()
        assert app.workspace_filter == bottling
        for root, key in [(str(Path.home()), "a/home"), (str(Path.home() / "Desktop"), "a/desktop"), (str(Path.home() / "Downloads"), "a/downloads")]:
            app.query_one("#workspace", Select).value = root
            await pilot.pause()
            assert app._row_keys == [key]
        assert store.read()["selected_keys"] == ["a/bot-old"]
    restarted = CboardApp(config, board=Board(config, {"a": FakeSource(tasks)}))
    async with restarted.run_test(size=size) as pilot:
        await pilot.pause()
        assert restarted.workspace_filter == str(Path.home() / "Downloads")
        assert restarted._row_keys == ["a/downloads"]


async def test_workspace_rank_stays_stable_until_manual_refresh_and_keeps_selection(config):
    import time
    from dataclasses import replace
    from textual.widgets import Static
    tasks, bottling, memo = recent_workspace_tasks(config)
    source = FakeSource(tasks)
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("right")
        source.tasks += [replace(task(config, f"memo-new-{i}"), cwd=memo + "/slot-4", updated_at=time.time()) for i in range(3)]
        await app.poll(full=True).wait()
        await pilot.pause()
        assert app._workspace_order[:2] == [bottling, memo]
        app.action_refresh_now()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._workspace_order[:2] == [memo, bottling]
        assert app.workspace_filter == bottling
        assert app.query_one("#workspace", Select).value == bottling
        # A selected group reaching zero stays selected; it must not reveal another group.
        source.tasks = [replace(t, updated_at=time.time() - 15 * 86400) if t.cwd.startswith(bottling) else t for t in source.tasks]
        await app.poll(full=False).wait()
        await pilot.pause()
        assert app.workspace_filter == bottling
        assert not app._row_keys
        assert "최근 14일 0개" in str(app._workspace_options)
        assert "▶ bottling-workspace" in app.query_one("#workspace-current", Static).render().plain
        assert "최근 14일 0개" in app.query_one("#workspace-current", Static).render().plain
        assert "표시할 작업이 없습니다" in str(app.query_one("#task-info", Static).render())


async def test_workspace_navigation_does_not_take_search_or_dialog_arrows(config):
    from textual.widgets import Input
    tasks, bottling, memo = recent_workspace_tasks(config)
    app = CboardApp(config, board=Board(config, {"a": FakeSource(tasks)}))
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        search = app.query_one("#search", Input)
        search.focus()
        search.value = "bot"
        await pilot.pause()
        search.cursor_position = 2
        await pilot.press("left")
        assert search.cursor_position == 1
        assert app.workspace_filter == "all"
        app.query_one("#tasks", DataTable).focus()
        await pilot.press("right")
        assert app.workspace_filter == bottling
        assert app.query == "bot"
        await pilot.press("g")
        await pilot.pause()
        # Legacy project view cannot split the chosen workspace back into slots.
        assert [key for key in app._row_keys if key.startswith("group:")] == ["group:" + bottling]
        app.action_resume_selected()  # No selection simply shows a notification.
        app._resume_dialog(["a/bot-1"])
        await pilot.pause()
        await pilot.press("right")
        assert app.workspace_filter == bottling
        assert app.screen.focused.id == "cancel"
        app.action_refresh_now()
        await app.workers.wait_for_complete()
        assert app.screen.focused.id == "cancel"
        await pilot.press("escape")


async def test_workspace_directory_names_are_literal_text(config):
    import time
    from dataclasses import replace
    from rich.text import Text
    root = str(config.accounts[0].home.parent / "[not-a-style]-workspace")
    source = FakeSource([replace(task(config), cwd=root + "/slot-1", updated_at=time.time())])
    app = CboardApp(config, board=Board(config, {"a": source}))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.workspace_filter == root
        label = app._workspace_options[1][0]
        assert isinstance(label, Text)
        assert "[not-a-style]-workspace" in label.plain
