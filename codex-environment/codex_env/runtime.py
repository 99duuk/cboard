"""Profile selection and native execution, without changing native configuration."""
import json
import os
from pathlib import Path
import subprocess
import signal
import sys

from . import core
from .transaction import Change, Transaction, fingerprint


def find_account(name):
    for account in core.load_accounts():
        if name == account['id'] or name in account['aliases']:
            return account
    raise ValueError(f'Unknown account: {name}; run clist or cnew')


def default_account():
    path = core.local_dir()/'default.json'
    if not path.exists():
        return ''
    value = json.loads(path.read_text()).get('account')
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Invalid default account setting')
    return find_account(value)['id']


def select(name):
    if os.environ.get('CODEX_HOME'):
        raise ValueError('CODEX_HOME overrides shell selection; unset CODEX_HOME before cuse')
    return find_account(name)['id']


def set_default(name=None, dry_run=False):
    registry = core.ROOT/'accounts.toml'
    observed = fingerprint(registry)
    account = find_account(name)['id'] if name else None
    path = core.local_dir()/'default.json'
    changes = [Change(registry, content=registry.read_bytes(), mode=registry.stat().st_mode & 0o777, expected=observed),
               Change(path, content=(json.dumps({'account': account})+'\n').encode(), expected=fingerprint(path))]
    Transaction(core.state_dir()).apply(changes, dry_run=dry_run)
    return 0


def resolve(name=None):
    if name:
        account = find_account(name)
        return account, core.account_home(account), 'explicit'
    external = os.environ.get('CODEX_HOME')
    if external:
        home = Path(external).expanduser()
        account = next((a for a in core.load_accounts() if core.account_home(a).resolve() == home.resolve()), None)
        return account, home, 'CODEX_HOME'
    selected = os.environ.get('CENV_ACCOUNT')
    if selected:
        account = find_account(selected)
        return account, core.account_home(account), 'shell'
    default = os.environ.get('CENV_DEFAULT_ACCOUNT') if 'CENV_DEFAULT_ACCOUNT' in os.environ else default_account()
    if default:
        account = find_account(default)
        return account, core.account_home(account), 'default'
    return None, None, 'none'


def current():
    account, home, reason = resolve()
    if home is None:
        print('No account selected; use cnew, cuse NAME or cdefault NAME')
    else:
        print(f'{account["id"] if account else "external"}\t{home}\t{reason}')
    return 0


def run(name=None, args=()):
    account, home, reason = resolve(name)
    if home is None:
        raise ValueError('No account selected; use cnew, cuse NAME or cdefault NAME')
    env = dict(os.environ, CODEX_HOME=str(home))
    if account and os.environ.get('TERM_PROGRAM') == 'iTerm.app' and sys.stdout.isatty() and sys.stderr.isatty():
        profile, color = account.get('profile'), account.get('color')
        if profile:
            print(f'\033]1337;SetProfile={profile}\a', end='', file=sys.stderr, flush=True)
        if color:
            print(f'\033]1337;SetColors=bg={color}\a', end='', file=sys.stderr, flush=True)
    windows = sys.platform == "win32"
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if windows else {}
    child = subprocess.Popen([core.resolve_cli(), *args], env=env, **options)
    previous = {}
    def forward(signum, frame):
        if child.poll() is None:
            child.send_signal(signum)
    for signum in ((signal.SIGTERM,) if windows else (signal.SIGTERM, signal.SIGHUP)):
        previous[signum] = signal.signal(signum, forward)
    try:
        result = child.wait()
    except KeyboardInterrupt:
        if child.poll() is None:
            child.send_signal(signal.CTRL_BREAK_EVENT if windows else signal.SIGINT)
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return 130
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return result if result >= 0 else 128-result
