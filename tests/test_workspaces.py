from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cboard.models import Snapshot, Status
from cboard.services import Board
from cboard.workspaces import RECENT_SECONDS, UNASSIGNED, workspace_label, workspace_name, workspace_path, workspace_relative, workspace_root
from conftest import FakeSource, task


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    workspace_root.cache_clear()
    yield folder
    workspace_root.cache_clear()


@pytest.mark.parametrize("relative,expected", [
    (".", "."), ("Desktop", "Desktop"), ("Desktop/temp/sub", "Desktop"),
    ("Downloads/archive/project", "Downloads"),
    ("Documents/workspace/bottling-workspace/slot-3/frontend", "Documents/workspace/bottling-workspace"),
    ("Documents/workspace/bottling-workspace/slot-1", "Documents/workspace/bottling-workspace"),
    ("Documents/workspace/mnemosyne-workspace/main", "Documents/workspace/mnemosyne-workspace"),
    ("Documents/workspace/other/backend", "Documents/workspace/other"),
    ("Projects/custom-workspace/slot-7", "Projects/custom-workspace"),
    ("Projects/ordinary", "Projects/ordinary"),
    ("Downloads-other/sub", "Downloads-other/sub"),
])
def test_directory_grouping(home, relative, expected):
    cwd = str(home / relative)
    root = str(home / expected)
    assert workspace_root(cwd) == root
    assert workspace_relative(cwd, root) == str(Path(relative).relative_to(expected))


def test_repository_subfolders_and_legacy_missing_paths(home):
    repo = home / "Projects/repo"
    repo.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: elsewhere")
    assert workspace_root(str(repo / "frontend/src")) == str(repo)
    assert workspace_root(str(home / "missing/folder")) == str(home / "missing/folder")
    assert workspace_root("") == UNASSIGNED
    assert workspace_relative("", UNASSIGNED) == "—"
    assert workspace_root("/") == "/"


def test_labels_keep_full_path_identity(home):
    a, b = str(home / "A/same-workspace"), str(home / "B/same-workspace")
    assert workspace_root(a) != workspace_root(b)
    assert workspace_label(a) != workspace_label(b)
    assert workspace_label(str(home)) == "홈(루트) · ~/"
    assert "~/A/same-workspace" in workspace_label(a)
    assert workspace_name(a) == "same-workspace"
    assert workspace_path(a) == "~/A/same-workspace"
    assert workspace_name(str(home)) == "홈(루트)"
    assert workspace_path(str(home)) == "~/"
    assert workspace_name(UNASSIGNED) == "작업 경로 없음"
    assert workspace_path(UNASSIGNED) == "경로 정보 없음"
    assert workspace_path(str(home / "[odd] · name-workspace")) == "~/[odd] · name-workspace"


def test_recent_counts_merge_slots_accounts_and_respect_hidden(config, home, monkeypatch):
    import cboard.services as services
    now = 10_000_000
    monkeypatch.setattr(services, "time", SimpleNamespace(time=lambda: now))
    root = home / "Documents/workspace/bottling-workspace"
    tasks = [
        replace(task(config, "new"), cwd=str(root / "slot-1"), updated_at=now, status=Status.WORKING),
        replace(task(config, "edge"), cwd=str(root / "slot-3/frontend"), updated_at=now - RECENT_SECONDS),
        replace(task(config, "old"), cwd=str(root / "main"), updated_at=now - RECENT_SECONDS - 1),
        replace(task(config, "hidden"), cwd=str(root / "slot-2"), updated_at=now),
        replace(task(config, "child"), cwd=str(root / "slot-2"), updated_at=now, parent_thread_id="new"),
        replace(task(config, "other", account_id="b"), cwd=str(root / "slot-5"), updated_at=now),
    ]
    board = Board(config, {"a": FakeSource(tasks)})
    board.snapshots["a"] = Snapshot("a", tuple(tasks[:-1]), now, True)
    board.snapshots["b"] = Snapshot("b", (tasks[-1],), now, True)
    state = {"hidden_keys": ["a/hidden"]}
    assert board.workspace_counts(state) == {str(root): 3}
    assert board.workspace_counts(state, show_hidden=True) == {str(root): 4}
    visible, counts = board.visible(state, workspace=str(root))
    assert {t.key.encoded for t in visible} == {"a/new", "a/edge", "b/other"}
    assert sum(counts.values()) == 3
    filtered, counts = board.visible(state, workspace=str(root), account="a", status="Working", query="new")
    assert [t.key.thread_id for t in filtered] == ["new"]
    assert counts == {"Working": 1}
    assert "a/old" in {t.key.encoded for t in board.visible(state)[0]}
