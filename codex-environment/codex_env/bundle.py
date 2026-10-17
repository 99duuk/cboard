"""Allowlisted, explicit-scope portable bundles without runtime identity or credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
from urllib.parse import parse_qsl, urlsplit

from . import core

_SECRET = re.compile(r'\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{32,}|glpat-[A-Za-z0-9_-]{20,})\b')
_HOME = re.compile(r'(?<![A-Za-z0-9_.-])/(?:Users|home)/[^/\s"\'<>]+')
_IGNORED = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '.git')


def _validate_values(value, location):
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r'token|password|secret|api[_-]?key', key, re.I) or key.lower() in {'authorization', 'proxy-authorization', 'cookie', 'set-cookie'}:
                raise ValueError('credential-bearing key in export: '+location+'.'+key)
            _validate_values(child, location+'.'+key)
    elif isinstance(value, list):
        for child in value:
            _validate_values(child, location)
    elif isinstance(value, str):
        if re.search(r'(?:authorization|cookie)\s*[:=]|--(?:api[-_]key|token|password|secret)(?:=|$)', value, re.I):
            raise ValueError('credential-bearing value in export: '+location)
        if value.startswith(('https://', 'http://')):
            url = urlsplit(value)
            if url.username or url.password or any(re.search(r'token|secret|password|api[_-]?key|signature', key, re.I) for key, _ in parse_qsl(url.query)):
                raise ValueError('credential-bearing URL in export: '+location)


def _sensitive(common, assets):
    items = ['config/common.toml:'+'.'.join(key) for key in core.flatten(common)
             if any(part in {'approval_policy', 'approvals_reviewer', 'sandbox_mode', 'sandbox_workspace_write', 'permissions', 'shell_environment_policy'} for part in key)]
    rules = assets/'rules'
    if rules.exists():
        items += ['assets/'+str(path.relative_to(assets)) for path in sorted(rules.rglob('*')) if path.is_file()]
    return items


def _copy(source, destination):
    if not source.exists():
        raise ValueError('required export source missing: '+str(source))
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, ignore=_IGNORED)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def export_bundle(output, include_accounts=False, include_shared=False, yes=False, dry_run=False):
    include_shared = include_shared or include_accounts
    target = Path(output).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('output already exists; choose a new archive path')
    accounts = core.load_accounts() if include_accounts else []
    common = core.read_toml(core.ROOT/'config/common.toml') if include_shared else {}
    sources = core.read_toml(core.ROOT/'sources.toml') if include_shared else {'version': 1, 'sources': []}
    if include_shared:
        core.desired_config({'id': '_export'}, account_override={}, shared=(core.flatten(core.expand(common)), {}))
        _validate_values(sources, 'sources.toml')
    from .tools import _plugin_source
    provider = _plugin_source() if include_shared else None
    provider_prefix = provider['path'].rstrip('/')+'/' if provider else None
    defaults = core.read_toml(core.ROOT/'config/defaults.toml') if include_shared else {}
    defaults = defaults or {'approval_policy': 'on-request', 'sandbox_mode': 'workspace-write', 'approvals_reviewer': 'user'}
    policies = _sensitive(common, core.ROOT/'assets') if include_shared else []
    policy_sources = [('config/defaults.toml', defaults)] if include_shared else []
    policy_sources += [(f'config/accounts/{account["id"]}.toml', core.read_toml(core.ROOT/f'config/accounts/{account["id"]}.toml')) for account in accounts]
    for location, values in policy_sources:
        policies += [location+':'+'.'.join(key) for key in core.flatten(values)
                     if any(part in {'approval_policy','approvals_reviewer','sandbox_mode','sandbox_workspace_write','permissions','shell_environment_policy'} for part in key)]
    dependencies = sources.get('sources', [])
    mode = 'accounts' if include_accounts else 'shared' if include_shared else 'toolkit'
    print('Bundle scope: '+mode)
    if include_shared:
        for path in sorted((core.ROOT/'assets').rglob('*')):
            if path.is_file():
                print('Include: assets/'+str(path.relative_to(core.ROOT/'assets')))
        for dependency in dependencies:
            print('Dependency: '+str(dependency.get('name', 'unnamed'))+' / '+str(dependency.get('status', 'unspecified')))
    for policy in policies:
        print('Permission review: '+policy)
    if policies and not dry_run and not yes:
        if not sys.stdin.isatty():
            raise ValueError('shared rules or permission settings require review; inspect --dry-run then pass --yes')
        if input('Include the listed rules and permission settings? [y/N] ').strip().lower() not in {'y', 'yes'}:
            raise KeyboardInterrupt
    with tempfile.TemporaryDirectory(prefix='cenv-export-') as directory:
        package = Path(directory)/'codex-environment'
        package.mkdir()
        for name in ['bin', 'codex_env', 'vendor', 'tests']:
            _copy(core.ROOT/name, package/name)
        for name in ['config/accounts', 'shell', 'assets/rules', 'assets/skills', 'assets/agents']:
            (package/name).mkdir(parents=True, exist_ok=True)
        (package/'README.md').write_text('# Codex environment manager\n\n- macOS and zsh account environment management\n- Start: `bin/cenv --help`\n- Install: `bin/cenv install`\n- New account: `cnew personal` in a new terminal\n- Credentials and session history excluded\n')
        (package/'SPEC.md').write_text('# Portable bundle contract\n\n- Scope and dependency inventory: BUNDLE-INFO.json\n- Integrity inventory: MANIFEST.json\n- Runtime authentication and machine state excluded\n')
        (package/'assets/AGENTS.md').write_text('')
        if include_shared:
            shutil.rmtree(package/'assets')
            _copy(core.ROOT/'assets', package/'assets')
        (package/'assets-manifest.json').write_text(json.dumps({'assets': []})+'\n')
        (package/'config/common.toml').write_bytes(core.dump(common))
        (package/'config/defaults.toml').write_bytes(core.dump(defaults))
        (package/'sources.toml').write_bytes(core.dump(sources))
        registry = {'version': 1, 'accounts': []}
        for account in accounts:
            entry = dict(account)
            home = core.account_home(account)
            if not home.is_relative_to(Path.home()):
                raise ValueError('account home outside user home; use a portable ~/ path before export: '+account['id'])
            entry['home'] = '~/'+str(home.relative_to(Path.home()))
            registry['accounts'].append(entry)
            config = core.read_toml(core.ROOT/f'config/accounts/{account["id"]}.toml')
            core.desired_config(account, account_override=config, shared=(core.flatten(core.expand(common)), {}))
            (package/f'config/accounts/{account["id"]}.toml').write_bytes(core.dump(config))
        (package/'accounts.toml').write_bytes(core.dump(registry))
        default = core.local_dir()/'default.json'
        if include_accounts and default.exists():
            data = json.loads(default.read_text())
            if not isinstance(data, dict) or set(data)-{'account'} or data.get('account') not in {None, *(a['id'] for a in accounts)}:
                raise ValueError('invalid default account state; repair the default before export')
            (package/'config/default.json').write_text(json.dumps(data)+'\n')
        for path in (package/'assets/rules').rglob('*.rules'):
            path.write_text(path.read_text().replace(str(Path.home()), '${HOME}'))
        files = []
        for path in sorted(package.rglob('*')):
            relative = str(path.relative_to(package))
            if path.is_symlink():
                # No symbolic links, including dangling links, in distributable archives.
                raise ValueError('symlink in export; capture a regular asset first: '+relative)
            if not path.is_file():
                continue
            public_provider = bool(provider_prefix and relative.startswith(provider_prefix))
            public_example = public_provider and path.name == '.env.example'
            if path.name in {'.env', 'auth.json', 'credentials.json'} or (path.name.startswith('.env.') and not public_example):
                raise ValueError('credential-like file in export: '+relative)
            text = path.read_bytes().decode('utf-8', errors='ignore')
            if _SECRET.search(text):
                raise ValueError('credential pattern in export file: '+relative)
            personal_source = relative.startswith(('assets/', 'config/')) and not public_provider
            if personal_source and (_HOME.search(text) or str(Path.home()) in text):
                raise ValueError('personal home path in export: '+relative+'; replace it with ${HOME} or a portable relative path')
            if relative.startswith('config/') and path.suffix == '.toml':
                _validate_values(core.read_toml(path), relative)
            files.append(relative)
        info = {'version': 1, 'scope': mode, 'files': files, 'dependencies': dependencies,
                'permission_review': policies,
                'caveat': 'Pattern checks do not guarantee complete anonymization; review shared instructions, policies and service dependencies before distribution.'}
        (package/'BUNDLE-INFO.json').write_text(json.dumps(info, indent=2)+'\n')
        hashes = {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(package.rglob('*')) if p.is_file()}
        (package/'MANIFEST.json').write_text(json.dumps({'version': 1, 'files': hashes}, indent=2)+'\n')
        if dry_run:
            print('Dry run: validated '+str(len(hashes))+' files; archive not written.')
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
                archive.add(package, arcname='codex-environment', recursive=True)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    print('Exported: '+str(target))
    print('Authentication, histories, machine state and caches excluded.')
    return 0
