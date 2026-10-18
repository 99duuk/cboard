from __future__ import annotations

import os
import math
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .models import Account

DEFAULT_ACCOUNTS = [("codex", ".codex"), ("cwork", ".codex-work"), ("ccomm", ".codex-comm"), ("csay", ".codex-say"), ("crim", ".codex-rim")]


@dataclass(frozen=True)
class Config:
    accounts: tuple[Account, ...]
    executable: Path
    state_dir: Path
    cache_dir: Path
    loaded_interval: float = 3
    list_interval: float = 15
    timeout: float = 5

    def account(self, account_id: str) -> Account:
        return next(a for a in self.accounts if a.id == account_id)


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) if sys.platform == "win32" else Path.home() / ".config"
    return Path(os.environ.get("XDG_CONFIG_HOME", base)) / "cboard/config.toml"


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data.get("schema_version", 1) != 1:
        raise ValueError("Unsupported config schema_version")
    defaults = [] if sys.platform != "darwin" else [{"id": name, "codex_home": str(Path.home() / directory)} for name, directory in DEFAULT_ACCOUNTS]
    registry = os.environ.get("CENV_REGISTRY")
    if registry and "accounts" not in data:
        shared = tomllib.loads(Path(registry).read_text(encoding="utf-8-sig"))
        if shared.get("version") != 1:
            raise ValueError("Unsupported account manager registry version")
        defaults = [{"id": a["id"], "label": a.get("label", a["id"]), "codex_home": a["home"]}
                    for a in shared.get("accounts", [])]
    entries = data.get("accounts", defaults)
    accounts = []
    ids, homes, sockets = set(), set(), set()
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        account_id = entry.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", account_id):
            raise ValueError(f"Invalid account id: {account_id!r}")
        home = Path(entry["codex_home"]).expanduser().resolve()
        socket = Path(entry.get("socket_path", home / "app-server-control/app-server-control.sock")).expanduser().absolute()
        if account_id in ids or home in homes or socket.resolve() in sockets:
            raise ValueError(f"Duplicate account id/home/socket: {account_id}")
        ids.add(account_id); homes.add(home); sockets.add(socket.resolve())
        profile = entry.get("iterm_profile")
        background = entry.get("iterm_background")
        if profile is not None and (not isinstance(profile, str) or not profile.strip() or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in profile)):
            raise ValueError(f"Invalid iterm_profile: {account_id}")
        if background is not None and (not isinstance(background, str) or not re.fullmatch(r"[0-9a-fA-F]{6}", background)):
            raise ValueError(f"iterm_background must be six hex digits: {account_id}")
        accounts.append(Account(account_id, str(entry.get("label", account_id.upper())), home, socket, profile, background.lower() if background is not None else None))
    managed_cli = None
    if registry:
        local = Path.home()/'.config/codex-environment/local.toml'
        if local.exists():
            managed_cli = tomllib.loads(local.read_text(encoding='utf-8-sig')).get('cli')
    executable = Path(data.get("codex_executable") or os.environ.get("CENV_CLI") or managed_cli or shutil.which("codex") or ("codex.exe" if sys.platform == "win32" else "/opt/homebrew/bin/codex")).expanduser().resolve()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    state_base = local if sys.platform == "win32" else Path.home() / ".local/share"
    cache_base = local if sys.platform == "win32" else Path.home() / ".cache"
    state_dir = Path(os.environ.get("XDG_DATA_HOME", state_base)) / "cboard"
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME", cache_base)) / "cboard"
    if sys.platform == "win32":
        state_dir /= "state"
        cache_dir /= "cache"
    intervals = [float(data.get(k, default)) for k, default in [("loaded_interval", 3), ("list_interval", 15), ("timeout", 5)]]
    if any(not math.isfinite(n) or n < 0.1 or n > 3600 for n in intervals):
        raise ValueError("Intervals and timeout must be between 0.1 and 3600 seconds")
    return Config(tuple(accounts), executable, state_dir, cache_dir, *intervals)
