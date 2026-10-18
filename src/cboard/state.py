from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .locking import file_lock
from .models import Snapshot, Task


class StateError(RuntimeError):
    pass


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class StateStore:
    def __init__(self, directory: Path):
        self.path = directory / "state.json"
        self.warning = ""

    @contextmanager
    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.parent / "state.lock"):
            yield

    def _read(self):
        default = {"schema_version": 1, "hidden_keys": [], "selected_keys": [], "filters": {}, "grouping": "recent"}
        if not self.path.exists():
            return default
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state must be an object")
            if data.get("schema_version") != 1:
                raise StateError("Unsupported state schema_version; state was not changed")
            if any(not isinstance(data.get(k, []), list) or any(not isinstance(v, str) for v in data.get(k, [])) for k in ["hidden_keys", "selected_keys"]):
                raise ValueError("invalid saved keys")
            if not isinstance(data.get("filters", {}), dict):
                raise ValueError("invalid filters")
            return default | data
        except (ValueError, OSError) as exc:
            backup = self.path.with_name(f"state.corrupt-{time.time_ns()}.json")
            shutil.copy2(self.path, backup)
            self.warning = f"저장 설정을 복구했습니다. 원본: {backup} ({exc})"
            return default

    def read(self):
        with self._lock():
            return self._read()

    def _mutate(self, change):
        with self._lock():
            data = self._read()
            change(data)
            atomic_json(self.path, data)
            return data

    def select(self, key: str, selected: bool):
        def change(data):
            keys = set(data["selected_keys"])
            if selected and key not in data["hidden_keys"]:
                keys.add(key)
            else:
                keys.discard(key)
            data["selected_keys"] = sorted(keys)
        return self._mutate(change)

    def hide(self, keys: set[str], hidden: bool):
        def change(data):
            current = set(data["hidden_keys"])
            if hidden:
                current.update(keys)
                data["selected_keys"] = sorted(set(data["selected_keys"]) - keys)
            else:
                current.difference_update(keys)
            data["hidden_keys"] = sorted(current)
        return self._mutate(change)

    def preferences(self, filters: dict, grouping: str):
        return self._mutate(lambda data: data.update(filters=filters, grouping=grouping))

    def forget(self, keys: set[str]):
        def change(data):
            for field in ("selected_keys", "hidden_keys"):
                data[field] = sorted(set(data[field]) - keys)
        return self._mutate(change)


def save_snapshot(directory: Path, snapshot: Snapshot):
    if snapshot.stale or not snapshot.complete:
        return
    atomic_json(directory / "accounts" / f"{snapshot.account_id}.json", {
        "schema_version": 1, "account_id": snapshot.account_id,
        "fetched_at": snapshot.fetched_at, "tasks": [t.to_json() for t in snapshot.tasks],
    })


def load_snapshot(directory: Path, account_id: str) -> Snapshot:
    try:
        data = json.loads((directory / "accounts" / f"{account_id}.json").read_text(encoding="utf-8"))
        if data["schema_version"] != 1 or data["account_id"] != account_id:
            raise ValueError("invalid snapshot")
        tasks = tuple(Task.from_json(t) for t in data["tasks"])
        if any(t.key.account_id != account_id for t in tasks):
            raise ValueError("snapshot account mismatch")
        return Snapshot(account_id, tasks, data["fetched_at"], True, "마지막 저장 목록", True)
    except (OSError, ValueError, KeyError, TypeError):
        return Snapshot(account_id)
