"""Account registry, managed configuration, and shell integration."""

import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tomllib
from urllib.parse import urlsplit, parse_qsl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
import tomlkit

NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
RESERVED = {"cenv", "cusage", "creset", "cnew", "cdel", "chelp", "clist", "clogin", "ccurrent", "cuse", "cdefault", "crun", "source", "exec", "command", "builtin", "eval", "exit"}
BEGIN = "# >>> codex-environment >>>"
END = "# <<< codex-environment <<<"
PREFERENCE_KEYS = {'model', 'model_reasoning_effort', 'plan_mode_reasoning_effort', 'personality', 'service_tier'}


def local_dir():
    return Path.home() / ".config/codex-environment"


def state_dir():
    return Path.home() / ".local/state/codex-environment"


def read_toml(path):
    return tomllib.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).exists() else {}


def account_home(account):
    raw = account["home"]
    if raw == "~" or raw.startswith("~/"):
        return Path.home() / raw[2:] if raw != "~" else Path.home()
    return Path(raw).expanduser()


def load_accounts(path=None):
    data = read_toml(path or ROOT / "accounts.toml")
    if data.get('version', 1) != 1:
        raise ValueError('unsupported accounts.toml version')
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError("accounts.toml: accounts must be a list")
    ids, aliases, homes = set(), set(), set()
    alias_owners = {}
    for item in accounts:
        if not isinstance(item, dict):
            raise ValueError('accounts must contain TOML account tables')
        name = item.get("id", "")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in ids:
            raise ValueError("invalid or duplicate account id")
        ids.add(name)
        raw = item.get("home", "")
        if not isinstance(raw, str) or not (raw.startswith("~/") or Path(raw).is_absolute()):
            raise ValueError(f"{name}: home must be absolute or ~/relative")
        home = account_home(item).resolve()
        protected = {Path.home().resolve(), Path('/'), (Path.home()/'.agents').resolve(),
                     (Path.home()/'.ssh').resolve(), (Path.home()/'.aws').resolve()}
        if home in protected or home in homes or any(p in home.parents for p in protected-{Path.home().resolve(), Path('/')}):
            raise ValueError(f"{name}: unsafe or duplicate account home")
        if any(home in other.parents or other in home.parents for other in homes):
            raise ValueError(f"{name}: nested account homes are not allowed")
        homes.add(home)
        names = item.get("aliases", [])
        if not isinstance(names, list) or not names:
            raise ValueError(f"{name}: aliases required")
        for alias in names:
            if not isinstance(alias, str) or not NAME.fullmatch(alias) or alias in RESERVED or alias in aliases:
                raise ValueError(f"{name}: invalid, reserved, or duplicate alias")
            if alias == 'codex' and name != 'codex':
                raise ValueError('codex alias belongs to the default account')
            aliases.add(alias)
            alias_owners[alias] = name
        if not isinstance(item.get('profile', ''), str) or any(ord(c) < 32 for c in item.get('profile', '')):
            raise ValueError(f'{name}: invalid terminal profile')
        if not isinstance(item.get('color', ''), str) or (item.get('color') and not re.fullmatch('[0-9a-fA-F]{6}', item['color'])):
            raise ValueError(f'{name}: invalid terminal color')
        item.setdefault('label', names[0])
        if not isinstance(item['label'],str) or any(ord(c)<32 for c in item['label']):
            raise ValueError(f'{name}: invalid display label')
    if any(alias in ids and alias != owner for alias, owner in alias_owners.items()):
        raise ValueError('execution alias collides with another account name')
    return accounts


def _windows_cli_candidates():
    """Resolve npm shims to the official package's native executable."""
    names = [shutil.which('codex.exe'), shutil.which('codex.cmd'), shutil.which('codex')]
    roots = []
    for name in names:
        if name:
            path = Path(name).expanduser()
            if path.suffix.lower() == '.exe':
                yield path
            roots.append(path.parent/'node_modules/@openai')
    appdata = os.environ.get('APPDATA')
    if appdata:
        roots.append(Path(appdata)/'npm/node_modules/@openai')
    for root in dict.fromkeys(roots):
        packages = [root/'codex', *sorted(root.glob('codex-win32-*')),
                    *sorted((root/'codex/node_modules/@openai').glob('codex-win32-*'))]
        for package in packages:
            try:
                metadata = json.loads((package/'package.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if metadata.get('name') not in {'@openai/codex', '@openai/codex-win32-x64', '@openai/codex-win32-arm64'}:
                continue
            paths = list((package/'vendor').glob('*-pc-windows-msvc/bin/codex.exe'))
            paths += list((package/'vendor').glob('*-pc-windows-msvc/codex/codex.exe'))
            for path in sorted(paths):
                if package.resolve() in path.resolve().parents:
                    yield path


def resolve_cli():
    settings = read_toml(local_dir() / 'local.toml')
    explicit = os.environ.get('CENV_CLI') or settings.get('cli')
    windows = sys.platform == 'win32'
    candidates = ([explicit] if explicit else list(_windows_cli_candidates()) if windows else
                  ['/opt/homebrew/bin/codex', '/usr/local/bin/codex', shutil.which('codex')])
    for name in candidates:
        if not name:
            continue
        path = Path(name).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            continue
        if windows and path.suffix.lower() != '.exe':
            continue
        if (not sys.platform.startswith('linux') and path == Path.home()/'.local/bin/codex') or path.resolve() in {(ROOT/'bin/cenv').resolve(), (ROOT/'bin/cusage').resolve()}:
            continue
        return str(path)
    raise ValueError('Codex CLI missing; install @openai/codex or set CENV_CLI to its native executable')


def dump(data):
    return tomlkit.dumps(data).encode()


def private_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)


def bootstrap():
    """Capture declarative sources without changing any installed settings."""
    if (ROOT/'accounts.toml').exists():
        return
    if not (ROOT/'config/common.toml').exists():
        private_write(ROOT/'config/common.toml', dump({}))
    private_write(ROOT/'accounts.toml', dump({'version': 1, 'accounts': []}))


def expand(value):
    if isinstance(value, str):
        return value.replace('${HOME}', str(Path.home())).replace('${ROOT}', str(ROOT))
    if isinstance(value, dict):
        return {k: expand(v) for k,v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


def flatten(value, prefix=()):
    result = {}
    for key, child in value.items():
        path = prefix+(key,)
        if isinstance(child, dict):
            result.update(flatten(child, path))
        else:
            result[path] = child
    return result


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from string_values(item)


def put(document, keys, value, delete=False):
    parent = document
    for key in keys[:-1]:
        if key not in parent:
            if delete:
                return
            parent[key] = tomlkit.table()
        if not isinstance(parent[key], dict):
            raise ValueError('config table conflicts with managed key: '+'.'.join(keys))
        parent = parent[key]
    if delete:
        parent.pop(keys[-1], None)
    else:
        parent[keys[-1]] = value


def shared_config():
    from .tools import rea_config, _plugin_source
    local = flatten(expand(rea_config()))
    plugin = _plugin_source()
    if plugin:
        local[('marketplaces','ponytail','source_type')] = 'local'
        local[('marketplaces','ponytail','source')] = str(ROOT/plugin['marketplace'])
    return flatten(expand(read_toml(ROOT/'config/common.toml'))), local


def desired_config(account, account_override=None, shared=None):
    common, local = shared_config() if shared is None else shared
    overrides = read_toml(ROOT/f'config/accounts/{account["id"]}.toml') if account_override is None else account_override
    values = flatten(expand(read_toml(ROOT/'config/defaults.toml')))
    values.update(common)
    values.update(flatten(expand(overrides)))
    values.update(local)
    for path in values:
        local_plugin_key = path in {('marketplaces','ponytail','source_type'), ('marketplaces','ponytail','source')}
        if path[0] in {'notify','projects','desktop','notice','hooks','browser_use','tui','marketplaces','shell_environment_policy'} and not local_plugin_key:
            raise ValueError('unmanaged config key in source: '+'.'.join(path))
        if path[:2] in {('mcp_servers','node_repl'), ('mcp_servers','computer-use')}:
            raise ValueError('app-owned MCP configuration cannot be managed')
        if any(re.search(r'token|password|secret|api[_-]?key', key, re.I) for key in path):
            raise ValueError('secret config belongs in local credentials, not portable sources')
        if any(key.lower() in {'authorization', 'proxy-authorization', 'cookie', 'set-cookie', 'x-api-key'} for key in path):
            raise ValueError('authentication header belongs in local credentials, not portable sources')
        for value in string_values(values[path]):
            if re.search(r'(?:authorization|cookie)\s*[:=]|--(?:api[-_]key|token|password|secret)(?:=|$)', value, re.I):
                raise ValueError('credential-bearing argument belongs in local settings')
            if value.startswith(('https://','http://')):
                url = urlsplit(value)
                if url.username or url.password or any(re.search(r'token|secret|password|api[_-]?key|signature',key,re.I) for key,_ in parse_qsl(url.query)):
                    raise ValueError('credential-bearing URL belongs in local settings')
    return values


def config_changes(accounts, account_overrides=None):
    from .transaction import Change, fingerprint
    sources = [ROOT/'config/defaults.toml', ROOT/'config/common.toml', ROOT/'sources.toml', local_dir()/'local.toml']
    sources += [ROOT/f'config/accounts/{a["id"]}.toml' for a in accounts]
    observed = {path: fingerprint(path) for path in sources}
    shared = shared_config()
    ledger_path = state_dir()/'managed.json'
    ledger_before = fingerprint(ledger_path)
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    updated = copy.deepcopy(ledger)
    changes = []
    missing = object()
    for account in accounts:
        path = account_home(account)/'config.toml'
        before = fingerprint(path)
        original = path.read_text() if path.exists() else ''
        document = tomlkit.parse(original)
        current = flatten(tomllib.loads(original))
        desired = desired_config(account, (account_overrides or {}).get(account['id']), shared)
        old = {tuple(item['path']): item['value'] for item in ledger.get(str(path), [])}
        for key in sorted(desired.keys() | old.keys()):
            now, previous, new = current.get(key, missing), old.get(key, missing), desired.get(key, missing)
            if key in old and now != previous and now != new:
                action = f'cenv account adopt {account["id"]}' if len(key)==1 and key[0] in PREFERENCE_KEYS else 'edit source or account override'
                raise ValueError(f'{account["id"]}: config conflict at {".".join(key)}; {action} before sync')
            if now == new:
                continue
            print(f'Config {account["id"]}: {"remove" if new is missing else "set"} {".".join(key)}')
            put(document, key, new, delete=new is missing)
        content = tomlkit.dumps(document).encode()
        tomllib.loads(content.decode())
        if content != original.encode():
            changes.append(Change(path, content=content, mode=path.stat().st_mode & 0o777 if path.exists() else 0o600, expected=before))
        updated[str(path)] = [{'path': list(k), 'value': v} for k,v in sorted(desired.items())]
    content = json.dumps(updated, indent=2, ensure_ascii=False).encode()+b'\n'
    if not ledger_path.exists() or content != ledger_path.read_bytes():
        changes.append(Change(ledger_path, content=content, expected=ledger_before))
    if any(fingerprint(path) != signature for path, signature in observed.items()):
        raise ValueError('configuration source changed during planning; retry sync')
    return changes


def shell_text(accounts, cli=None, shell="zsh"):
    q = shlex.quote
    executable = q(str(ROOT/'bin/cenv'))
    aliases = [alias for account in accounts for alias in account['aliases'] if alias != 'codex']
    names = ['codex','cenv','cnew','cdel','chelp','clist','clogin','ccurrent','cuse','cdefault','crun','cusage','creset']
    lines = ['# Generated account functions; no config writes or network during loading.',
             'case ":$PATH:" in *":$HOME/.codex/bin:"*) ;; *) export PATH="$HOME/.codex/bin:$PATH" ;; esac',
             'if (( ${+_CENV_ALIASES} )); then',
             '  for _cenv_alias in "${_CENV_ALIASES[@]}"; do unfunction "$_cenv_alias" 2>/dev/null || true; done',
             'fi',
             'typeset -ga _CENV_ALIASES=('+ ' '.join(q(n) for n in aliases)+')',
             'unalias '+ ' '.join(names+aliases)+' 2>/dev/null || true',
             'if (( ! ${+CENV_DEFAULT_ACCOUNT} )); then',
             f'  _cenv_default="$({executable} _default)" || return $?',
             '  export CENV_DEFAULT_ACCOUNT="$_cenv_default"',
             '  unset _cenv_default',
             'fi',
             'cenv() {', f'  {executable} "$@"', '  local _cenv_status=$?',
             '  if [[ "${1:-}:${2:-}" == account:remove && -n "${CENV_ACCOUNT:-}" ]]; then',
             f'    {executable} _exists "$CENV_ACCOUNT" >/dev/null 2>&1 || unset CENV_ACCOUNT',
             '  fi',
             '  if [[ "${1:-}:${2:-}" == account:remove && -n "${CENV_DEFAULT_ACCOUNT:-}" ]]; then',
             f'    {executable} _exists "$CENV_DEFAULT_ACCOUNT" >/dev/null 2>&1 || export CENV_DEFAULT_ACCOUNT=""',
             '  fi',
             '  case "${1:-}:${2:-}" in',
             '    install:*|sync:*|rollback:*|account:add|account:remove|account:import)',
             '      [[ ! -r "$HOME/.config/codex-environment/shell.zsh" ]] || source "$HOME/.config/codex-environment/shell.zsh" ;;',
             '  esac', '  return $_cenv_status', '}',
             'codex() { cenv _run -- "$@"; }',
             'cnew() { cenv account add "$@"; }',
             'cdel() { cenv account remove "$@"; }',
             'clogin() { cenv account login "$@"; }',
             'clist() { cenv account list "$@"; }',
             'chelp() { cenv help "$@"; }',
             'ccurrent() { cenv current "$@"; }',
             'cdefault() { cenv default "$@"; }',
             'crun() { cenv run "$@"; }',
             'cusage() { cenv usage "$@"; }',
             'creset() { cenv usage --resets "$@"; }',
             'cuse() {',
             '  if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then cenv use "$@"; return $?; fi',
             '  if [[ "${1:-}" == --clear && $# == 1 ]]; then unset CENV_ACCOUNT; return 0; fi',
             '  local _cenv_selected',
             f'  _cenv_selected="$({executable} _select "$@")" || return $?',
             '  export CENV_ACCOUNT="$_cenv_selected"', '}' ]
    for account in accounts:
        for alias in account['aliases']:
            if alias != 'codex':
                lines.append(f'{alias}() {{ cenv run {q(account["id"])} -- "$@"; }}')
    text = '\n'.join(lines)+'\n'
    if shell == 'bash':
        text = text.replace('if (( ${+_CENV_ALIASES} )); then', 'if declare -p _CENV_ALIASES >/dev/null 2>&1; then')
        text = text.replace('unfunction "$_cenv_alias"', 'unset -f "$_cenv_alias"')
        text = text.replace('typeset -ga _CENV_ALIASES=', '_CENV_ALIASES=')
        text = text.replace('if (( ! ${+CENV_DEFAULT_ACCOUNT} )); then', 'if [[ ! ${CENV_DEFAULT_ACCOUNT+x} ]]; then')
        text = text.replace('shell.zsh', 'shell.bash')
    return text


def shell_changes(accounts):
    from .transaction import Change, fingerprint
    changes = []
    for shell in ('zsh', 'bash'):
        path = ROOT/f'shell/accounts.{shell}'
        changes.append(Change(path, content=shell_text(accounts, shell=shell).encode(), mode=0o644, expected=fingerprint(path)))
    return changes


def shell_block(shell='zsh'):
    return f'{BEGIN}\nif [ -r "$HOME/.config/codex-environment/shell.{shell}" ]; then\n  . "$HOME/.config/codex-environment/shell.{shell}"\nfi\n{END}'


def update_shell(text, migrate=False, managed_names=(), shell="zsh"):
    if BEGIN in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(END)<text.index(BEGIN):
            raise ValueError('malformed managed shell block')
        start, stop = text.index(BEGIN), text.index(END)+len(END)
        text = text[:start]+text[stop:]
    if migrate and '# Codex 계정별 iTerm2 프로필 전환' in text:
        start = text.index('# Codex 계정별 iTerm2 프로필 전환')
        match = re.search(r'^alias cusage=.*\n?', text[start:], re.M)
        if not match:
            raise ValueError('legacy Codex shell block changed; refusing automatic removal')
        block = text[start:start+match.end()]
        if any(line.strip() and not (line.startswith((' ', '\t', '#', 'codex()', '}', 'alias '))) for line in block.splitlines()):
            raise ValueError('unexpected legacy shell content')
        text = text[:start]+text[start+match.end():]
    for name in managed_names:
        escaped = re.escape(name)
        if re.search(r'^\s*(?:function\s+'+escaped+r'(?:\s|\()|'+escaped+r'\s*\(\s*\)|alias\s+'+escaped+r'=)', text, re.M):
            raise ValueError(f'shell command collision: {name}; rename or remove the existing definition before install')
    return text.rstrip()+'\n\n'+shell_block(shell)+'\n'


def install_changes(accounts):
    if os.name == "nt":
        from .windows import install_changes as windows_install
        return windows_install(accounts)
    from .assets import asset_links
    from .transaction import Change, fingerprint
    changes = config_changes(accounts)
    for dest, source in asset_links(ROOT, Path.home(), accounts):
        changes.append(Change(dest, link=source, expected=fingerprint(dest)))
    changes += shell_changes(accounts)
    for shell in ('zsh', 'bash'):
        pointer = local_dir()/f'shell.{shell}'
        changes.append(Change(pointer, link=ROOT/f'shell/accounts.{shell}', expected=fingerprint(pointer)))
    shell_files = set()
    startup_files = [(Path.home()/'.zshrc', 'zsh'), (Path.home()/'.dotfiles/zsh/.zshrc', 'zsh')]
    if sys.platform.startswith('linux'):
        startup_files += [(Path.home()/'.bashrc', 'bash')]
        startup_files += [(Path.home()/'.bash_profile', 'bash')] if (Path.home()/'.bash_profile').exists() else []
    for file, shell in startup_files:
        if file.parent.exists():
            if file.is_symlink():
                if not file.exists():
                    raise ValueError('broken shell symlink: '+str(file))
            file = file.resolve()
            if file in shell_files:
                continue
            shell_files.add(file)
            original = file.read_text() if file.exists() else ''
            changes.append(Change(file, content=update_shell(original, migrate=True, shell=shell, managed_names={'codex','cenv','cnew','cdel','chelp','clist','clogin','ccurrent','cuse','cdefault','crun','cusage','creset'} | {n for a in accounts for n in a['aliases']}).encode(), mode=0o644, expected=fingerprint(file)))
    for name in ['cenv','cusage','creset','cnew','cdel','chelp','clist','clogin','ccurrent','cuse','cdefault','crun']:
        dest = Path.home()/'.codex/bin'/name
        changes.append(Change(dest, link=ROOT/'bin'/name, expected=fingerprint(dest)))
    local = local_dir()/'local.toml'
    if not local.exists():
        try:
            settings = {'cli': resolve_cli()}
        except ValueError:
            settings = {}
        changes.append(Change(local, content=dump(settings), expected=fingerprint(local)))
    return changes
