from __future__ import annotations

import sys
from pathlib import Path


def terminal_launcher(state_dir: Path, config_path: Path | None = None):
    if sys.platform == "win32":
        from .windows import WindowsLauncher
        return WindowsLauncher(state_dir, config_path)
    from .iterm import ItermLauncher
    return ItermLauncher(state_dir, config_path)
