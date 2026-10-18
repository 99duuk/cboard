#!/bin/sh
set -eu
# Downloaded alone: fetch one release archive, verify it, then use its installer.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -f "$project_dir/pyproject.toml" ] || [ ! -f "$project_dir/codex-environment/bin/cenv" ]; then
    release=https://github.com/99duuk/cboard/releases/latest/download
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT HUP INT TERM
    curl -fL --retry 2 "$release/cboard-linux.tar.gz" -o "$stage/cboard-linux.tar.gz"
    curl -fL --retry 2 "$release/SHA256SUMS" -o "$stage/SHA256SUMS"
    (cd "$stage" && awk '$2 == "cboard-linux.tar.gz" {print}' SHA256SUMS > selected.sha256 && test -s selected.sha256 && sha256sum -c selected.sha256)
    tar -xzf "$stage/cboard-linux.tar.gz" -C "$stage"
    sh "$stage/cboard-linux/scripts/install-linux.sh"
    exit $?
fi
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'This installer requires Linux.' >&2
    exit 1
fi
python=${CBOARD_PYTHON:-python3}
"$python" -I -c 'import sys,venv,ensurepip;sys.exit(0 if sys.version_info >= (3,11) else "Python 3.11+ required")'
base="$HOME/.local/share/cboard"
mkdir -p "$base"
# Exclude mutable manager state from replacement during an upgrade.
"$python" -I - "$project_dir" "$base/source" <<'PY'
from pathlib import Path
import shutil,sys
source,dest=map(Path,sys.argv[1:])
if source.resolve() != dest.resolve():
    dest.mkdir(parents=True,exist_ok=True)
    for name in ('src','scripts'):
        shutil.copytree(source/name,dest/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('pyproject.toml','requirements-runtime.lock'):
        shutil.copy2(source/name,dest/name)
    manager=dest/'codex-environment';manager.mkdir(exist_ok=True)
    for child in (source/'codex-environment').iterdir():
        target=manager/child.name
        if child.name in {'accounts.toml','config','assets','assets-manifest.json','sources.toml'} and target.exists():
            continue
        if child.is_dir():
            shutil.copytree(child,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        else:
            shutil.copy2(child,target)
PY
source_dir="$base/source"
if [ ! -x "$base/venv/bin/python" ]; then "$python" -I -m venv "$base/venv"; fi
runtime="$base/venv/bin/python"
"$runtime" -I -m pip install --disable-pip-version-check -r "$source_dir/requirements-runtime.lock"
"$runtime" -I -m pip install --disable-pip-version-check --no-deps "$source_dir"
# Kernel shebangs cannot quote an interpreter path containing spaces; use shell wrappers.
"$runtime" -I - "$source_dir/codex-environment/bin" "$runtime" <<'PY'
from pathlib import Path
import shlex,sys
folder=Path(sys.argv[1]);python=sys.argv[2]
for path in folder.iterdir():
    if path.suffix != '.py' and path.is_file() and path.read_bytes().startswith(b'#!/usr/bin/env python3\n'):
        program=path.with_suffix('.py')
        path.rename(program)
        path.write_text('#!/bin/sh\nexec '+shlex.quote(python)+' '+shlex.quote(str(program))+' "$@"\n')
        path.chmod(0o755)
PY
manager="$source_dir/codex-environment"
if ! "$runtime" -I - "$manager" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
from codex_env.core import resolve_cli
try: resolve_cli()
except ValueError: raise SystemExit(1)
PY
then
    "$runtime" -I "$source_dir/scripts/windows/install_native.py" --manager "$manager" --destination "$base/tools/codex" --save-cli
fi
"$manager/bin/cenv" install
mkdir -p "$HOME/.codex/bin"
"$runtime" -I - "$base" "$manager" <<'PYBOARD'
from pathlib import Path
import shlex,sys
base,manager=map(Path,sys.argv[1:])
wrapper=base/'cboard'
wrapper.write_text('#!/bin/sh\nexec env '+shlex.quote('CENV_REGISTRY='+str(manager/'accounts.toml'))+' '+shlex.quote(str(base/'venv/bin/python'))+' -I -m cboard "$@"\n')
wrapper.chmod(0o755)
PYBOARD
if [ -e "$HOME/.codex/bin/cboard" ] || [ -L "$HOME/.codex/bin/cboard" ]; then
    previous=$(readlink "$HOME/.codex/bin/cboard" || true)
    if [ "$previous" != "$base/cboard" ] && [ "$previous" != "$base/venv/bin/cboard" ]; then
        printf '%s\n' 'Existing ~/.codex/bin/cboard preserved; remove its conflict and rerun.' >&2
        exit 1
    fi
fi
ln -sfn "$base/cboard" "$HOME/.codex/bin/cboard"
printf '\n%s\n' 'Installed. No accounts added or login performed.' 'Open a new Bash/Zsh terminal, then run chelp or cusage.'
if [ -t 1 ] && [ -r /dev/tty ]; then
    printf '%s\n' 'Opening a ready shell; run cusage here.'
    case "${SHELL:-/bin/bash}" in
        */zsh) exec zsh -i < /dev/tty ;;
        *) exec bash -i < /dev/tty ;;
    esac
fi
