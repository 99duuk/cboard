from __future__ import annotations

import asyncio
import itertools
import json
import sys
import time
from pathlib import Path

from websockets.asyncio.client import unix_connect


READ_METHODS = frozenset({"thread/list", "thread/loaded/list", "thread/read", "thread/turns/list", "thread/items/list"})


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class ReadOnlyRpc:
    """One receiver per connection, with bounded read-only requests."""

    def __init__(self, socket_path: Path, timeout: float = 5):
        self.socket_path = socket_path
        self.timeout = timeout
        self._ws = None
        self._reader = None
        self._pending = {}
        self._ids = itertools.count(1)
        self._connect_lock = asyncio.Lock()
        self._limit = asyncio.Semaphore(2)
        self._next_attempt = 0.0
        self._failures = 0

    async def _receive(self, ws):
        error = RpcError("Codex connection closed")
        try:
            async for raw in ws:
                message = json.loads(raw)
                # An observer never participates in approvals or thread execution.
                if "method" in message:
                    continue
                future = self._pending.get(message.get("id"))
                if future is not None and not future.done():
                    if "error" in message:
                        err = message["error"]
                        future.set_exception(RpcError(err.get("message", "RPC error"), err.get("code")))
                    else:
                        future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = RpcError(f"Codex connection closed: {exc}")
        finally:
            if self._ws is ws:
                self._ws = None
                for future in tuple(self._pending.values()):
                    if not future.done():
                        future.set_exception(error)

    async def _request(self, method: str, params: dict):
        ws = self._ws
        if ws is None:
            raise RpcError("Codex is disconnected")
        request_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(self.timeout):
                await ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
                return await future
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _connect(self):
        if sys.platform == "win32":
            raise RpcError("Windows의 실행 중 대화 연결은 아직 지원되지 않습니다. 계정 관리는 cnew · clist · cusage를 사용하세요")
        async with self._connect_lock:
            if self._ws is not None:
                return
            if time.monotonic() < self._next_attempt:
                raise RpcError("Reconnect pending")
            try:
                self._ws = await unix_connect(
                    str(self.socket_path), open_timeout=self.timeout, close_timeout=1,
                    max_size=32 * 1024 * 1024, compression=None,
                )
                self._reader = asyncio.create_task(self._receive(self._ws))
                await self._request("initialize", {
                    "clientInfo": {"name": "cboard", "title": "Cboard", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                })
                await self._ws.send(json.dumps({"method": "initialized", "params": {}}))
                self._failures = 0
            except BaseException:
                await self.close()
                self._failures += 1
                self._next_attempt = time.monotonic() + [1, 2, 5, 10, 30][min(self._failures - 1, 4)]
                raise

    async def call(self, method: str, params: dict | None = None):
        if method not in READ_METHODS:
            raise ValueError(f"Mutation is not allowed: {method}")
        async with self._limit:
            await self._connect()
            try:
                return await self._request(method, params or {})
            except TimeoutError as exc:
                await self.close()
                self._next_attempt = time.monotonic() + 1
                raise RpcError(f"Request timed out: {method}") from exc

    async def close(self):
        ws, reader = self._ws, self._reader
        self._reader = None
        if ws is not None:
            await ws.close()
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        self._ws = None
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(RpcError("Codex connection closed"))
