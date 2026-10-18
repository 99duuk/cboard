from __future__ import annotations

import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import TypeVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Markdown, Select, Static

from ..config import Config
from ..deletion import DeletePlan, SessionDeleter
from ..models import Detail, Status, Task, plain
from ..renaming import SessionRenamer, validate_name
from ..services import Board
from ..state import StateStore
from ..terminals.command import TerminalLauncher, TerminalUnavailable, resume_spec
from ..workspaces import UNASSIGNED, workspace_label, workspace_name, workspace_path, workspace_relative, workspace_root

COLORS = ["#67d5ef", "#f3b569", "#ca9bea", "#83d7a2", "#ec8597"]
STATUS_COLORS = {Status.NEEDS_YOU: "#f3cd73", Status.WORKING: "#83d7a2", Status.READY: "#67d5ef", Status.INACTIVE: "#8491a8", Status.ERROR: "#ec8597", Status.UNKNOWN: "#ca9bea"}


def age(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    for unit, factor in [("d", 86400), ("h", 3600), ("m", 60)]:
        if seconds >= factor:
            return f"{int(seconds / factor)}{unit} ago"
    return "now"


class TasksTable(DataTable):
    BINDINGS = [
        Binding("left", "app.move_workspace(-1)", "이전 작업공간", show=False),
        Binding("right", "app.move_workspace(1)", "다음 작업공간", show=False),
    ]

    class ViewportChanged(Message):
        pass

    def on_resize(self):
        self.post_message(self.ViewportChanged())


DialogResult = TypeVar("DialogResult")


class ButtonDialog(ModalScreen[DialogResult]):
    BINDINGS = [
        Binding("left,up", "move_button(-1)", "이전 버튼", show=False, priority=True),
        Binding("right,down", "move_button(1)", "다음 버튼", show=False, priority=True),
        Binding("enter", "activate_button", "실행", show=False, priority=True),
    ]

    def _enabled_buttons(self):
        return [button for button in self.query(Button) if not button.disabled]

    def action_move_button(self, direction: int):
        buttons = self._enabled_buttons()
        index = buttons.index(self.focused) if self.focused in buttons else 0
        buttons[(index + direction) % len(buttons)].focus()

    def action_activate_button(self):
        if isinstance(self.focused, Button) and not self.focused.disabled:
            self.focused.press()


class ResumeScreen(ButtonDialog[str]):
    BINDINGS = [Binding("escape", "cancel", "취소")]

    def __init__(self, tasks: list[Task], missing: list[str], allow_open: bool = True, open_error: str | None = None):
        super().__init__()
        self.tasks = tasks
        self.missing = missing
        self.allow_open = allow_open
        self.open_error = open_error
        self.has_resumable = any(not task.ephemeral and task.persisted is not False for task in tasks)

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label(f"선택한 대화 {len(self.tasks)}개", id="resume-title")
            with VerticalScroll(id="resume-list"):
                for task in self.tasks:
                    storage = " · 아직 저장 안 됨 / 재개 불가" if task.persisted is False else ""
                    yield Static(Text(f"{task.key.account_id.upper()}  ·  {task.title}\n{task.cwd}\n{task.status.value}{storage}", overflow="fold"))
                for key in self.missing:
                    yield Static(Text(f"조회할 수 없는 선택: {key}", style="yellow"))
            yield Static(self.open_error or "항상 새 iTerm2 탭에서 해당 대화를 엽니다.", id="resume-note")
            with Horizontal(id="resume-buttons"):
                yield Button("터미널에서 열기", id="open", variant="primary", disabled=not self.has_resumable or not self.allow_open)
                yield Button("명령 복사", id="copy", disabled=not self.has_resumable)
                yield Button("취소", id="cancel")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self):
        self.dismiss("cancel")

    def on_mount(self):
        self._enabled_buttons()[0].focus()


class RenameScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "취소")]

    def __init__(self, task: Task, account_label: str):
        super().__init__()
        self.target_task = task
        self.account_label = account_label

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Label("세션 이름 변경", id="rename-title")
            yield Static(Text(f"{self.account_label} · {self.target_task.key.encoded}"))
            yield Input(value=self.target_task.title, placeholder="새 세션 이름", id="rename-name", select_on_focus=True)
            yield Static("Codex 원본 대화의 이름을 변경합니다. Enter 저장 · Esc 취소", id="rename-note")
            with Horizontal(id="rename-buttons"):
                yield Button("저장", id="rename-save", variant="primary")
                yield Button("취소", id="rename-cancel")

    def on_mount(self):
        self.query_one("#rename-name", Input).focus()

    def _save(self):
        try:
            name = validate_name(self.query_one("#rename-name", Input).value)
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            return
        self.dismiss(name)

    @on(Input.Submitted, "#rename-name")
    def submitted(self):
        self._save()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        if event.button.id == "rename-save":
            self._save()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class DeleteScreen(ButtonDialog[bool]):
    BINDINGS = [Binding("escape", "cancel", "취소")]

    def __init__(self, plan: DeletePlan, account_label: str):
        super().__init__()
        self.plan = plan
        self.account_label = account_label

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-dialog"):
            yield Label("대화 영구 삭제", id="delete-title")
            with VerticalScroll(id="delete-description"):
                yield Static(Text(f"{self.account_label} · {self.plan.task.title}\n{self.plan.task.cwd}\n{self.plan.task.key.encoded}"))
                yield Static(f"이 대화와 하위 에이전트 대화 {len(self.plan.descendants)}개를 영구 삭제합니다.\n복구할 수 없습니다. 프로젝트 파일은 삭제하지 않습니다.", id="delete-warning")
                yield Static("화살표 선택 · Enter 실행 · Esc 취소", id="delete-note")
            with Horizontal(id="delete-buttons"):
                yield Button("영구 삭제", id="delete-confirm-button", variant="error")
                yield Button("취소", id="delete-cancel")

    def on_mount(self):
        self.query_one("#delete-cancel", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "delete-confirm-button")

    def action_cancel(self):
        self.dismiss(False)


class CboardApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "Cboard"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "종료"), Binding("d", "detail", "상세"), Binding("space", "select_task", "선택"),
        Binding("R", "resume_selected", "선택 재개"), Binding("h", "hide_task", "숨기기"),
        Binding("H", "show_hidden", "숨김 보기"), Binding("slash", "search", "검색"),
        Binding("g", "group", "그룹"), Binding("r", "refresh_now", "새로고침"),
        Binding("delete", "delete_task", "대화 삭제", show=False),
        Binding("f2", "rename_task", "이름 변경"),
        Binding("escape", "back", "목록", show=False),
    ]

    def __init__(self, config: Config, *, board: Board | None = None, store: StateStore | None = None, launcher: TerminalLauncher | None = None, deleter: SessionDeleter | None = None, renamer: SessionRenamer | None = None):
        super().__init__()
        self.config = config
        self.board = board or Board(config)
        self.store = store or StateStore(config.state_dir)
        self.saved = self.store.read()
        self.account_filter = self.saved["filters"].get("account", "all")
        if self.account_filter not in {"all", *(a.id for a in config.accounts)}:
            self.account_filter = "all"
        self.status_filter = self.saved["filters"].get("status", "All")
        if self.status_filter not in {"All", *(s.value for s in Status)}:
            self.status_filter = "All"
        self.query = str(self.saved["filters"].get("query", ""))
        workspace = self.saved["filters"].get("workspace", "all")
        self.workspace_filter = workspace if isinstance(workspace, str) and (workspace in {"all", UNASSIGNED} or Path(workspace).is_absolute()) else "all"
        self._workspace_order = []
        self._workspace_options = None
        self._workspace_summary = None
        self._rerank_workspaces = True
        self._reset_cursor = False
        self.grouping = self.saved.get("grouping", "recent")
        if self.grouping not in {"recent", "project", "account"}:
            self.grouping = "recent"
        self.show_hidden = False
        self._detail_key = None
        self._detail_version = None
        self.current_detail: Detail | None = None
        self._detail_generation = 0
        self._detail_pages = 3
        self._row_keys = []
        self._column_widths = None
        self._table_signature = None
        self._counts_text = None
        self._health_text = None
        self._poll_busy = False
        self._pending_full = False
        self._launch_busy = False
        self._delete_busy = False
        self._rename_busy = False
        self._terminal_error: str | None = None
        self._board_closing = False
        self.launcher = launcher
        self.deleter = deleter
        self.renamer = renamer

    def compose(self) -> ComposeResult:
        yield Static("CBOARD  /  Agent command center", id="title")
        with Horizontal(id="controls"):
            yield Select([("전체 계정", "all"), *((a.label, a.id) for a in self.config.accounts)], value=self.account_filter, allow_blank=False, id="account")
            yield Select([(s, s) for s in ["All", *(status.value for status in Status)]], value=self.status_filter, allow_blank=False, id="status")
            yield Input(value=self.query, placeholder="작업명 · 프로젝트 검색", id="search")
        with Horizontal(id="workspace-controls"):
            yield Static("작업공간\n← / →", id="workspace-hint")
            yield Static("", id="workspace-current")
            options = [("전체 · 모든 기간", "all")]
            if self.workspace_filter != "all":
                options.append((Text(workspace_label(self.workspace_filter) + " · 최근 14일"), self.workspace_filter))
            yield Select(options, value=self.workspace_filter, allow_blank=False, id="workspace")
        yield Static("계정에 연결하는 중…", id="counts")
        with Horizontal(id="body"):
            yield TasksTable(id="tasks", cursor_type="row", zebra_stripes=False)
            with VerticalScroll(id="detail"):
                yield Static("작업을 선택하세요", id="task-info")
                yield Markdown("", id="messages", open_links=True)
                yield Button("이전 메시지 더 보기", id="more")
        yield Static("", id="health")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#tasks", DataTable)
        table.focus()
        self.query_one("#more").display = False
        if self.store.warning:
            self.notify(self.store.warning, severity="warning", timeout=10)
        self._render_tasks()
        self.poll(full=True)
        self.set_interval(self.config.loaded_interval, self.poll)
        self.set_interval(self.config.list_interval, lambda: self.poll(full=True))

    async def on_unmount(self):
        self._board_closing = True
        self._detail_generation += 1
        self.workers.cancel_all()
        await self.board.close()

    @work(group="refresh")
    async def poll(self, full: bool = False):
        if self._board_closing:
            return
        if self._poll_busy or self._delete_busy or self._rename_busy:
            self._pending_full |= full
            return
        self._poll_busy = True
        try:
            await self.board.refresh(loaded=not full, on_update=self._render_tasks)
            if full and self._rerank_workspaces:
                self._sync_workspaces(reorder=True)
                self._rerank_workspaces = False
                self._render_tasks()
        finally:
            self._poll_busy = False
        if self._pending_full:
            self._pending_full = False
            self.poll(full=True)

    def _persist(self):
        self.saved = self.store.preferences({"account": self.account_filter, "status": self.status_filter, "query": self.query, "workspace": self.workspace_filter}, self.grouping)

    def _sync_workspaces(self, *, reorder: bool = False):
        counts = self.board.workspace_counts(self.saved, show_hidden=self.show_hidden)
        ranked = sorted(counts, key=lambda root: (-counts[root], root))
        if reorder:
            self._workspace_order = ranked
        else:
            self._workspace_order = [root for root in self._workspace_order if root in counts or root == self.workspace_filter]
            known = set(self._workspace_order)
            self._workspace_order.extend(root for root in ranked if root not in known)
        if self.workspace_filter != "all" and self.workspace_filter not in self._workspace_order:
            self._workspace_order.append(self.workspace_filter)
        options = [("전체 · 모든 기간", "all"), *((Text(f"{workspace_label(root)} · 최근 14일 {counts[root]}개"), root) for root in self._workspace_order)]
        selector = self.default_screen.query_one("#workspace", Select)
        if options != self._workspace_options or selector.value != self.workspace_filter:
            with selector.prevent(Select.Changed):
                selector.set_options(options)
                selector.value = self.workspace_filter
            selector.tooltip = "최근 14일 활동한 전체 계정의 세션 수 순 · r로 순위 갱신\n" + ("전체 기간" if self.workspace_filter == "all" else self.workspace_filter)
            self._workspace_options = options
        summary = Text(no_wrap=True, overflow="ellipsis")
        if self.workspace_filter == "all":
            summary.append("▶ 전체 작업공간", style="bold #67d5ef")
            summary.append(f"\n{len(self._workspace_order)}개 공간 · 모든 기간", style="#e4eaf3")
            summary.append("\n←/→ 전환 · r 순위 갱신", style="#afbed1")
        else:
            root = self.workspace_filter
            summary.append("▶ " + workspace_name(root), style="bold #67d5ef")
            summary.append(f"\n{self._workspace_order.index(root) + 1}/{len(self._workspace_order)} · 최근 14일 {counts[root]}개", style="#e4eaf3")
            summary.append("\n" + workspace_path(root), style="#afbed1")
        if summary != self._workspace_summary:
            current = self.default_screen.query_one("#workspace-current", Static)
            current.update(summary)
            current.tooltip = selector.tooltip
            self._workspace_summary = summary

    def _set_workspace(self, root: str):
        if root == self.workspace_filter:
            return
        self.workspace_filter = root
        self._reset_cursor = True
        self._persist()
        self._render_tasks()

    def action_move_workspace(self, direction: int):
        if self.screen is not self.default_screen or not isinstance(self.focused, TasksTable):
            return
        roots = ["all", *self._workspace_order]
        index = roots.index(self.workspace_filter)
        self._set_workspace(roots[(index + direction) % len(roots)])

    @on(TasksTable.ViewportChanged)
    def task_viewport_changed(self):
        self._render_tasks()

    def _render_tasks(self):
        if self._board_closing:
            return
        try:
            table = self.query_one("#tasks", DataTable)
        except NoMatches:
            return
        if not table.is_mounted:
            return
        self._sync_workspaces()
        old_key = None if self._reset_cursor else self._current_key()
        old_row = 0 if self._reset_cursor else table.cursor_row
        self._reset_cursor = False
        tasks, counts = self.board.visible(self.saved, account=self.account_filter, query=self.query, status=self.status_filter, show_hidden=self.show_hidden, workspace=self.workspace_filter)
        groups = defaultdict(list)
        projects = {}
        for task in tasks:
            if self.grouping == "project":
                if task.cwd not in projects:
                    projects[task.cwd] = task.project if self.workspace_filter == "all" else self.workspace_filter
                group = projects[task.cwd]
            else:
                group = task.key.account_id if self.grouping == "account" else ""
            groups[group].append(task)
        labels = {a.id: a.label for a in self.config.accounts}
        colors = {a.id: COLORS[i % len(COLORS)] for i, a in enumerate(self.config.accounts)}
        selected = set(self.saved["selected_keys"])
        hidden = set(self.saved["hidden_keys"])
        left_width = table.scrollable_content_region.width
        if not left_width:
            left_width = self.size.width - 4 if self.size.width < 100 else int((self.size.width - 2) * 0.58) - 2
        account_width = max(7, min(15, max((len(a.label) for a in self.config.accounts), default=7)))
        widths = (max(1, left_width - account_width - 18 - 9 - 8), account_width, 18, 9)
        ordered_groups = sorted(groups.items(), key=lambda pair: (-max(t.updated_at for t in pair[1]), pair[0]))
        signature = (widths, self.grouping, self.workspace_filter, tuple(
            (group, task.key.encoded, task.title, task.cwd if self.workspace_filter != "all" else "", task.status, task.persisted, age(task.updated_at), task.key.encoded in selected, task.key.encoded in hidden, self.board.snapshots[task.key.account_id].stale)
            for group, group_tasks in ordered_groups for task in group_tasks
        ))
        if signature != self._table_signature:
            if widths != self._column_widths:
                table.clear(columns=True)
                for name, width in zip(["Tasks", "Account", "Status", "Updated"], widths):
                    table.add_column(name, width=width)
                self._column_widths = widths
            else:
                table.clear()
            self._row_keys = []
            for group, group_tasks in ordered_groups:
                if self.grouping != "recent":
                    label = str(group).replace(str(Path.home()), "~", 1) if self.grouping == "project" else labels.get(group, group)
                    group_key = "group:" + group
                    table.add_row(Text(f"{label}  {len(group_tasks)}", style="#8491a8"), "", "", "", key=group_key)
                    self._row_keys.append(group_key)
                for task in group_tasks:
                    key = task.key.encoded
                    mark = "☑" if key in selected else "□"
                    location = f"[{workspace_relative(task.cwd, self.workspace_filter)}] " if self.workspace_filter != "all" else ""
                    title = f" {mark} {location}{task.title}" + ("  [숨김]" if key in hidden else "")
                    snapshot = self.board.snapshots[task.key.account_id]
                    status = task.status.value + (" · stale" if snapshot.stale else "")
                    if task.persisted is False:
                        status += " · 미저장"
                    table.add_row(Text(title, style=colors[task.key.account_id]), Text(labels[task.key.account_id], style=colors[task.key.account_id]), Text(status, style=STATUS_COLORS[task.status]), age(task.updated_at), key=key)
                    self._row_keys.append(key)
            target = self._row_keys.index(old_key) if old_key in self._row_keys else min(old_row, max(0, len(self._row_keys) - 1))
            if self._row_keys and self._row_keys[target].startswith("group:"):
                target = min(target + 1, len(self._row_keys) - 1)
            table.move_cursor(row=target, animate=False)
            self._table_signature = signature
        parts = [f"All {sum(counts.values())}"] + [f"{status.value} {counts[status.value]}" for status in Status]
        parts += [f"선택 {len(selected)}", {"recent": "작업공간 내 최근순 ↓" if self.workspace_filter != "all" else "전체 최근순 ↓", "project": "프로젝트별 그룹 · 그룹 내 최근순 ↓", "account": "계정별 그룹 · 그룹 내 최근순 ↓"}[self.grouping]]
        if self.show_hidden:
            parts.append("숨김 포함")
        if self.workspace_filter != "all":
            parts.append("최근 14일 활동")
        counts_text = "   ·   ".join(parts)
        if counts_text != self._counts_text:
            self.query_one("#counts", Static).update(counts_text)
            self._counts_text = counts_text
        health = []
        for account in self.config.accounts:
            snapshot = self.board.snapshots[account.id]
            if snapshot.error:
                health.append(f"{account.label}: {snapshot.error} · 마지막 확인 {age(snapshot.fetched_at) if snapshot.fetched_at else '없음'}")
            elif not snapshot.complete:
                health.append(f"{account.label}: 불러온 {len(snapshot.tasks)}개 · 조회 중")
            if account.id in self.board.errors:
                health.append(f"{account.label}: {self.board.errors[account.id]}")
        health_text = " / ".join(health) or ("연결 정상 · 계정별 원본 상태를 조회합니다" if self.config.accounts else "등록된 Cboard 계정 없음 · 계정 관리: cnew NAME / clist / cusage · 보드 연결은 config.toml의 accounts 설정 필요")
        if health_text != self._health_text:
            self.query_one("#health", Static).update(Text(health_text, style="#8491a8"))
            self._health_text = health_text
        key = self._current_key()
        if key and key in self.board.tasks:
            task = self.board.tasks[key]
            version = (task.title, task.cwd, task.raw_status, task.persisted, task.updated_at, task.status, task.model, task.reasoning_effort, self.board.snapshots[task.key.account_id].stale)
            if key != self._detail_key or version != self._detail_version:
                self._choose_detail(key)
        elif not key:
            self._detail_generation += 1
            self._detail_key = None
            self.current_detail = None
            self.query_one("#task-info", Static).update("표시할 작업이 없습니다")
            self.query_one("#messages", Markdown).update("")

    def _current_key(self):
        table = self.query_one("#tasks", DataTable)
        if 0 <= table.cursor_row < len(self._row_keys):
            key = self._row_keys[table.cursor_row]
            return key if not key.startswith("group:") else None
        return None

    def _choose_detail(self, key: str):
        task = self.board.tasks.get(key)
        if task is None:
            return
        self._detail_key = key
        self._detail_version = (task.title, task.cwd, task.raw_status, task.persisted, task.updated_at, task.status, task.model, task.reasoning_effort, self.board.snapshots[task.key.account_id].stale)
        self._detail_generation += 1
        self._detail_pages = 3
        self.current_detail = None
        self.query_one("#task-info", Static).update(Text(f"{task.title}\n{task.key.account_id.upper()} · {task.status.value}\n{task.cwd}\n\n최근 대화 불러오는 중…"))
        self.query_one("#messages", Markdown).update("")
        self.query_one("#more").display = False
        self.load_detail(key, self._detail_generation, 3)

    @work(group="detail", exclusive=True)
    async def load_detail(self, key: str, generation: int, pages: int):
        task = self.board.tasks.get(key)
        if task is None:
            return
        try:
            detail = await self.board.sources[task.key.account_id].detail(task.key.thread_id, pages=pages)
            if self._board_closing or generation != self._detail_generation or self._detail_key != key:
                return
            self.current_detail = detail
            self._show_detail(detail)
        except Exception as exc:
            if not self._board_closing and generation == self._detail_generation:
                self.query_one("#task-info", Static).update(Text(f"{task.title}\n{task.cwd}\n\n상세 조회 실패: {plain(exc)}", style="#ec8597"))

    def _show_detail(self, detail: Detail):
        task = detail.task
        snapshot = self.board.snapshots[task.key.account_id]
        status = task.status.value + (f" · 마지막 확인 {age(task.observed_at)}" if snapshot.stale else "")
        info = f"{task.title}\n{self.config.account(task.key.account_id).label} · {status}\n\nModel: {task.model or '—'}\nReasoning: {task.reasoning_effort or '—'}\n\nProject\n{task.cwd}\nTokens: —\nUpdated: {age(task.updated_at)}"
        if task.persisted is False:
            info += "\n\n아직 저장 안 됨\n첫 사용자 메시지 전의 빈 세션입니다. 서버 메모리에만 있어 새 탭에서 재개할 수 없습니다. 기존 열린 세션에서 작업을 시작하거나 새 세션을 사용하세요."
        self.query_one("#task-info", Static).update(Text(info))
        heading = "마지막 답변" if detail.assistant_phase == "final_answer" else "최근 메시지 · " + (detail.assistant_phase or "아직 답변 없음")
        body = f"## 마지막 요청\n\n{detail.last_user or '아직 읽은 범위에 요청이 없습니다.'}\n\n---\n\n## {heading}\n\n{detail.last_assistant or '아직 읽은 범위에 답변이 없습니다.'}"
        self.query_one("#messages", Markdown).update(body)
        self.query_one("#more").display = not detail.complete

    @on(DataTable.RowHighlighted, "#tasks")
    def highlighted(self, event: DataTable.RowHighlighted):
        key = event.row_key.value
        if key and key == self._current_key() and not key.startswith("group:") and key != self._detail_key:
            self._choose_detail(key)

    @on(DataTable.RowSelected, "#tasks")
    def open_row(self, event: DataTable.RowSelected):
        key = event.row_key.value
        if key and not key.startswith("group:"):
            self._resume_dialog([key])

    @on(Select.Changed)
    def filter_changed(self, event: Select.Changed):
        if event.value is Select.BLANK:
            return
        if event.select.id == "account":
            self.account_filter = str(event.value)
        elif event.select.id == "status":
            self.status_filter = str(event.value)
        elif event.select.id == "workspace":
            self._set_workspace(str(event.value))
            return
        self._persist()
        self._render_tasks()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed):
        self.query = event.value
        self._persist()
        self._render_tasks()

    @on(Button.Pressed, "#more")
    def more_detail(self):
        if self._detail_key:
            self._detail_pages += 3
            self.load_detail(self._detail_key, self._detail_generation, self._detail_pages)

    def action_select_task(self):
        key = self._current_key()
        if key:
            self.saved = self.store.select(key, key not in self.saved["selected_keys"])
            self._render_tasks()

    def action_hide_task(self):
        key = self._current_key()
        if key:
            restoring = key in self.saved["hidden_keys"]
            self.saved = self.store.hide({key}, not restoring)
            self._render_tasks()

    def action_rename_task(self):
        key = self._current_key()
        if not key or self._rename_busy or self._delete_busy or self._launch_busy:
            return
        if self.renamer is None:
            self.notify("이 실행에서는 이름 변경 연동을 사용할 수 없습니다", severity="error")
            return
        task = self.board.tasks.get(key)
        if not task or self.board.snapshots[task.key.account_id].stale:
            self.notify("계정 연결을 확인한 뒤 이름을 변경할 수 있습니다", severity="error")
            return
        self.push_screen(RenameScreen(task, self.config.account(task.key.account_id).label), lambda name: self.rename_task(task.key, name) if name is not None else None)

    @work(group="rename")
    async def rename_task(self, key, name: str):
        if self._rename_busy or self._delete_busy or self._launch_busy or self.renamer is None:
            return
        self._rename_busy = True
        try:
            while self._poll_busy:
                await asyncio.sleep(0.05)
            renamed = await self.renamer.rename(key, name)
            self.notify(f"세션 이름 변경: {renamed.title}")
        except Exception as exc:
            self.notify(plain(exc), severity="error", timeout=15)
        finally:
            self._rename_busy = False
            if not self._board_closing:
                self.poll(full=True)

    def action_delete_task(self):
        if self.screen is not self.default_screen:
            return
        key = self._current_key()
        if key and not self._delete_busy and not self._launch_busy and not self._rename_busy:
            if self.deleter is None:
                self.notify("이 실행에서는 삭제 연동을 사용할 수 없습니다", severity="error")
                return
            task = self.board.tasks.get(key)
            if not task or self.board.snapshots[task.key.account_id].stale:
                self.notify("계정 연결을 확인한 뒤 삭제할 수 있습니다", severity="error")
                return
            self.prepare_delete(task.key)

    @work(group="delete")
    async def prepare_delete(self, key):
        if self._delete_busy or self._launch_busy or self._rename_busy or self.deleter is None:
            return
        self._delete_busy = True
        try:
            plan = await self.deleter.prepare(key)
            self.push_screen(DeleteScreen(plan, self.config.account(key.account_id).label), lambda confirmed: self.delete_task(plan) if confirmed else None)
        except Exception as exc:
            self.notify(plain(exc), severity="error", timeout=10)
        finally:
            self._delete_busy = False

    @work(group="delete")
    async def delete_task(self, plan: DeletePlan):
        if self._delete_busy or self._launch_busy or self._rename_busy or self.deleter is None:
            return
        self._delete_busy = True
        try:
            while self._poll_busy:
                await asyncio.sleep(0.05)
            await self.deleter.delete(plan)
            self.notify(f"{plan.task.title}: 대화와 하위 대화 {len(plan.descendants)}개를 삭제했습니다")
            try:
                self.saved = self.store.forget(plan.keys)
            except Exception as exc:
                self.notify("삭제는 완료됐지만 선택 목록 저장에 실패했습니다: " + plain(exc), severity="warning", timeout=15)
        except Exception as exc:
            self.notify(plain(exc), severity="error", timeout=15)
        finally:
            self._delete_busy = False
            if not self._board_closing:
                self.poll(full=True)

    def action_show_hidden(self):
        self.show_hidden = not self.show_hidden
        self._render_tasks()

    def action_search(self):
        self.query_one("#search", Input).focus()

    def action_group(self):
        modes = ["recent", "project", "account"]
        self.grouping = modes[(modes.index(self.grouping) + 1) % len(modes)]
        self._persist()
        self._render_tasks()

    def action_refresh_now(self):
        workspace_root.cache_clear()
        self._rerank_workspaces = True
        self.poll(full=True)

    def action_quit(self):
        self._board_closing = True
        self.workers.cancel_all()
        self.exit()

    def action_back(self):
        self.query_one("#tasks", DataTable).focus()
        self.remove_class("detail-only")
        self.default_screen.remove_class("detail-only")

    def action_detail(self):
        if self.has_class("compact"):
            self.add_class("detail-only")
            self.default_screen.add_class("detail-only")
        self.query_one("#detail", VerticalScroll).focus()

    def action_resume_selected(self):
        keys = self.saved["selected_keys"]
        if keys:
            self._resume_dialog(keys)
        else:
            self.notify("Space로 재개할 대화를 선택하세요")

    def _resume_dialog(self, keys: list[str]):
        tasks = self.board.tasks
        available = [tasks[key] for key in keys if key in tasks and key not in self.saved["hidden_keys"]]
        missing = [key for key in keys if key not in tasks]
        allow_open = self.launcher is not None and self._terminal_error is None
        self.push_screen(ResumeScreen(available, missing, allow_open, self._terminal_error), lambda action: self.launch_tasks([t.key.encoded for t in available], action))

    @work(group="launch")
    async def launch_tasks(self, keys: list[str], action: str):
        if action not in {"open", "copy"} or self._launch_busy or self._delete_busy or self._rename_busy:
            return
        if action == "open" and self._terminal_error:
            self.notify(self._terminal_error, severity="error", timeout=15)
            return
        self._launch_busy = True
        commands, failures, opened = [], [], []
        try:
            await self.board.refresh()
            hidden = set(self.store.read()["hidden_keys"])
            for key in keys:
                try:
                    task = self.board.tasks.get(key)
                    if not task or key in hidden:
                        raise ValueError("대화가 숨겨졌거나 원래 목록에 없습니다")
                    if self.board.snapshots[task.key.account_id].stale:
                        raise ValueError("계정에 연결할 수 없습니다")
                    task = await self.board.sources[task.key.account_id].read_task(task.key.thread_id)
                    spec = resume_spec(self.config, task)
                    commands.append(spec.command)
                    if action == "open":
                        if self.launcher is None:
                            raise ValueError("터미널 연동을 사용할 수 없습니다. 명령 복사를 사용하세요")
                        response = await self.launcher.open(spec)
                        confirmed = False
                        for _ in range(10):
                            task = await self.board.sources[task.key.account_id].read_task(task.key.thread_id)
                            if task.raw_status.get("type") != "notLoaded":
                                confirmed = True
                                break
                            await asyncio.sleep(0.5)
                        opened.append(key)
                        self.notify(f"{task.title}: " + ("대화 연결 확인" if confirmed else "탭 열림 · 대화 연결은 아직 확인되지 않음"), severity="information" if confirmed else "warning")
                except TerminalUnavailable as exc:
                    self._terminal_error = plain(exc)
                    failures.append(f"{key}: {self._terminal_error}\n나머지 항목의 자동 열기도 중단했습니다. 선택 목록은 유지됩니다.")
                    break
                except Exception as exc:
                    failures.append(f"{key}: {plain(exc)}")
            if action == "copy" and commands:
                if sys.platform == "win32":
                    self.copy_to_clipboard("\n".join(commands) + "\n")
                else:
                    proc = await asyncio.create_subprocess_exec("/usr/bin/pbcopy", stdin=asyncio.subprocess.PIPE)
                    await proc.communicate(("\n".join(commands) + "\n").encode())
                    if proc.returncode:
                        raise RuntimeError("클립보드에 복사하지 못했습니다")
                self.notify(f"재개 명령 {len(commands)}개 복사 완료")
            if failures:
                self.notify("\n".join(failures), severity="error", timeout=15)
            if opened:
                self.poll(full=True)
        except Exception as exc:
            self.notify(plain(exc), severity="error", timeout=10)
        finally:
            self._launch_busy = False

    def on_resize(self, event):
        self.set_class(event.size.width < 100, "compact")
        self.default_screen.set_class(event.size.width < 100, "compact")
