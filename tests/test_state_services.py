import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from cboard.models import Account, Snapshot, Status, ThreadKey
from cboard.services import Board
from cboard.state import StateError, StateStore, atomic_json
from conftest import FakeSource, task


def test_seven_two_hidden_five_selected_survive_restart(config):
    store = StateStore(config.state_dir)
    for i in range(7):
        store.select(f"a/{i}", True)
    store.hide({"a/0", "a/1"}, True)
    restored = StateStore(config.state_dir).read()
    assert set(restored["selected_keys"]) == {f"a/{i}" for i in range(2, 7)}
    assert set(restored["hidden_keys"]) == {"a/0", "a/1"}
    store.hide({"a/0"}, False)
    assert "a/0" not in store.read()["selected_keys"]


def test_concurrent_state_changes_are_merged(config):
    def select(i):
        StateStore(config.state_dir).select(f"a/{i}", True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(select, range(30)))
    assert len(StateStore(config.state_dir).read()["selected_keys"]) == 30


def test_corrupt_and_future_state_are_preserved(config):
    store = StateStore(config.state_dir)
    store.path.parent.mkdir()
    store.path.write_text("broken")
    assert store.read()["selected_keys"] == []
    assert any(p.read_text() == "broken" for p in store.path.parent.glob("state.corrupt-*.json"))
    store.path.write_text('{"schema_version": 99}')
    with pytest.raises(StateError):
        store.select("a/1", True)
    assert json.loads(store.path.read_text())["schema_version"] == 99


def test_atomic_write_failure_preserves_previous_file(tmp_path, monkeypatch):
    import os
    path = tmp_path / "state.json"
    atomic_json(path, {"old": True})
    def fail(*args):
        raise OSError("interrupted")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob("*.tmp"))


async def test_same_thread_id_is_scoped_by_account_and_failure_isolated(config):
    a = task(config)
    b = replace(a, key=ThreadKey("b", "one"))
    accounts = config.accounts + (Account("b", "B", config.accounts[0].home.parent / "b", config.accounts[0].socket_path.parent / "b.sock"),)
    cfg = replace(config, accounts=accounts)
    sources = {"a": FakeSource([a]), "b": FakeSource([b])}
    async def fail():
        raise RuntimeError("offline")
    board = Board(cfg, sources)
    await board.refresh()
    assert set(board.tasks) == {"a/one", "b/one"}
    sources["b"].refresh = fail
    await board.refresh()
    assert not board.snapshots["a"].stale
    assert board.snapshots["b"].stale
    assert set(board.tasks) == {"a/one", "b/one"}


def test_hidden_and_child_filters_do_not_change_source(config):
    tasks = [task(config, "one"), task(config, "two"), replace(task(config, "child"), parent_thread_id="one")]
    board = Board(config, {"a": FakeSource(tasks)})
    board.snapshots["a"] = Snapshot("a", tuple(tasks), 1, True)
    visible, counts = board.visible({"hidden_keys": ["a/one"]})
    assert [t.key.thread_id for t in visible] == ["two"]
    assert len(board.snapshots["a"].tasks) == 3


def test_recent_sort_ignores_status_and_has_stable_ties(config):
    tasks = [
        replace(task(config, "old"), updated_at=1, status=Status.NEEDS_YOU),
        replace(task(config, "recent"), updated_at=100, status=Status.INACTIVE),
        replace(task(config, "middle-b"), updated_at=50, status=Status.WORKING),
        replace(task(config, "middle-a"), updated_at=50, status=Status.READY),
    ]
    board = Board(config, {"a": FakeSource(tasks)})
    board.snapshots["a"] = Snapshot("a", tuple(tasks), 100, True)
    visible, counts = board.visible({})
    assert [t.key.thread_id for t in visible] == ["recent", "middle-a", "middle-b", "old"]
    assert sum(counts.values()) == 4
    visible, _ = board.visible({}, status="Needs you")
    assert [t.key.thread_id for t in visible] == ["old"]


def test_forget_deleted_keys_preserves_other_accounts_and_preferences(config):
    store = StateStore(config.state_dir)
    for key in ["a/root", "a/child", "b/root"]:
        store.select(key, True)
    store.hide({"a/hidden-child", "b/hidden-child"}, True)
    store.preferences({"query": "saved"}, "recent")
    state = store.forget({"a/root", "a/child", "a/hidden-child"})
    assert state["selected_keys"] == ["b/root"]
    assert state["hidden_keys"] == ["b/hidden-child"]
    assert state["filters"] == {"query": "saved"}


def test_task_index_reuses_tuple_and_keeps_new_observation(config):
    original = task(config)
    board = Board(config, {"a": FakeSource([original])})
    board.snapshots["a"] = Snapshot("a", (original,), 100, True)
    index = board.tasks
    assert board.tasks is index
    observed = replace(original, observed_at=101)
    assert observed == original
    board.snapshots["a"] = Snapshot("a", (observed,), 101, True)
    assert board.tasks is not index
    assert board.tasks["a/one"].observed_at == 101
    changed = replace(observed, title="새 이름", persisted=False)
    assert changed != observed
    board.snapshots["a"] = Snapshot("a", (changed,), 102, True)
    assert board.tasks["a/one"] is changed


async def test_unchanged_poll_skips_save_but_changes_full_and_retry_save(config, monkeypatch):
    import cboard.services as services
    source = FakeSource([task(config)])
    board = Board(config, {"a": source})
    writes = []
    fail = False
    def save(folder, snapshot):
        writes.append(snapshot)
        if fail:
            raise OSError("disk unavailable")
    monkeypatch.setattr(services, "save_snapshot", save)
    await board.refresh()
    source.tasks[0] = replace(source.tasks[0], observed_at=101)
    await board.refresh(loaded=True)
    assert len(writes) == 1
    for change in [dict(title="새 이름"), dict(status=Status.WORKING), dict(persisted=False)]:
        source.tasks[0] = replace(source.tasks[0], **change)
        await board.refresh(loaded=True)
    assert len(writes) == 4
    await board.refresh()
    assert len(writes) == 5  # Full refresh always persists freshness.
    fail = True
    await board.refresh()
    assert "a" in board.errors
    fail = False
    await board.refresh(loaded=True)
    assert len(writes) == 7
    assert not board.errors


async def test_recovered_stale_poll_saves_even_without_task_changes(config, monkeypatch):
    import cboard.services as services
    original = task(config)
    board = Board(config, {"a": FakeSource([original])})
    board.snapshots["a"] = Snapshot("a", (original,), 99, True, "offline", True)
    writes = []
    monkeypatch.setattr(services, "save_snapshot", lambda folder, snapshot: writes.append(snapshot))
    await board.refresh(loaded=True)
    assert len(writes) == 1
    assert not writes[0].stale
