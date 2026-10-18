#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -x "$project_dir/.venv/bin/python" ]; then
    python3 -m venv "$project_dir/.venv"
fi
"$project_dir/.venv/bin/python" -m pip install -r "$project_dir/requirements-runtime.lock"
"$project_dir/.venv/bin/python" -m pip install --no-deps -e "$project_dir"
command_dir="$HOME/.codex/bin"
mkdir -p "$command_dir"
if [ -e "$command_dir/cboard" ] || [ -L "$command_dir/cboard" ]; then
    if [ "$(readlink "$command_dir/cboard")" != "$project_dir/.venv/bin/cboard" ]; then
        printf '%s\n' "Existing cboard command was not changed: $command_dir/cboard" >&2
        exit 1
    fi
else
    ln -s "$project_dir/.venv/bin/cboard" "$command_dir/cboard"
fi
printf '%s\n' "Installed: $command_dir/cboard"
