import asyncio
import json
import time

import pytest
from websockets.asyncio.server import unix_serve

from cboard.models import Status, Task
from cboard.sources.codex import CodexSource, message_detail
from cboard.sources.rpc import READ_METHODS, ReadOnlyRpc, RpcError


@pytest.mark.parametrize("raw,expected", [
    ({"type": "active", "activeFlags": []}, Status.WORKING),
    ({"type": "active", "activeFlags": ["waitingOnApproval"]}, Status.NEEDS_YOU),
    ({"type": "active", "activeFlags": ["waitingOnUserInput"]}, Status.NEEDS_YOU),
    ({"type": "active", "activeFlags": ["brandNewFlag"]}, Status.UNKNOWN),
    ({"type": "notLoaded"}, Status.INACTIVE),
    ({"type": "idle"}, Status.READY),
    ({"type": "systemError"}, Status.ERROR),
    ({"type": "futureState"}, Status.UNKNOWN),
])
def test_status_contract(raw, expected):
    t = Task.from_thread("a", {"id": "x", "source": "vscode", "status": raw}, time.time())
    assert t.status == expected


async def test_pagination_loaded_union_and_observer_only(config):
    methods = []
    root = {"id": "root", "name": "대화", "cwd": str(config.accounts[0].home), "source": "vscode", "status": {"type": "active", "activeFlags": []}}
    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            method = msg["method"]
            methods.append(method)
            if "id" not in msg:
                continue
            params = msg.get("params", {})
            if method == "initialize":
                result = {}
            elif method == "thread/list":
                assert params["useStateDbOnly"] is True
                assert "sourceKinds" not in params
                if not params.get("cursor"):
                    result = {"data": [root | {"id": str(i)} for i in range(100)], "nextCursor": "next"}
                else:
                    result = {"data": [root | {"id": "99"}, root | {"id": "100"}], "nextCursor": None}
            elif method == "thread/loaded/list":
                result = {"data": ["root", "child", "temporary"]}
            elif method == "thread/read":
                ident = params["threadId"]
                assert not params["includeTurns"]
                result = {"thread": root | {"id": ident, "parentThreadId": "root" if ident == "child" else None, "ephemeral": ident == "temporary"}}
            elif method == "thread/turns/list":
                result = {"data": []}
            else:
                raise AssertionError(method)
            await ws.send(json.dumps({"id": msg["id"], "result": result}))
    async with unix_serve(handler, str(config.accounts[0].socket_path)):
        source = CodexSource(config.accounts[0], 1)
        try:
            snapshot = await source.refresh()
            assert snapshot.complete and not snapshot.stale
            assert len(snapshot.tasks) == 104
            assert sum(t.is_child for t in snapshot.tasks) == 1
            assert any(t.ephemeral for t in snapshot.tasks)
            assert all(t.source == "vscode" for t in snapshot.tasks)
            assert set(methods) <= READ_METHODS | {"initialize", "initialized"}
            with pytest.raises(ValueError, match="Mutation"):
                await source.rpc.call("thread/resume", {"threadId": "root"})
        finally:
            await source.close()


async def test_failure_keeps_last_status(config):
    calls = 0
    source = CodexSource(config.accounts[0])
    async def call(method, params=None):
        nonlocal calls
        if calls:
            raise RpcError("disconnected")
        if method == "thread/list":
            return {"data": [{"id": "x", "status": {"type": "active", "activeFlags": []}}]}
        return {"data": []}
    source.rpc.call = call
    good = await source.refresh()
    calls += 1
    failed = await source.refresh()
    assert not good.stale and failed.stale
    assert failed.tasks[0].status == Status.WORKING
    assert failed.tasks[0].observed_at == good.tasks[0].observed_at


def test_current_turn_commentary_does_not_show_previous_final(config):
    t = Task.from_thread("a", {"id": "one"}, 0)
    items = [
        {"type": "agentMessage", "phase": "commentary", "text": "현재 조사 중"},
        {"type": "userMessage", "content": [{"type": "text", "text": "새 요청"}]},
        {"type": "agentMessage", "phase": "final_answer", "text": "과거 완료"},
    ]
    d = message_detail(t, items, True)
    assert d.last_user == "새 요청"
    assert d.last_assistant == "현재 조사 중"
    assert d.assistant_phase == "commentary"
    pending = message_detail(t, items[1:], True)
    assert pending.last_assistant == ""


async def test_rpc_timeout_and_reconnect_do_not_mix_responses(config):
    connection_number = 0
    async def handler(ws):
        nonlocal connection_number
        connection_number += 1
        number = connection_number
        async for raw in ws:
            msg = json.loads(raw)
            if "id" not in msg:
                continue
            if msg["method"] == "thread/list" and number == 1:
                await asyncio.sleep(0.15)
                continue
            await ws.send(json.dumps({"id": msg["id"], "result": {"connection": number}}))
    async with unix_serve(handler, str(config.accounts[0].socket_path)):
        rpc = ReadOnlyRpc(config.accounts[0].socket_path, 0.08)
        try:
            with pytest.raises(RpcError, match="timed out"):
                await rpc.call("thread/list")
            rpc._next_attempt = 0
            result = await rpc.call("thread/list")
            assert result["connection"] == 2
        finally:
            await rpc.close()


async def test_loaded_thread_disappearing_does_not_break_whole_account(config):
    source = CodexSource(config.accounts[0])
    async def call(method, params=None):
        if method == "thread/list":
            return {"data": [{"id": "root", "status": {"type": "idle"}}]}
        if method == "thread/loaded/list":
            return {"data": ["root", "disappeared-child"]}
        if params["threadId"] == "disappeared-child":
            raise RpcError("thread not found", -32602)
        return {"thread": {"id": "root", "status": {"type": "idle"}}}
    source.rpc.call = call
    result = await source.refresh()
    assert result.complete and not result.stale
    assert [t.key.thread_id for t in result.tasks] == ["root"]


@pytest.mark.parametrize("listed", [False, True])
async def test_first_message_pending_thread_is_visible_but_not_resumable(config, listed):
    from cboard.terminals.command import resume_spec
    source = CodexSource(config.accounts[0])
    materialized = False
    methods = []
    root = {"id": "empty", "name": "빈 세션", "cwd": str(config.accounts[0].home), "source": "vscode", "ephemeral": False, "status": {"type": "idle"}, "historyMode": "paginated"}
    async def call(method, params=None):
        methods.append(method)
        if method == "thread/list":
            return {"data": [root] if listed or materialized else []}
        if method == "thread/loaded/list":
            return {"data": ["empty"]}
        if method == "thread/read":
            return {"thread": root}
        if method == "thread/turns/list":
            if not materialized:
                raise RpcError("thread empty is not materialized yet; thread/turns/list is unavailable before first user message", -32600)
            return {"data": []}
        raise AssertionError(method)
    source.rpc.call = call
    snapshot = await source.refresh()
    assert snapshot.complete and not snapshot.stale
    assert snapshot.tasks[0].status == Status.READY
    assert snapshot.tasks[0].persisted is False
    with pytest.raises(ValueError, match="빈 세션"):
        resume_spec(config, snapshot.tasks[0])
    detail = await source.detail("empty")
    assert detail.last_user == detail.last_assistant == ""
    assert detail.task.persisted is False
    assert "thread/items/list" not in methods
    materialized = True
    snapshot = await source.refresh_loaded()
    assert snapshot.tasks[0].persisted is True
    assert resume_spec(config, snapshot.tasks[0]).key.thread_id == "empty"
    assert "thread/resume" not in methods


async def test_saved_proof_reused_only_for_poll_and_rechecked_for_actions(config):
    from collections import Counter
    from dataclasses import replace
    from cboard.models import Snapshot
    source = CodexSource(config.accounts[0])
    methods = Counter()
    materialized = True
    present = True
    root = {"id": "root", "status": {"type": "idle"}}
    # A persisted cache/list flag alone must never bypass the first probe.
    cached = replace(Task.from_thread("a", root, 0), persisted=True)
    source.snapshot = Snapshot("a", (cached,), 0, True)
    async def call(method, params=None):
        methods[method] += 1
        if method == "thread/list":
            return {"data": [root] if present else []}
        if method == "thread/loaded/list":
            return {"data": ["root"] if present else []}
        if method == "thread/read":
            if not present:
                raise RpcError("thread not found", -32602)
            return {"thread": root}
        if method == "thread/turns/list":
            if not materialized:
                raise RpcError("not materialized yet before first user message", -32600)
            return {"data": []}
        raise AssertionError(method)
    source.rpc.call = call
    await source.refresh_loaded()
    assert methods["thread/turns/list"] == 1
    await source.refresh_loaded()
    assert methods["thread/read"] == 2
    assert methods["thread/turns/list"] == 1
    materialized = False
    assert (await source.read_task("root")).persisted is False
    assert "root" not in source._materialized_ids
    await source.refresh_loaded()
    await source.refresh_loaded()
    assert methods["thread/turns/list"] == 4  # Failed proof is never cached.
    materialized = True
    await source.refresh_loaded()
    assert source.snapshot.tasks[0].persisted is True
    present = False
    await source.refresh_loaded()
    assert source.snapshot.tasks == ()
    assert not source._materialized_ids
    present = True
    await source.refresh_loaded()
    assert methods["thread/turns/list"] == 6
