"""Build small, account-free runtime archives from the current working tree."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    files = {}
    for directory in ('src/cboard', 'codex-environment/bin', 'codex-environment/codex_env',
                      'codex-environment/vendor'):
        for path in sorted((ROOT / directory).rglob('*')):
            if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
                continue
            if path.is_symlink():
                raise ValueError(f'Symlink in runtime: {path.relative_to(ROOT)}')
            if path.is_file():
                files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ('pyproject.toml', 'requirements-runtime.lock', 'README.md'):
        files[name] = (ROOT / name).read_bytes()
    # Distributable defaults never come from the installing user's registry or assets.
    defaults = {
        'accounts.toml': 'version = 1\naccounts = []\n',
        'sources.toml': 'version = 1\nsources = []\n',
        'config/common.toml': '',
        'config/defaults.toml': 'approval_policy = "on-request"\nsandbox_mode = "workspace-write"\napprovals_reviewer = "user"\n',
        'assets/AGENTS.md': '', 'assets-manifest.json': '{"assets": []}\n',
        'BUNDLE-INFO.json': '{"version": 1, "scope": "toolkit"}\n',
    }
    for directory in ('config/accounts', 'shell', 'tools', 'assets/rules', 'assets/skills', 'assets/agents'):
        defaults[directory + '/.gitkeep'] = ''
    files.update({'codex-environment/' + name: content.encode() for name, content in defaults.items()})
    return files


def manifest(files, commit, dirty):
    return (json.dumps({
        'source_commit': commit, 'working_tree_snapshot': dirty,
        'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(files.items())],
    }, indent=2) + '\n').encode()


def windows_files(base, commit, dirty):
    files = dict(base)
    helpers = {}
    for path in sorted((ROOT / 'scripts/windows').iterdir()):
        if path.suffix not in {'.py', '.ps1', '.cmd', '.json', '.md'} or path.name == 'build_bundle.py':
            continue
        content = path.read_text(encoding='utf-8-sig')
        files['scripts/windows/' + path.name] = content.encode()
        if path.suffix == '.ps1':
            helpers[path.name] = b'\xef\xbb\xbf' + content.replace('\n', '\r\n').encode()
        elif path.suffix == '.cmd':
            helpers[path.name] = content.replace('\n', '\r\n').encode('ascii')
        else:
            helpers[path.name] = content.encode()
    return {**{'source/' + name: data for name, data in files.items()},
            'MANIFEST.json': manifest(files, commit, dirty), **helpers}


def linux_files(base, commit, dirty):
    files = dict(base)
    for name in ('scripts/install-linux.sh', 'scripts/windows/install_native.py', 'scripts/windows/codex-native.json'):
        files[name] = (ROOT / name).read_bytes()
    files['MANIFEST.json'] = manifest(files, commit, dirty)
    return files


def validate(files):
    for name in files:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError(f'Unsafe archive name: {name}')
        if any(part in {'.git', '.venv', '__pycache__', 'tests', 'artifacts', 'evidence'} for part in path.parts):
            raise ValueError(f'Non-runtime file: {name}')
        if path.name in {'auth.json', 'credentials.json', '.env'}:
            raise ValueError(f'Credential-like file: {name}')
    for name, data in files.items():
        if name.endswith('accounts.toml') and data != b'version = 1\naccounts = []\n':
            raise ValueError('Only an empty account registry may be published')


def build(platform='all', require_clean=False):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if require_clean and dirty:
        raise ValueError('Release requires a clean working tree')
    base = source_files()
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    results = []
    if platform in {'all', 'windows'}:
        files = windows_files(base, commit, dirty)
        validate(files)
        target = output / 'cboard-windows.zip'
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(files):
                raise ValueError('Windows archive integrity failure')
            if any(archive.read(name) != data for name, data in files.items()):
                raise ValueError('Windows archive content mismatch')
        results.append(target)
    if platform in {'all', 'linux'}:
        files = linux_files(base, commit, dirty)
        validate(files)
        target = output / 'cboard-linux.tar.gz'
        with tarfile.open(target, 'w:gz', compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo('cboard-linux/' + name)
                info.size = len(data)
                info.mode = 0o755 if name.startswith('codex-environment/bin/') or name.endswith('.sh') else 0o644
                archive.addfile(info, io.BytesIO(data))
        with tarfile.open(target, 'r:gz') as archive:
            members = archive.getmembers()
            if {m.name for m in members} != {'cboard-linux/' + name for name in files}:
                raise ValueError('Linux archive inventory mismatch')
            if any(archive.extractfile('cboard-linux/' + name).read() != data for name, data in files.items()):
                raise ValueError('Linux archive content mismatch')
        results.append(target)
    (output / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n' for path in results))
    for path in results:
        print(f'{path}: {path.stat().st_size:,} bytes')
    print(f'Source: {commit}; working tree changes: {dirty}')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('all', 'windows', 'linux'), default='all')
    parser.add_argument('--require-clean', action='store_true')
    args = parser.parse_args()
    build(args.platform, args.require_clean)
