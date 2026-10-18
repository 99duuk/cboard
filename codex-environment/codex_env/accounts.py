"""Profile registration and removal; native credentials and histories stay provider-owned."""
import json
import argparse
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from datetime import datetime, timezone

from . import core
from .assets import asset_links
from .transaction import Change, Transaction, fingerprint


def find(name):
    for account in core.load_accounts():
        if name == account['id'] or name in account['aliases']:
            return account
    raise ValueError('unknown account: '+name)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False)+'\n').encode()


def guarded_registry(accounts, before):
    path = core.ROOT/'accounts.toml'
    return Change(path, content=core.dump({'version': 1, 'accounts': accounts}), expected=before)


def validate(accounts):
    with tempfile.TemporaryDirectory(prefix='cenv-registry-') as temporary:
        path = Path(temporary)/'accounts.toml'
        path.write_bytes(core.dump({'version': 1, 'accounts': accounts}))
        core.load_accounts(path)


def source_snapshot(accounts):
    paths = [core.ROOT/'config/defaults.toml', core.ROOT/'config/common.toml',
             core.ROOT/'sources.toml', core.local_dir()/'local.toml']
    paths += [core.ROOT/f'config/accounts/{a["id"]}.toml' for a in accounts]
    result = []
    for path in paths:
        observed = fingerprint(path)
        result.append(Change(path, content=path.read_bytes(), mode=path.stat().st_mode & 0o777, expected=observed)
                      if path.exists() else Change(path, remove=True, expected=observed))
    return result


def guard_sources(changes, snapshot):
    destinations = {change.path for change in changes}
    changes.extend(change for change in snapshot if change.path not in destinations)
    return changes


def register(args, importing=False):
    registry = core.ROOT/'accounts.toml'
    before = fingerprint(registry)
    accounts = core.load_accounts()
    snapshot = source_snapshot(accounts)
    if not core.NAME.fullmatch(args.name):
        raise argparse.ArgumentError(None, 'account name must use lowercase letters, numbers, underscore or hyphen')
    if any(a['id'] == args.name for a in accounts):
        raise ValueError(f'{args.name} already registered; use clist or clogin {args.name}')
    alias = args.alias or 'c'+args.name
    home = str(getattr(args, 'home', None) or ('~/.codex-'+args.name))
    account = {'id': args.name, 'label': alias, 'home': home, 'aliases': [alias],
               'profile': '', 'color': '000000', 'origin': 'imported' if importing else 'created'}
    if alias == 'codex':
        raise argparse.ArgumentError(None, 'codex is the selected-account command; choose a separate execution alias')
    new = accounts+[account]
    try:
        validate(new)
    except ValueError as error:
        raise argparse.ArgumentError(None, str(error)) from None
    native_home = core.account_home(account)
    if importing:
        if not native_home.is_dir():
            raise ValueError('import requires an existing account directory')
    elif os.path.lexists(native_home):
        raise ValueError(f'account home already exists; use cenv account import {args.name} --home {native_home}')
    source = core.ROOT/f'config/accounts/{args.name}.toml'
    if source.exists():
        raise ValueError('unregistered account source exists; inspect or restore before registering')
    defaults_path = core.ROOT/'config/defaults.toml'
    defaults_before = fingerprint(defaults_path)
    seed_defaults = core.read_toml(defaults_path) or {'approval_policy': 'on-request', 'sandbox_mode': 'workspace-write', 'approvals_reviewer': 'user'}
    if os.name == 'nt' and not importing:
        seed_defaults.setdefault('cli_auth_credentials_store', 'file')
    defaults = dict(seed_defaults)
    native = native_home/'config.toml'
    native_before = fingerprint(native)
    if importing:
        existing = core.read_toml(native)
        # Existing preferences/policy override defaults; common managed collisions need explicit adoption.
        keys = core.PREFERENCE_KEYS | {'approval_policy','sandbox_mode','approvals_reviewer','network_access'}
        defaults.update({k: existing[k] for k in keys if k in existing})
        desired = core.desired_config(account, defaults)
        current = core.flatten(existing)
        collisions = [key for key in desired if key in current and desired[key] != current[key]]
        for key in collisions:
            print('Import conflict: '+'.'.join(key))
        if collisions and not getattr(args, 'adopt_existing', False):
            raise ValueError('import conflict; resolve common source or explicitly --adopt-existing')
        for key in collisions:
            core.put(defaults, key, current[key])
        adopted = core.desired_config(account, defaults)
        remaining = [key for key in collisions if adopted[key] != current[key]]
        if remaining:
            raise ValueError('machine-owned import conflict; resolve local tool paths first: '+', '.join('.'.join(key) for key in remaining))
    changes = core.config_changes(new, {args.name: defaults})
    changes += [guarded_registry(new, before),
                Change(defaults_path, content=defaults_path.read_bytes() if defaults_path.exists() else core.dump(seed_defaults), expected=defaults_before),
                Change(source, content=core.dump(defaults), expected=fingerprint(source))]
    # Config planning fingerprints source/native files; guard the inspected native import snapshot too.
    if importing and fingerprint(native) != native_before:
        raise RuntimeError('native account changed during import; retry')
    if importing and native.exists() and not any(c.path == native for c in changes):
        changes.append(Change(native, content=native.read_bytes(), mode=native.stat().st_mode & 0o777, expected=native_before))
    links = asset_links(core.ROOT, Path.home(), new)
    if os.name == 'nt':
        from .windows import assets_changes, shell_changes
        changes += assets_changes(links)+shell_changes(new)
    else:
        for destination, target in links:
            if importing and destination.is_relative_to(native_home) and os.path.lexists(destination):
                if not destination.is_symlink() or destination.resolve() != target.resolve():
                    raise ValueError('existing import asset conflict; preserve/move before import: '+str(destination))
            changes.append(Change(destination, link=target, expected=fingerprint(destination)))
        changes += core.shell_changes(new)
    result = Transaction(core.state_dir()).apply(guard_sources(changes, snapshot), dry_run=args.dry_run)
    from .tools import install_tools
    if args.dry_run:
        return install_tools([account], dry_run=True)
    print(f'Registered {args.name}; alias {alias}; home {native_home}')
    print('Applied: '+str(result))
    print('Select with cuse '+args.name+'; set new-shell default with cdefault '+args.name)
    try:
        with Transaction(core.state_dir())._lock():
            status = install_tools([account])
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('Tools incomplete: '+str(error))
        status = 1
    if status:
        print('Registration retained; retry tools: cenv tools install')
    return status


def remove(args):
    registry = core.ROOT/'accounts.toml'
    before = fingerprint(registry)
    accounts = core.load_accounts()
    account = find(args.name)
    remaining = [a for a in accounts if a['id'] != account['id']]
    home = core.account_home(account)
    default = core.local_dir()/'default.json'
    default_before = fingerprint(default)
    default_value = json.loads(default.read_text()) if default.exists() else {}
    print(f'Unregister: {account["id"]}; aliases: {", ".join(account["aliases"])}')
    print('Preserve native credentials, config, sessions and histories: '+str(home))
    print('Running sessions remain running. Shared assets and other accounts remain installed.')
    if default_value.get('account') == account['id']:
        print('Clear default account: '+account['id'])
    if not args.dry_run and not args.yes:
        if not sys.stdin.isatty():
            raise argparse.ArgumentError(None, 'noninteractive removal requires --yes; preview with --dry-run')
        if input('Unregister this account? [y/N] ').strip().lower() not in {'y','yes'}:
            return 130
    source = core.ROOT/f'config/accounts/{account["id"]}.toml'
    source_before = fingerprint(source)
    archive = core.ROOT/f'config/removed/{account["id"]}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")}.json'
    record = {'account': account, 'settings': source.read_text() if source.exists() else '', 'native_home_preserved': str(home)}
    changes = [guarded_registry(remaining, before), Change(archive, content=json_bytes(record), expected=fingerprint(archive)),
               Change(source, remove=True, expected=source_before)]
    if os.name == 'nt':
        from .windows import shell_changes
        changes += shell_changes(remaining)
    else:
        changes += core.shell_changes(remaining)
    if default_value.get('account') == account['id']:
        changes.append(Change(default, content=json_bytes({'account': None}), expected=default_before))
    else:
        changes.append(Change(default, content=default.read_bytes() if default.exists() else json_bytes({'account': None}), expected=default_before))
    identities = core.local_dir()/'identities.json'
    identity_before = fingerprint(identities)
    if identities.exists():
        cached = json.loads(identities.read_text())
        cached.pop(account['id'], None)
        changes.append(Change(identities, content=json_bytes(cached), expected=identity_before))
    ledger = core.state_dir()/'managed.json'
    ledger_before = fingerprint(ledger)
    values = json.loads(ledger.read_text()) if ledger.exists() else {}
    values.pop(str(home/'config.toml'), None)
    changes.append(Change(ledger, content=json_bytes(values), expected=ledger_before))
    result = Transaction(core.state_dir()).apply(changes, dry_run=args.dry_run)
    if not args.dry_run:
        print('Unregistered; recovery record: '+str(archive))
        print(f'Register again: cenv account import {account["id"]} --home '+str(home))
        print('Applied: '+str(result))
    return 0
