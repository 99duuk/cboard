"""Directory grouping for recent-session navigation, independent of the UI."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

RECENT_SECONDS = 14 * 86400
UNASSIGNED = "unassigned"


@lru_cache(maxsize=2048)
def workspace_root(cwd: str) -> str:
    if not cwd:
        return UNASSIGNED
    path = Path(os.path.abspath(os.path.expanduser(cwd)))
    home = Path.home()
    if path == home:
        return str(home)
    for name in ("Desktop", "Downloads"):
        root = home / name
        if path.is_relative_to(root):
            return str(root)
    projects = home / "Documents/workspace"
    if path.is_relative_to(projects) and path != projects:
        return str(projects / path.relative_to(projects).parts[0])
    for ancestor in (path, *path.parents):
        if ancestor.name.endswith("-workspace"):
            return str(ancestor)
    for ancestor in (path, *path.parents):
        try:
            if (ancestor / ".git").exists():
                return str(ancestor)
        except OSError:
            continue
    return str(path)


def workspace_name(root: str) -> str:
    if root == UNASSIGNED:
        return "작업 경로 없음"
    if Path(root) == Path.home():
        return "홈(루트)"
    return Path(root).name or "/"


def workspace_path(root: str) -> str:
    if root == UNASSIGNED:
        return "경로 정보 없음"
    path = Path(root)
    home = Path.home()
    if path == home:
        return "~/"
    return "~/" + str(path.relative_to(home)) if path.is_relative_to(home) else root


def workspace_label(root: str) -> str:
    if root == UNASSIGNED:
        return workspace_name(root)
    return f"{workspace_name(root)} · {workspace_path(root)}"


def workspace_relative(cwd: str, root: str) -> str:
    if not cwd or root == UNASSIGNED:
        return "—"
    path = Path(os.path.abspath(os.path.expanduser(cwd)))
    return str(path.relative_to(root)) if path.is_relative_to(root) else cwd
