import json
from dataclasses import replace

import pytest
from websockets.asyncio.server import unix_serve

from cboard.models import Account, ThreadKey
from cboard.renaming import CodexSessionRenamer, _RenameRpc, validate_name
from cboard.sources.rpc import ReadOnlyRpc, RpcError


@pytest.mark.parametrize("name", ["", "  ", "first\nsecond", "first\x00second", "first\x1bsecond"])
def test_invalid_name_is_rejected(name):
    with pytest.raises(ValueError):
        validate_name(name)


def test_name_preserves_unicode_and_literal_characters():
    assert validate_name("  작업 🚀 $(literal) `text`  ") == "작업 🚀 $(literal) `text`"


@pytest.fixture
async def rename_server(config):
    state = {"thread": {"id": "one", "name": "기존 이름", "cwd": str(config.accounts[0].home), "source": "cli", "status": {"type": "active", "activeFlags": []}}, "calls": [], "renames": [], "sockets": [], "drop_ack": False}
    async def handler(ws):
        state["sockets"].append(ws.local_address)
        async for raw in ws:
            message = json.loads(raw)
            if "id" not in message:
                continue
            method, params = message["method"], message.get("params", {})
            state["calls"].append(method)
            if method == "initialize":
                result = {}
            elif method == "thread/read":
                assert params == {"threadId": "one", "includeTurns": False}
                result = {"thread": state["thread"]}
            elif method == "thread/name/set":
                assert params["threadId"] == "one"
                state["renames"].append(params["name"])
                state["thread"]["name"] = params["name"]
                if state["drop_ack"]:
                    await ws.close()
                    continue
                result = {}
            else:
                raise AssertionError(method)
            await ws.send(json.dumps({"id": message["id"], "result": result}))
    async with unix_serve(handler, str(config.accounts[0].socket_path)):
        state["handler"] = handler
        yield state


@pytest.mark.parametrize("status", ["active", "idle", "notLoaded"])
async def test_rename_is_persisted_without_resuming_or_starting_a_turn(config, rename_server, status):
    rename_server["thread"]["status"] = {"type": status}
    renamed = await CodexSessionRenamer(config).rename(ThreadKey("a", "one"), "  변경된 세션 이름 🚀  ")
    assert renamed.title == "변경된 세션 이름 🚀"
    assert renamed.raw_status["type"] == status
    assert rename_server["renames"] == [renamed.title]
    assert rename_server["calls"] == ["initialize", "thread/read", "thread/name/set", "thread/read"]


@pytest.mark.parametrize("change", [{"ephemeral": True}, {"id": "another"}])
async def test_ephemeral_or_wrong_thread_cannot_be_renamed(config, rename_server, change):
    rename_server["thread"].update(change)
    with pytest.raises(ValueError):
        await CodexSessionRenamer(config).rename(ThreadKey("a", "one"), "변경")
    assert rename_server["renames"] == []


async def test_rename_routes_to_exact_account_socket(config, rename_server):
    b = Account("b", "B", config.accounts[0].home.parent / "b", config.accounts[0].socket_path.parent / "b.sock")
    cfg = replace(config, accounts=(*config.accounts, b))
    async with unix_serve(rename_server["handler"], str(b.socket_path)):
        result = await CodexSessionRenamer(cfg).rename(ThreadKey("b", "one"), "계정 B 이름")
    assert result.key == ThreadKey("b", "one")
    assert rename_server["renames"] == ["계정 B 이름"]
    assert rename_server["sockets"] == [str(b.socket_path)]


async def test_lost_rename_response_is_not_retried(config, rename_server):
    rename_server["drop_ack"] = True
    with pytest.raises(RpcError, match="자동 재시도하지"):
        await CodexSessionRenamer(config).rename(ThreadKey("a", "one"), "새 이름")
    assert rename_server["renames"] == ["새 이름"]


@pytest.mark.parametrize("rpc_class", [ReadOnlyRpc, _RenameRpc])
async def test_generic_rpc_cannot_rename(config, rpc_class):
    with pytest.raises(ValueError, match="Mutation"):
        await rpc_class(config.accounts[0].socket_path).call("thread/name/set", {"threadId": "one", "name": "새 이름"})
