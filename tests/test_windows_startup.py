import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cboard import config as config_module
from cboard.config import load_config
from cboard.models import ThreadKey
from cboard.state import StateStore
from cboard.terminals.command import ResumeSpec


def test_windows_empty_config_uses_native_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    config = load_config()
    assert config.accounts == ()
    assert config_module.config_path() == tmp_path / "roaming/cboard/config.toml"
    assert config.state_dir == tmp_path / "local/cboard/state"
    assert config.cache_dir == tmp_path / "local/cboard/cache"
    assert not config.state_dir.exists()


def test_explicit_empty_accounts_are_valid(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("accounts = []\n", encoding="utf-8")
    assert load_config(path).accounts == ()


def test_state_unicode_roundtrip(tmp_path):
    store = StateStore(tmp_path)
    store.preferences({"query": "한글 ' $(nope)"}, "recent")
    assert store.read()["filters"]["query"] == "한글 ' $(nope)"
    assert json.loads(store.path.read_bytes().decode("utf-8"))["schema_version"] == 1


async def test_windows_unix_transport_reports_unsupported(tmp_path, monkeypatch):
    from cboard.sources import rpc
    from cboard.sources.codex import CodexSource
    from cboard.models import Account
    monkeypatch.setattr(rpc, "sys", SimpleNamespace(platform="win32"))
    source = CodexSource(Account("a", "A", tmp_path, tmp_path / "missing.sock"))
    try:
        snapshot = await source.refresh()
        assert snapshot.stale
        assert "Windows" in snapshot.error and "지원되지" in snapshot.error
    finally:
        await source.close()


def test_windows_launcher_uses_encoded_literal_arguments(tmp_path, monkeypatch):
    from cboard.terminals import windows
    calls = []
    monkeypatch.setattr(windows.shutil, "which", lambda name: "pwsh.exe" if name == "pwsh.exe" else None)
    monkeypatch.setattr(windows.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
    path = tmp_path / "quote' $(New-Item NEVER).toml"
    spec = ResumeSpec(ThreadKey("a", "one"), ("codex.exe", "resume", "one"), str(tmp_path), str(tmp_path))
    assert windows.WindowsLauncher(tmp_path, path)._open(spec).startswith("started:")
    argv, kwargs = calls[0]
    assert argv[:5] == ["pwsh.exe", "-NoLogo", "-NoProfile", "-NoExit", "-EncodedCommand"]
    script = base64.b64decode(argv[5]).decode("utf-16-le")
    assert "quote'' $(New-Item NEVER).toml'" in script
    assert "'_resume' 'a/one'" in script
    assert kwargs == {"cwd": str(tmp_path), "creationflags": 16}
    assert not (tmp_path / "NEVER").exists()


def test_windows_copy_command_escapes_powershell_literals(tmp_path, monkeypatch):
    from cboard.terminals import command
    monkeypatch.setattr(command, "sys", SimpleNamespace(platform="win32"))
    spec = ResumeSpec(ThreadKey("a", "one"), ("C:\\codex.exe", "resume", "a'$(evil)"), str(tmp_path), "C:\\Joe's home")
    assert spec.command == "$env:CODEX_HOME='C:\\Joe''s home'; & 'C:\\codex.exe' 'resume' 'a''$(evil)'"


async def test_empty_board_starts_without_fake_connected_health(tmp_path):
    from cboard.ui.app import CboardApp
    config = load_config(tmp_path / "missing.toml")
    config = replace(config, accounts=(), state_dir=tmp_path / "state", cache_dir=tmp_path / "cache")
    app = CboardApp(config)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "등록된 Cboard 계정 없음" in str(app.query_one("#health").render())
        assert app.board.tasks == {}


def test_windows_file_lock_unlocks_on_failure(tmp_path, monkeypatch):
    import os
    import sys
    from cboard import locking
    calls = []
    fake = SimpleNamespace(LK_LOCK=1, LK_UNLCK=2, locking=lambda fd, mode, size: calls.append((mode, size)))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(locking, "os", SimpleNamespace(name="nt", SEEK_END=os.SEEK_END))
    path = tmp_path / "state.lock"
    with pytest.raises(RuntimeError):
        with locking.file_lock(path):
            raise RuntimeError("stop")
    assert calls == [(1, 1), (2, 1)]
    assert path.read_bytes() == b"\0"


def test_manager_registry_is_read_without_adding_accounts(tmp_path, monkeypatch):
    registry = tmp_path/'accounts.toml'
    registry.write_text('version = 1\n[[accounts]]\nid = "personal"\nlabel = "Personal"\nhome = "~/codex-personal"\n', encoding='utf-8')
    monkeypatch.setenv('CENV_REGISTRY', str(registry))
    config = load_config(tmp_path/'missing.toml')
    assert [a.id for a in config.accounts] == ['personal']
    explicit = tmp_path/'explicit.toml'
    explicit.write_text('accounts = []\n', encoding='utf-8')
    assert load_config(explicit).accounts == ()
