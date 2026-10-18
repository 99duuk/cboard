"""Resolve existing Codex or install the checksum-pinned official native package."""
import argparse
import contextlib
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


def install(destination, entry):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('native destination already exists')
    if not entry['url'].startswith('https://registry.npmjs.org/@openai/codex/-/'):
        raise ValueError('unexpected native package origin')
    algorithm, expected = entry['integrity'].split('-', 1)
    if algorithm != 'sha512':
        raise ValueError('native package SHA-512 required')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cboard-native-', dir=destination.parent) as temporary:
        root = Path(temporary)
        archive = root/'package.tgz'
        digest = hashlib.sha512()
        size = 0
        with urllib.request.urlopen(entry['url'], timeout=30) as response, archive.open('xb') as stream:
            while chunk := response.read(1024*1024):
                size += len(chunk)
                if size > 300*1024*1024:
                    raise ValueError('native package download exceeds 300 MiB')
                digest.update(chunk)
                stream.write(chunk)
        if digest.digest() != base64.b64decode(expected, validate=True):
            raise ValueError('native package checksum mismatch')
        extracted = root/'native'
        extracted.mkdir()
        with tarfile.open(archive) as package:
            members = package.getmembers()
            total = 0
            for item in members:
                path = PurePosixPath(item.name)
                if path.is_absolute() or '..' in path.parts or '\\' in item.name or ':' in item.name:
                    raise ValueError('unsafe native package path')
                if not (item.isdir() or item.isfile()):
                    raise ValueError('native package links/special files are not supported')
                total += item.size
                if total > 600*1024*1024:
                    raise ValueError('native package expands beyond 600 MiB')
            for item in members:
                target = extracted.joinpath(*PurePosixPath(item.name).parts)
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(item) as src, target.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(item.mode & 0o777)
        candidates = list(extracted.glob('package/vendor/*-pc-windows-msvc/bin/codex.exe'))
        candidates += list(extracted.glob('package/vendor/*-pc-windows-msvc/codex/codex.exe'))
        candidates += list(extracted.glob('package/vendor/*-unknown-linux-*/bin/codex'))
        candidates += list(extracted.glob('package/vendor/*-unknown-linux-*/codex/codex'))
        if len(candidates) != 1:
            raise ValueError('expected one native Codex executable')
        relative = candidates[0].relative_to(extracted)
        shutil.move(str(extracted), str(destination))
    return destination/relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--save-cli', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.manager))
    from codex_env import core
    try:
        executable = Path(core.resolve_cli())
    except ValueError:
        entries = json.loads(Path(__file__).with_name('codex-native.json').read_text(encoding='utf-8'))
        arch = platform.machine().lower()
        if arch not in ('amd64', 'x86_64', 'arm64', 'aarch64'):
            raise ValueError('x64 or ARM64 required')
        family = 'linux-' if sys.platform.startswith('linux') else '' if sys.platform == 'win32' else None
        if family is None:
            raise ValueError('Automatic Codex download supports Windows or Linux')
        executable = install(args.destination, entries[family+('arm64' if arch in ('arm64', 'aarch64') else 'x64')])
    result = subprocess.run([str(executable), '--version'], capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('native Codex version check failed')
    if args.save_cli:
        from codex_env.transaction import Change, Transaction, fingerprint
        path = core.local_dir()/'local.toml'
        document = core.tomlkit.parse(path.read_text(encoding='utf-8-sig')) if path.exists() else core.tomlkit.document()
        document['cli'] = str(executable)
        with contextlib.redirect_stdout(sys.stderr):
            Transaction(core.state_dir()).apply([Change(path, content=core.tomlkit.dumps(document).encode(), expected=fingerprint(path))])
    print(json.dumps(str(executable)))


if __name__ == '__main__':
    main()
