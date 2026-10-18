"""Explicit local environment management; ordinary Codex runs never sync settings."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata
import tempfile

from . import core
from .assets import capture_assets, asset_links
from .transaction import Change, Transaction, fingerprint


def save_new(path, data):
    if not path.exists():
        core.private_write(path, data)


def localize_rules(root):
    # Rule syntax has no home expansion; exported placeholders are materialized once.
    for path in (root/'assets/rules').rglob('*.rules'):
        content = path.read_text()
        if '${HOME}' in content:
            path.write_text(content.replace('${HOME}', str(Path.home())))


@contextlib.contextmanager
def prepared(dry_run=False):
    original = core.ROOT
    portable_rules = any('${HOME}' in p.read_text() for p in (original/'assets/rules').rglob('*.rules'))
    if dry_run and (not (original/'accounts.toml').exists() or not (original/'assets-manifest.json').exists() or portable_rules):
        with tempfile.TemporaryDirectory(prefix='cenv-plan-') as name:
            stage = Path(name)/'bundle'
            stage.mkdir()
            for name in ['accounts.toml', 'sources.toml', 'BUNDLE-INFO.json']:
                if (original/name).exists():
                    shutil.copy2(original/name, stage/name)
            for name in ['config', 'assets']:
                if (original/name).exists():
                    shutil.copytree(original/name, stage/name)
            core.ROOT = stage
            try:
                core.bootstrap()
                capture_assets(stage, Path.home())
                localize_rules(stage)
                yield original, stage
            finally:
                core.ROOT = original
    else:
        core.bootstrap()
        if not (core.ROOT/'assets-manifest.json').exists():
            captured = capture_assets(core.ROOT, Path.home())
            save_new(core.ROOT/'assets-manifest.json', (json.dumps(captured, indent=2, ensure_ascii=False)+'\n').encode())
        localize_rules(core.ROOT)
        yield original, original


def translate(changes, stage, real):
    if stage == real:
        return changes
    for change in changes:
        previous_path = change.path
        change.path = Path(str(change.path).replace(str(stage), str(real)))
        if change.link:
            change.link = Path(str(change.link).replace(str(stage), str(real)))
        if change.content is not None:
            change.content = change.content.replace(str(stage).encode(), str(real).encode())
        if change.path != previous_path:
            change.expected = fingerprint(change.path)
    return changes


def sync(dry_run=False, install=False):
    with prepared(dry_run) as (real, stage):
        registry = core.ROOT/'accounts.toml'
        registry_before = fingerprint(registry)
        registry_bytes = registry.read_bytes()
        accounts = core.load_accounts()
        native_home = Path.home()/'.codex'
        if any((native_home/name).exists() for name in ['config.toml','auth.json']) and not any(core.account_home(a).resolve() == native_home.resolve() for a in accounts):
            print('WARN unregistered native Codex home preserved: '+str(native_home))
            print('Register it explicitly before managed execution: cenv account import existing --home '+str(native_home))
        if fingerprint(registry) != registry_before:
            raise ValueError('account registry changed during planning; retry sync')
        from .accounts import source_snapshot, guard_sources
        snapshot = source_snapshot(accounts)
        changes = core.install_changes(accounts)
        default_path = core.local_dir()/'default.json'
        if not default_path.exists():
            portable = core.ROOT/'config/default.json'
            old_shell = Path.home()/'.zshrc'
            migrated = old_shell.exists() and core.BEGIN in old_shell.read_text()
            if portable.exists():
                value = json.loads(portable.read_text())
                if not isinstance(value, dict) or set(value)-{'account'} or value.get('account') not in {None, *(a['id'] for a in accounts)}:
                    raise ValueError('invalid portable default account')
            else:
                value = {'account': 'codex' if migrated and any(a['id']=='codex' for a in accounts) else None}
            changes.append(Change(default_path, content=(json.dumps(value)+'\n').encode(), expected=fingerprint(default_path)))
        changes.insert(0, Change(registry, content=registry_bytes, mode=registry.stat().st_mode & 0o777, expected=registry_before))
        changes = translate(guard_sources(changes, snapshot), stage, real)
        result = Transaction(core.state_dir()).apply(changes, dry_run=dry_run)
    if not dry_run:
        print('Applied: '+result if result else 'Already synchronized; no files rewritten.')
        print('New terminals load automatically; existing terminals need initial loading only.')
        print('Existing sessions remain running; configuration applies on next Codex start.')
    return 0


def account_add(args):
    from .accounts import register
    return register(args)


def account_new(args):
    if not args.name:
        if not sys.stdin.isatty():
            raise argparse.ArgumentError(None, 'account name required in noninteractive mode; example: cnew personal')
        args.name = input('Account name: ').strip()
        if not args.alias:
            suggested = 'c'+args.name
            args.alias = input('Execution alias ['+suggested+']: ').strip() or suggested
    if not args.alias:
        args.alias = 'c'+args.name
    status = account_add(args)
    if args.dry_run or args.no_login:
        return status
    # Provider activation may fail after successful registration; login remains usable.
    if status and not any(a['id'] == args.name for a in core.load_accounts()):
        return status
    login_status = login(args.name)
    return login_status or status


def find_account(name):
    from .accounts import find
    return find(name)


def adopt_preferences(name, dry_run=False):
    registry = core.ROOT/'accounts.toml'
    registry_before = fingerprint(registry)
    account = find_account(name)
    native = core.account_home(account)/'config.toml'
    before = fingerprint(native)
    current = core.read_toml(native)
    source = core.ROOT/f'config/accounts/{account["id"]}.toml'
    source_before = fingerprint(source)
    document = core.tomlkit.parse(source.read_text())
    for key in core.PREFERENCE_KEYS:
        if key in current:
            document[key] = current[key]
        else:
            document.pop(key, None)
    ledger_path = core.state_dir()/'managed.json'
    ledger_before = fingerprint(ledger_path)
    if not ledger_path.exists():
        raise ValueError('install required before adopting native preferences')
    ledger = json.loads(ledger_path.read_text())
    tracked = ledger.get(str(native), [])
    tracked = [row for row in tracked if not (len(row['path'])==1 and row['path'][0] in core.PREFERENCE_KEYS)]
    tracked.extend({'path':[k], 'value':current[k]} for k in sorted(core.PREFERENCE_KEYS) if k in current)
    ledger[str(native)] = sorted(tracked,key=lambda row:row['path'])
    if fingerprint(native) != before:
        raise ValueError('native preferences changed during inspection; retry adoption')
    changes = [Change(registry, content=registry.read_bytes(), expected=registry_before),
               Change(native, content=native.read_bytes(), mode=native.stat().st_mode & 0o777, expected=before),
               Change(source, content=core.tomlkit.dumps(document).encode(), expected=source_before),
               Change(ledger_path, content=(json.dumps(ledger,indent=2,ensure_ascii=False)+'\n').encode(), expected=ledger_before)]
    result = Transaction(core.state_dir()).apply(changes, dry_run=dry_run)
    if not dry_run:
        print(f'Adopted current model preferences for {account["id"]}; native config unchanged.')
        if result:
            print('Applied: '+result)
    return 0


def login(name):
    account = find_account(name)
    env = os.environ.copy()
    env['CODEX_HOME'] = str(core.account_home(account))
    result = subprocess.run([core.resolve_cli(), 'login'], env=env)
    if result.returncode:
        print('Login incomplete; registration preserved. Retry: cenv account login '+account['id'])
        return result.returncode
    from .usage import read_identity
    identity = read_identity(account)
    if 'error' in identity:
        remember_identity(account, identity)
        print('Login identity could not be confirmed; registration retained.')
        return 1
    remember_identity(account, identity)
    print('Login confirmed: '+str(identity.get('email') or identity.get('type') or 'identity unavailable'))
    identity_diagnostics(core.load_accounts())
    return 0


def remember_identity(account, identity):
    from datetime import datetime, timezone
    from .accounts import json_bytes
    path = core.local_dir()/'identities.json'
    before = fingerprint(path)
    cached = json.loads(path.read_text()) if path.exists() else {}
    allowed = {key: re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
               for key in ['type', 'email', 'planType']
               if isinstance((value := identity.get(key)), str)}
    cached[account['id']] = {'identity': allowed, 'confirmed': 'error' not in identity,
                             'checked_at': datetime.now(timezone.utc).isoformat()}
    registry = core.ROOT/'accounts.toml'
    observed = fingerprint(registry)
    if not any(a['id'] == account['id'] for a in core.load_accounts()):
        raise ValueError('account removed while checking identity')
    Transaction(core.state_dir()).apply([
        Change(registry, content=registry.read_bytes(), expected=observed),
        Change(path, content=json_bytes(cached), expected=before)])


def account_list(online=False):
    from . import runtime
    accounts = core.load_accounts()
    if not accounts:
        print('No registered accounts; use cnew personal')
        return 0
    failed = False
    if online:
        from .usage import read_identity
        with ThreadPoolExecutor(max_workers=5) as pool:
            identities = list(pool.map(read_identity, accounts))
        for account, identity in zip(accounts, identities):
            remember_identity(account, identity)
            failed |= 'error' in identity
    cache = core.local_dir()/'identities.json'
    cached = json.loads(cache.read_text()) if cache.exists() else {}
    try:
        selected, selected_home, reason = runtime.resolve()
        selected_id = selected['id'] if selected else None
    except ValueError as error:
        print('Selection invalid: '+str(error))
        selected_id = None
    try:
        default_id = runtime.default_account()
    except ValueError as error:
        print('Default invalid: '+str(error))
        default_id = None
    from .tools import tools_status
    try:
        tool_problems = tools_status(accounts)
    except (ValueError, OSError, RuntimeError) as error:
        tool_problems = [str(error)]
    pending = Transaction(core.state_dir()).pending()
    print('NAME\tALIASES\tSELECTED\tDEFAULT\tAUTH\tIDENTITY / CHECKED\tCONFIG\tTOOLS\tHOME')
    for account in accounts:
        home = core.account_home(account)
        record = cached.get(account['id'], {})
        identity = record.get('identity', {})
        auth = 'confirmed (cached)' if record.get('confirmed') else 'unconfirmed'
        if online:
            auth = 'confirmed' if record.get('confirmed') else 'error'
        elif not record and not (home/'auth.json').exists():
            auth = 'no auth file / OS store unconfirmed'
        elif not record:
            auth = 'auth file present / unconfirmed'
        config = 'recovery required' if pending else 'synchronized'
        if not pending:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    changed = core.config_changes([account])
                if changed:
                    config = 'sync required'
            except (ValueError, OSError) as error:
                config = 'conflict'
        print('\t'.join([account['id'], ','.join(account['aliases']),
              '*' if account['id'] == selected_id else '-', '*' if account['id'] == default_id else '-',
              auth, str(identity.get('email') or identity.get('type') or 'unknown')+' / '+str(record.get('checked_at', 'never')),
              config, 'incomplete' if any(not p.startswith(tuple(a['id']+':' for a in accounts if a['id'] != account['id'])) for p in tool_problems) else 'ready', str(home)]))
    emails = {}
    for account in accounts:
        email = cached.get(account['id'], {}).get('identity', {}).get('email')
        if email:
            emails.setdefault(email.casefold(), []).append(account['id'])
    for names in emails.values():
        if len(names) > 1:
            print('WARN same login identity: '+', '.join(names)+'; separate profiles allowed')
    return int(failed)


def identity_diagnostics(accounts):
    from .usage import read_identity
    with ThreadPoolExecutor(max_workers=5) as pool:
        identities = list(pool.map(read_identity, accounts))
    failures = []
    emails = {}
    for account, identity in zip(accounts, identities):
        if 'error' in identity:
            failures.append(account['id']+': login identity unavailable')
        else:
            print(account['id']+': login confirmed ('+str(identity.get('type'))+')')
            email = identity.get('email')
            if email:
                emails.setdefault(email.casefold(), []).append(account['id'])
    for names in emails.values():
        if len(names)>1:
            print('WARN same login email: '+', '.join(names)+'; verify intended account/workspace selection')
    return failures


def doctor(online=False):
    accounts = core.load_accounts()
    failures = []
    warnings = []
    cli = core.resolve_cli()
    from . import runtime
    try:
        runtime.default_account()
        runtime.resolve()
    except ValueError as error:
        failures.append('selection: '+str(error))
    print('CLI: '+cli)
    print('Python: '+sys.version.split()[0])
    if Transaction(core.state_dir()).pending():
        failures.append('unfinished apply; use cenv rollback <ID>')
    try:
        pending = core.install_changes(accounts)
        changed = []
        for c in pending:
            if c.link:
                equal = c.path.is_symlink() and os.readlink(c.path) == str(c.link)
            else:
                equal = c.path.is_file() and not c.path.is_symlink() and c.path.read_bytes() == c.content
            if not equal:
                changed.append(str(c.path))
        if changed:
            failures.append('sync required: '+', '.join(changed))
    except (ValueError, OSError) as exc:
        failures.append(str(exc))
    for account in accounts:
        home = core.account_home(account)
        auth = home/'auth.json'
        if not auth.is_file() and not online:
            failures.append(account['id']+': login required (or OS credential store; confirm with --online)')
        elif os.name != 'nt' and auth.is_file() and auth.stat().st_mode & 0o077:
            warnings.append(account['id']+': auth.json permissions allow group/other access; inspect chmod 600')
        for part in ['AGENTS.md','skills','rules']:
            canonical = core.ROOT/'assets'/part
            required = canonical.is_file() and bool(canonical.read_bytes()) or canonical.is_dir() and any(canonical.iterdir())
            if required and not (home/part).exists():
                failures.append(account['id']+': missing '+part)
        print(f'{account["id"]}: home={home}, auth_file={auth.is_file()}')
    sources = core.read_toml(core.ROOT/'sources.toml').get('sources', [])
    from .tools import tools_status
    failures.extend(tools_status(accounts))
    for item in sources:
        if item.get('kind') == 'provider_bootstrap' and item.get('status') != 'reinstallable':
            warnings.append(item['name']+': provider bootstrap not reproducibly pinned; existing disabled configuration preserved')
    for role in (core.ROOT/'assets/agents').glob('*.toml'):
        try:
            core.read_toml(role)
        except ValueError:
            failures.append('invalid agent definition: '+role.name)
    if online:
        print('Online inspection may refresh account credentials.')
        failures.extend(identity_diagnostics(accounts))
        from .usage import main
        if main([]):
            failures.append('usage snapshot partially failed')
    for warning in warnings:
        print('WARN '+warning)
    for failure in failures:
        print('FAIL '+failure)
    print(f'{len(failures)} required failure(s), {len(warnings)} warning(s)')
    return int(bool(failures))


def export_bundle(output, include_accounts=False, include_shared=False, yes=False, dry_run=False):
    from .bundle import export_bundle as export
    return export(output, include_accounts=include_accounts, include_shared=include_shared,
                  yes=yes, dry_run=dry_run)


def parser():
    result = argparse.ArgumentParser(prog='cenv', description='Codex account and execution environment manager')
    sub = result.add_subparsers(dest='command', required=True, metavar='{help,install,sync,account,current,use,default,run,usage,doctor,export,tools,rollback}')
    sub.add_parser('help', help='show commands and daily workflows')
    for name in ['install', 'sync']:
        item = sub.add_parser(name)
        item.add_argument('--dry-run', action='store_true')
    account = sub.add_parser('account').add_subparsers(dest='action', required=True)
    account.add_parser('list').add_argument('--online', action='store_true')
    for action in ['add', 'import']:
        add = account.add_parser(action)
        add.add_argument('name', nargs='?' if action == 'add' else None)
        add.add_argument('--alias')
        add.add_argument('--dry-run', action='store_true')
        if action == 'add':
            add.add_argument('--no-login', action='store_true')
        else:
            add.add_argument('--home', required=True)
            add.add_argument('--adopt-existing', action='store_true', help='explicitly retain colliding native managed settings')
    account.add_parser('login').add_argument('name')
    remove = account.add_parser('remove')
    remove.add_argument('name')
    remove.add_argument('--yes', action='store_true')
    remove.add_argument('--dry-run', action='store_true')
    adopt = account.add_parser('adopt')
    adopt.add_argument('name')
    adopt.add_argument('--dry-run', action='store_true')
    sub.add_parser('current')
    use = sub.add_parser('use', help='requires the installed shell function')
    use.add_argument('name', nargs='?')
    use.add_argument('--clear', action='store_true')
    default = sub.add_parser('default')
    default.add_argument('name', nargs='?')
    default.add_argument('--clear', action='store_true')
    default.add_argument('--dry-run', action='store_true')
    run = sub.add_parser('run')
    run.add_argument('name')
    run.add_argument('args', nargs=argparse.REMAINDER)
    launch = sub.add_parser('_run')
    launch.add_argument('args', nargs=argparse.REMAINDER)
    sub.add_parser('_default')
    sub.add_parser('_select').add_argument('name')
    sub.add_parser('_exists').add_argument('name')
    usage = sub.add_parser('usage')
    usage.add_argument('name', nargs='?')
    usage.add_argument('--resets', action='store_true')
    usage.add_argument('--demo', action='store_true')
    sub.add_parser('doctor').add_argument('--online', action='store_true')
    export = sub.add_parser('export')
    export.add_argument('--output', required=True)
    export.add_argument('--include-shared', action='store_true')
    export.add_argument('--include-accounts', action='store_true')
    export.add_argument('--yes', action='store_true')
    export.add_argument('--dry-run', action='store_true')
    tools = sub.add_parser('tools').add_subparsers(dest='action', required=True)
    tools.add_parser('install').add_argument('--dry-run', action='store_true')
    rollback = sub.add_parser('rollback')
    rollback.add_argument('id')
    rollback.add_argument('--dry-run', action='store_true')
    return result


def help_text():
    sections = [
        ('계정 등록 / 조회', [
            ('cnew', '이름을 입력받아 계정 추가 및 로그인'),
            ('cnew 이름', '지정한 이름으로 계정 추가 및 로그인'),
            ('clogin 이름', '다시 로그인 · 취소한 로그인 재시도'),
            ('clist', '등록한 계정 목록 확인'),
            ('clist --online', '실제 로그인 상태까지 새로 확인'),
            ('ccurrent', '현재 실행할 계정과 선택 이유 확인'),
        ]),
        ('계정 선택 / 실행', [
            ('cuse 이름', '현재 터미널에서 사용할 계정 선택'),
            ('cuse --clear', '현재 선택 해제 · 터미널 기본값 사용'),
            ('cdefault 이름', '새 터미널에서 쓸 기본 계정 지정'),
            ('cdefault --clear', '새 터미널의 기본 계정 지정 해제'),
            ('codex', '현재 선택한 계정으로 Codex 실행'),
            ('crun 이름 "작업 내용"', '해당 계정으로 한 번 실행 · 선택 유지'),
            ('cpersonal', 'personal 계정으로 바로 실행하는 별칭'),
        ]),
        ('사용량 / 리셋 조회', [
            ('cusage', '전체 계정의 남은 사용량 확인'),
            ('cusage 이름', '지정한 계정의 남은 사용량 확인'),
            ('creset', '전체 계정의 리셋 정보 확인'),
            ('creset 이름', '지정 계정의 리셋 정보 확인'),
        ]),
        ('등록 해제 / 가져오기', [
            ('cdel 이름', '등록 해제 · 로그인 정보와 대화 보존'),
            ('cenv account import 이름 --home 경로', '기존 계정 폴더를 관리 목록에 등록'),
        ]),
        ('설정 / 점검 / 복구', [
            ('cenv sync', '공통 설정을 등록한 계정들에 반영'),
            ('cenv account adopt 이름', 'CLI에서 바꾼 모델 설정을 원본에 반영'),
            ('cenv doctor', '설정 · 연결 · 설치 상태 점검'),
            ('cenv doctor --online', '로그인 상태와 사용량까지 점검'),
            ('cenv tools install', '공통 도구 설치 · 실패한 설치 재시도'),
            ('cenv rollback 적용ID', '지정한 설정 변경의 되돌리기'),
        ]),
        ('다른 Mac용 파일 만들기', [
            ('cenv export --output 파일', '도구만 내보내기 · 등록 계정 0개'),
            ('  + --include-shared', '공통 스킬 · 지침 · 설정도 포함'),
            ('  + --include-accounts', '계정 구성도 포함 · 인증 정보 제외'),
        ]),
        ('추가 옵션 / 도움말', [
            ('cnew 이름 --alias 별칭', '직접 정한 실행 명령으로 계정 추가'),
            ('cnew 이름 --no-login', '등록만 진행 · 로그인은 나중에 수행'),
            ('명령 --dry-run', '변경 전 미리 보기 · 지원 명령 한정'),
            ('cdel 이름 --yes', '등록 해제의 확인 질문 생략'),
            ('명령 --help', '해당 명령의 상세 옵션 확인'),
            ('chelp', '이 도움말 다시 보기'),
        ]),
        ('처음 시작하는 순서', [
            ('1. cnew personal', 'personal이라는 이름으로 추가 및 로그인'),
            ('2. cuse personal', '현재 터미널의 사용 계정 선택'),
            ('3. cdefault personal', '새 터미널의 기본 계정도 지정'),
            ('4. codex', '선택한 계정으로 작업 시작'),
        ]),
    ]

    def width(text):
        return sum(0 if unicodedata.combining(c) else
                   2 if unicodedata.east_asian_width(c) in {'W', 'F'} else 1 for c in text)

    left = max(width(text) for title, rows in sections for text in [title, *(row[0] for row in rows)])
    right = max(width(row[1]) for _, rows in sections for row in rows)
    border = '+'+'-'*(left+2)+'+'+'-'*(right+2)+'+'

    def row(command, description):
        print('| '+command+' '*(left-width(command))+' | '+description+' '*(right-width(description))+' |')

    print('Codex 계정 관리 도움말')
    print('이름: clist의 NAME 열에 표시되는 등록 이름 (예: personal, work)')
    print()
    print(border)
    for title, rows in sections:
        row(title, '설명')
        print(border)
        for command, description in rows:
            row(command, description)
        print(border)
    print()
    print('· 계정별 기본 별칭: c + 이름 (예: work → cwork)')
    print('· cuse: 현재 터미널에 적용 / cdefault: 새 터미널에 적용')
    print('· creset: 정보 조회 전용 · 실제 리셋 크레딧 사용 없음')
    print('· 새 터미널의 자동 로딩 · 계정 관리 중 반복 source 불필요')
    print('· 내보내기 예시: cenv export --include-shared --output shared.tar.gz')
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'help':
            return help_text()
        if args.command in {'install', 'sync'}:
            return sync(args.dry_run, args.command == 'install')
        if args.command == 'account':
            if args.action == 'add':
                return account_new(args)
            if args.action == 'import':
                from .accounts import register
                return register(args, importing=True)
            if args.action == 'remove':
                from .accounts import remove
                return remove(args)
            if args.action == 'login':
                return login(args.name)
            if args.action == 'adopt':
                return adopt_preferences(args.name, args.dry_run)
            return account_list(args.online)
        if args.command in {'current', 'default', 'use', 'run', '_run', '_default', '_select', '_exists'}:
            from . import runtime
            if args.command == 'current':
                return runtime.current()
            if args.command == 'default':
                if args.name and args.clear:
                    raise argparse.ArgumentError(None, 'choose an account name or --clear, not both')
                if not args.name and not args.clear:
                    print(runtime.default_account() or 'No default account; use cdefault NAME')
                    return 0
                return runtime.set_default(None if args.clear else args.name, dry_run=args.dry_run)
            if args.command == 'use':
                print('cenv use cannot change its parent shell; load shell integration and use cuse NAME', file=sys.stderr)
                return 2
            if args.command == '_default':
                print(runtime.default_account() or '')
                return 0
            if args.command == '_select':
                print(runtime.select(args.name))
                return 0
            if args.command == '_exists':
                return 0 if any(a['id'] == args.name for a in core.load_accounts()) else 1
            remaining = args.args[1:] if args.args[:1] == ['--'] else args.args
            return runtime.run(args.name if args.command == 'run' else None, remaining)
        if args.command == 'usage':
            from .usage import main as usage_main
            options = (['--resets'] if args.resets else []) + (['--demo'] if args.demo else [])
            if args.name:
                options += [find_account(args.name)['id']]
            return usage_main(options)
        if args.command == 'doctor':
            return doctor(args.online)
        if args.command == 'export':
            return export_bundle(args.output, include_accounts=args.include_accounts,
                                 include_shared=args.include_shared, yes=args.yes, dry_run=args.dry_run)
        if args.command == 'tools':
            from .tools import install_tools
            tx = Transaction(core.state_dir())
            if tx.pending():
                raise ValueError('unfinished apply; rollback required before provider installation')
            if args.dry_run:
                return install_tools(core.load_accounts(), dry_run=True)
            with tx._lock():
                return install_tools(core.load_accounts())
        if args.command == 'rollback':
            return 0 if Transaction(core.state_dir()).rollback(args.id, dry_run=args.dry_run) else 1
    except (KeyboardInterrupt, EOFError):
        print('Interrupted; completed registration preserved. Retry login or inspect cenv doctor.', file=sys.stderr)
        return 130
    except argparse.ArgumentError as exc:
        print('cenv: '+str(exc), file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('cenv: '+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
