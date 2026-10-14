import json
from dataclasses import replace

import pytest
from websockets.asyncio.server import unix_serve

from cboard.deletion import CodexSessionDeleter, _DeleteRpc
from cboard.models import Account, ThreadKey
from cboard.sources.rpc import ReadOnlyRpc, RpcError


def thread(config, ident="root", **changes):
    return {"id": ident, "name": f"대화 {ident}", "cwd": str(config.accounts[0].home), "source": "cli", "status": {"type": "notLoaded"}, "parentThreadId": None, "ephemeral": False} | changes


def handler_for(state):
    async def handler(ws):
        async for raw in ws:
            message = json.loads(raw)
            if "id" not in message:
                continue
            method, params = message["method"], message.get("params", {})
            state["calls"].append((method, params))
            if method == "initialize":
                result = {}
            elif method == "thread/read":
                result = {"thread": state.get("read_overrides", {}).get(params["threadId"], state["threads"][params["threadId"]])}
            elif method == "thread/list":
                assert params["ancestorThreadId"] == "root"
                assert "subAgentThreadSpawn" in params["sourceKinds"]
                children = state["archived"] if params["archived"] else state["children"]
                start = int(params.get("cursor", "0"))
                result = {"data": [state["threads"][ident] for ident in children[start:start + 1]], "nextCursor": str(start + 1) if start + 1 < len(children) else None}
            elif method == "thread/loaded/list":
                result = {"data": state["loaded"]}
            elif method == "thread/delete":
                assert params == {"threadId": "root"}
                state["deleted"].append(params["threadId"])
                if state.get("drop_ack"):
                    await ws.close()
                    continue
                result = {}
            else:
                raise AssertionError(method)
            await ws.send(json.dumps({"id": message["id"], "result": result}))
    return handler


@pytest.fixture
async def deletion_server(config):
    state = {"threads": {"root": thread(config)}, "children": [], "archived": [], "loaded": [], "deleted": [], "calls": []}
    async with unix_serve(handler_for(state), str(config.accounts[0].socket_path)):
        yield state


async def test_delete_inspects_paginated_active_and_archived_descendants(config, deletion_server):
    state = deletion_server
    state["threads"].update({
        "child": thread(config, "child", parentThreadId="root", source="subAgentThreadSpawn"),
        "grandchild": thread(config, "grandchild", parentThreadId="child", source="subAgentOther"),
        "archived-child": thread(config, "archived-child", parentThreadId="root", source="subAgent"),
        "unrelated": thread(config, "unrelated", status={"type": "active", "activeFlags": []}),
    })
    state["children"], state["archived"], state["loaded"] = ["child", "grandchild"], ["archived-child"], ["unrelated"]
    deleter = CodexSessionDeleter(config)
    plan = await deleter.prepare(ThreadKey("a", "root"))
    assert plan.keys == {"a/root", "a/child", "a/grandchild", "a/archived-child"}
    assert not state["deleted"]
    await deleter.delete(plan)
    assert state["deleted"] == ["root"]
    assert all(method in {"initialize", "thread/read", "thread/list", "thread/loaded/list", "thread/delete"} for method, _ in state["calls"])


@pytest.mark.parametrize("status", ["active", "systemError", "futureState"])
async def test_busy_or_unknown_root_cannot_be_deleted(config, deletion_server, status):
    deletion_server["threads"]["root"]["status"] = {"type": status}
    with pytest.raises(ValueError, match="Ready 상태"):
        await CodexSessionDeleter(config).prepare(ThreadKey("a", "root"))
    assert not deletion_server["deleted"]


@pytest.mark.parametrize("change", [{"ephemeral": True}, {"parentThreadId": "parent"}])
async def test_ephemeral_or_child_root_is_rejected(config, deletion_server, change):
    deletion_server["threads"]["root"].update(change)
    with pytest.raises(ValueError, match="최상위"):
        await CodexSessionDeleter(config).prepare(ThreadKey("a", "root"))
    assert not deletion_server["deleted"]


@pytest.mark.parametrize("scenario", ["persisted", "ephemeral", "reload"])
async def test_loaded_descendants_or_reloaded_root_block_deletion(config, deletion_server, scenario):
    state = deletion_server
    if scenario == "reload":
        state["loaded"] = ["root"]
        state["threads"]["root"]["status"] = {"type": "active", "activeFlags": []}
    else:
        state["threads"]["child"] = thread(config, "child", parentThreadId="root", source="subAgentThreadSpawn", status={"type": "active", "activeFlags": []}, ephemeral=scenario == "ephemeral")
        if scenario == "persisted":
            state["children"] = ["child"]
        else:
            state["loaded"] = ["child"]
    with pytest.raises(ValueError, match="삭제를 중단"):
        await CodexSessionDeleter(config).prepare(ThreadKey("a", "root"))
    assert not state["deleted"]


@pytest.mark.parametrize("change", ["busy", "scope", "title"])
async def test_confirmation_rechecks_runtime_scope_and_identity(config, deletion_server, change):
    state = deletion_server
    deleter = CodexSessionDeleter(config)
    plan = await deleter.prepare(ThreadKey("a", "root"))
    if change == "busy":
        state["threads"]["root"]["status"] = {"type": "active", "activeFlags": []}
    elif change == "scope":
        state["threads"]["child"] = thread(config, "child", parentThreadId="root")
        state["children"] = ["child"]
    else:
        state["threads"]["root"]["name"] = "바뀐 제목"
    with pytest.raises(ValueError):
        await deleter.delete(plan)
    assert not state["deleted"]


async def test_delete_routes_to_exact_account_socket(config, deletion_server):
    b = Account("b", "B", config.accounts[0].home.parent / "b", config.accounts[0].socket_path.parent / "b.sock")
    cfg = replace(config, accounts=(*config.accounts, b))
    state_b = {"threads": {"root": thread(cfg)}, "children": [], "archived": [], "loaded": [], "deleted": [], "calls": []}
    async with unix_serve(handler_for(state_b), str(b.socket_path)):
        deleter = CodexSessionDeleter(cfg)
        plan = await deleter.prepare(ThreadKey("b", "root"))
        await deleter.delete(plan)
    assert plan.keys == {"b/root"}
    assert state_b["deleted"] == ["root"]
    assert deletion_server["calls"] == []


async def test_lost_delete_response_is_not_retried(config, deletion_server):
    deleter = CodexSessionDeleter(config)
    plan = await deleter.prepare(ThreadKey("a", "root"))
    deletion_server["drop_ack"] = True
    with pytest.raises(RpcError, match="자동 재시도하지"):
        await deleter.delete(plan)
    assert deletion_server["deleted"] == ["root"]


@pytest.mark.parametrize("rpc_class", [ReadOnlyRpc, _DeleteRpc])
async def test_generic_rpc_cannot_delete(config, rpc_class):
    rpc = rpc_class(config.accounts[0].socket_path)
    with pytest.raises(ValueError, match="Mutation"):
        await rpc.call("thread/delete", {"threadId": "root"})


async def test_loaded_ready_root_and_idle_child_can_be_deleted(config, deletion_server):
    state = deletion_server
    state["threads"]["root"]["status"] = {"type": "idle"}
    state["threads"]["child"] = thread(config, "child", parentThreadId="root", source="subAgentThreadSpawn", status={"type": "idle"})
    state["children"] = ["child"]
    state["loaded"] = ["root", "child"]
    deleter = CodexSessionDeleter(config)
    plan = await deleter.prepare(ThreadKey("a", "root"))
    assert plan.keys == {"a/root", "a/child"}
    await deleter.delete(plan)
    assert state["deleted"] == ["root"]


async def test_child_becoming_busy_after_list_is_read_fresh_and_blocks_deletion(config, deletion_server):
    state = deletion_server
    state["threads"]["child"] = thread(config, "child", parentThreadId="root", source="subAgentThreadSpawn", status={"type": "idle"})
    state["children"] = ["child"]
    state["loaded"] = ["child"]
    deleter = CodexSessionDeleter(config)
    plan = await deleter.prepare(ThreadKey("a", "root"))
    state["read_overrides"] = {"child": state["threads"]["child"] | {"status": {"type": "active", "activeFlags": ["waitingOnApproval"]}}}
    with pytest.raises(ValueError, match="Ready 상태"):
        await deleter.delete(plan)
    assert not state["deleted"]
