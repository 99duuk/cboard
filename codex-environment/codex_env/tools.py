"""Explicit provider installation; no plugin work during shell startup or config sync."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from . import core


def _sources():
    return core.read_toml(core.ROOT / 'sources.toml').get('sources', [])


def node_path():
    settings = core.read_toml(core.local_dir() / 'local.toml')
    config = core.read_toml(Path.home() / '.codex/config.toml')
    candidates = ([settings['node']] if settings.get('node') else
                  [shutil.which('node'), config.get('mcp_servers', {}).get('rea', {}).get('command')])
    for candidate in candidates:
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
                return str(path)
    raise ValueError('Node missing; install a supported Node release or set node in local.toml')


def rea_config():
    if not any(s.get('kind') == 'npm' and s.get('name') == 'rea-agents' for s in _sources()):
        return {}
    return {'mcp_servers': {'rea': {
        'command': node_path(),
        'args': ['${HOME}/.local/share/rea/node_modules/rea-agents/scripts/rea.mjs', 'mcp']}}}


def _rea_source():
    return next((s for s in _sources() if s.get('kind') == 'npm' and s.get('name') == 'rea-agents'), None)


def _rea_ready(source):
    package = Path.home() / '.local/share/rea/node_modules/rea-agents/package.json'
    if not package.is_file():
        return False
    metadata = json.loads(package.read_text())
    return (metadata.get('version') == source.get('version')
            and (package.parent / 'scripts/rea.mjs').is_file())


def _plugin_source():
    from .assets import tree_hash
    source = next((s for s in _sources() if s.get('kind') == 'plugin'
                   and s.get('name') == 'ponytail@ponytail' and s.get('enabled')), None)
    if not source:
        return None
    if not re.fullmatch(r'\d+\.\d+\.\d+', source.get('version', '')):
        raise ValueError('Ponytail requires a fixed plugin version')
    for key in ('path', 'marketplace'):
        path = (core.ROOT / source.get(key, '')).resolve()
        if not source.get(key) or not path.is_relative_to(core.ROOT.resolve()) or not path.is_dir():
            raise ValueError(f'Ponytail canonical {key} missing or outside deployment root')
    plugin = core.ROOT / source['path']
    metadata = json.loads((plugin / '.codex-plugin/plugin.json').read_text())
    if metadata.get('version') != source['version'] or tree_hash(plugin) != source.get('sha256'):
        raise ValueError('Ponytail canonical source fingerprint/version mismatch')
    marketplace = core.ROOT / source['marketplace'] / '.agents/plugins/marketplace.json'
    descriptor = json.loads(marketplace.read_text())
    entries = descriptor.get('plugins', [])
    if (descriptor.get('name') != 'ponytail' or len(entries) != 1
            or entries[0].get('name') != 'ponytail'
            or entries[0].get('source', {}).get('source') != 'local'
            or (marketplace.parents[2] / entries[0]['source']['path']).resolve() != plugin.resolve()):
        raise ValueError('Ponytail marketplace must select only its canonical local source')
    return source


def _plugin_ready(home, source):
    from .assets import tree_hash
    installed = core.read_toml(home / 'config.toml').get('plugins', {}).get(source['name'], {})
    directory = home / 'plugins/cache/ponytail/ponytail' / source['version']
    return (installed.get('enabled') is True and directory.is_dir()
            and tree_hash(directory) == source['sha256'])


def tool_plan(accounts):
    """Return argv and CODEX_HOME for enabled, explicitly supported user tools only."""
    sources = _sources()
    plan = []
    rea = _rea_source()
    if rea and not _rea_ready(rea):
        version = rea.get('version', '')
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
            raise ValueError('REA source requires a fixed npm version')
        npm = shutil.which('npm')
        if not npm:
            raise ValueError('npm missing; install npm alongside Node')
        plan.append({'account': None, 'argv': [npm, 'install', '--prefix', str(Path.home()/'.local/share/rea'),
                                               '--no-audit', '--no-fund', f'rea-agents@{version}'],
                     'reason': 'REA version missing or inconsistent'})
    source = _plugin_source()
    if source:
        cli = core.resolve_cli()
        marketplace = str(core.ROOT / source['marketplace'])
        for account in accounts:
            home = core.account_home(account)
            if _plugin_ready(home, source):
                continue
            # Per-command local override preserves all existing provider registrations.
            plan.append({'account': account['id'], 'home': str(home),
                         'argv': [cli, '-c', 'marketplaces.ponytail.source_type="local"',
                                  '-c', 'marketplaces.ponytail.source=' + json.dumps(marketplace),
                                  'plugin', 'add', source['name']],
                         'reason': f"Ponytail {source['version']} from verified canonical source"})
    return plan


def tools_status(accounts):
    problems = []
    rea = _rea_source()
    if rea and not _rea_ready(rea):
        problems.append(f"REA {rea['version']}: package missing or inconsistent; run cenv tools install")
    try:
        node_path()
    except ValueError as error:
        if rea:
            problems.append(str(error))
    try:
        source = _plugin_source()
        if source:
            for account in accounts:
                if not _plugin_ready(core.account_home(account), source):
                    problems.append(f"{account['id']}: Ponytail {source['version']} missing or inconsistent; run cenv tools install")
    except (ValueError, OSError) as error:
        problems.append(str(error))
    return problems


def install_tools(accounts, dry_run=False):
    """Run providers after private config backups; preserve backups on partial failure.

    Provider caches are owned by providers. A backup is diagnostic recovery material,
    not a promise to reverse their side effects or concurrent app writes.
    """
    plan = tool_plan(accounts)
    for entry in plan:
        print(json.dumps({'account': entry['account'], 'argv': entry['argv']}, ensure_ascii=False))
    if dry_run or not plan:
        return 0
    node = node_path() if _rea_source() else None
    if node:
        result = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10, check=True)
        match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)\s*', result.stdout)
        if not match:
            raise ValueError('Unrecognized Node version')
        major, minor, patch = map(int, match.groups())
        if not ((major == 22 and (minor, patch) >= (19, 0)) or (major == 24 and (minor, patch) >= (11, 0)) or major >= 26):
            raise ValueError('REA 6.5.0 requires Node ^22.19.0 || ^24.11.0 || >=26')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    backup = core.state_dir() / 'tools' / stamp
    backup.mkdir(parents=True, mode=0o700)
    seen = set()
    for entry in plan:
        if entry.get('home') and entry['home'] not in seen:
            seen.add(entry['home'])
            original = Path(entry['home']) / 'config.toml'
            if original.exists():
                core.private_write(backup / (entry['account']+'.toml'), original.read_bytes())
    core.private_write(backup / 'plan.json', (json.dumps(plan, indent=2)+'\n').encode())
    print(f'Provider configuration backups: {backup}')
    for entry in plan:
        env = os.environ.copy()
        if entry.get('home'):
            env['CODEX_HOME'] = entry['home']
        if node:
            env['PATH'] = str(Path(node).parent) + os.pathsep + env.get('PATH', '')
        try:
            subprocess.run(entry['argv'], env=env, timeout=600, check=True)
        except (subprocess.SubprocessError, OSError) as error:
            print(f'Provider installation stopped: {error}; backups retained at {backup}')
            return 1
    remaining = tools_status(accounts)
    for problem in remaining:
        print(problem)
    return 1 if remaining else 0
