"""Native PowerShell integration and file copies without symlink privileges."""
import hashlib
import json
import os
from pathlib import Path
import sys

from . import core
from .transaction import Change, fingerprint

COMMANDS = {
    'chelp': ['help'], 'cnew': ['account', 'add'], 'cdel': ['account', 'remove'],
    'clogin': ['account', 'login'], 'clist': ['account', 'list'],
    'ccurrent': ['current'], 'cdefault': ['default'], 'crun': ['run'],
    'cusage': ['usage'], 'creset': ['usage', '--resets'], 'codex': ['_run', '--'],
}


def quote(value):
    return "'"+str(value).replace("'", "''")+"'"


def shell_text(accounts):
    python, entry = quote(sys.executable), quote(core.ROOT/'bin/cenv')
    lines = ["# Generated native PowerShell account functions.",
             "$env:PYTHONUTF8 = '1'", "$env:PYTHONIOENCODING = 'utf-8'",
             "if ($PSVersionTable.PSVersion -ge [version]'7.4') { $global:PSNativeCommandArgumentPassing = 'Standard' }",
             "if (Get-Variable CenvAliases -Scope Global -ErrorAction SilentlyContinue) {",
             "  foreach ($name in $global:CenvAliases) { Remove-Item ('Function:'+ $name) -ErrorAction SilentlyContinue }", "}",
             'function global:cenv {',
             f'  & {python} -I -X utf8 {entry} @args',
             '  $status = $LASTEXITCODE',
             "  if ($args.Count -ge 2 -and $args[0] -eq 'account' -and $args[1] -in @('add','remove','import')) {",
             f'    . {quote(core.local_dir()/"shell.ps1")}',
             '    foreach ($key in @(\'CENV_ACCOUNT\', \'CENV_DEFAULT_ACCOUNT\')) {',
             '      $selected = [Environment]::GetEnvironmentVariable($key)',
             '      if ($selected) {', f'        & {python} -I -X utf8 {entry} _exists $selected',
             '        if ($LASTEXITCODE -ne 0) { [Environment]::SetEnvironmentVariable($key, $null, \'Process\') }',
             '      }', '    }', '  }', '  $global:LASTEXITCODE = $status', '}',
             'function global:cuse {',
             "  if ($args.Count -eq 1 -and $args[0] -eq '--clear') { Remove-Item Env:CENV_ACCOUNT -ErrorAction SilentlyContinue; $global:LASTEXITCODE = 0; return }",
             "  if ($args -contains '--help' -or $args -contains '-h') { cenv use @args; return }",
             f'  $selected = & {python} -I -X utf8 {entry} _select @args',
             '  if ($LASTEXITCODE -eq 0) { $env:CENV_ACCOUNT = [string]$selected }', '}',
             "if (-not (Test-Path Env:CENV_DEFAULT_ACCOUNT)) {",
             f'  $selected = & {python} -I -X utf8 {entry} _default',
             '  if ($LASTEXITCODE -eq 0) { $env:CENV_DEFAULT_ACCOUNT = [string]$selected }', '}']
    for name, prefix in COMMANDS.items():
        lines.append(f'function global:{name} {{ cenv '+ ' '.join(quote(p) for p in prefix)+' @args }')
    aliases = []
    for account in accounts:
        for alias in account['aliases']:
            if alias != 'codex':
                aliases.append(alias)
                lines.append(f'function global:{alias} {{ cenv run {quote(account["id"])} -- @args }}')
    lines.append('if (Test-Path -LiteralPath '+quote(core.local_dir()/'board.ps1')+') { . '+quote(core.local_dir()/'board.ps1')+' }')
    lines.append('$global:CenvAliases = @('+','.join(quote(a) for a in aliases)+')')
    return '\n'.join(lines)+'\n'


def shell_changes(accounts):
    content = shell_text(accounts).encode('utf-8-sig')
    paths = [core.ROOT/'shell/accounts.ps1', core.local_dir()/'shell.ps1']
    return [Change(path, content=content, expected=fingerprint(path)) for path in paths]


def profile_text(original):
    if core.BEGIN in original or core.END in original:
        if original.count(core.BEGIN) != 1 or original.count(core.END) != 1 or original.index(core.END) < original.index(core.BEGIN):
            raise ValueError('malformed managed PowerShell profile block')
        start, stop = original.index(core.BEGIN), original.index(core.END)+len(core.END)
        original = original[:start]+original[stop:]
    path = quote(core.local_dir()/'shell.ps1')
    return original.rstrip()+f'\n\n{core.BEGIN}\nif (Test-Path -LiteralPath {path}) {{ . {path} }}\n{core.END}\n'


def assets_changes(links):
    """Copy managed files, refusing edits to unowned or user-modified targets."""
    ledger = core.local_dir()/'copies.json'
    before = fingerprint(ledger)
    owned = json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {}
    updated = dict(owned)
    changes = []
    staged = {}
    for destination, source in links:
        source = source.resolve()
        incoming = {path: data for path, data in staged.items() if path == source or source in path.parents}
        directory = source.is_dir() or any(path != source for path in incoming)
        items = {}
        if source.exists():
            for item in sorted(source.rglob('*')) if directory else [source]:
                if item.is_symlink():
                    raise ValueError('unsupported shared asset link: '+str(item))
                if item.name == '.gitkeep' or item.is_dir():
                    continue
                if not item.is_file():
                    raise ValueError('unsupported shared asset: '+str(item))
                items[item] = item.read_bytes()
        items.update(incoming)
        if not items and not directory:
            raise ValueError('shared asset source missing: '+str(source))
        for item, data in sorted(items.items()):
            target = destination/item.relative_to(source) if directory else destination
            staged[target.resolve()] = data
            observed = fingerprint(target)
            digest = hashlib.sha256(data).hexdigest()
            if observed.get('kind') == 'file' and observed.get('sha256') == digest:
                continue
            previous = owned.get(str(target))
            if observed['kind'] != 'missing' and (observed['kind'] != 'file' or observed.get('sha256') != previous):
                raise ValueError('shared asset conflict; preserve existing file before sync: '+str(target))
            changes.append(Change(target, content=data, expected=observed))
            updated[str(target)] = digest
    if updated != owned:
        changes.append(Change(ledger, content=(json.dumps(updated, indent=2)+'\n').encode(), expected=before))
    return changes


def install_changes(accounts):
    from .assets import asset_links
    changes = core.config_changes(accounts)+assets_changes(asset_links(core.ROOT, Path.home(), accounts))
    changes += shell_changes(accounts)
    if os.environ.get('CENV_DOCUMENTS'):
        documents = Path(os.environ['CENV_DOCUMENTS'])
    elif os.name == 'nt':
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) != 0:
            raise OSError('Windows Documents folder lookup failed')
        documents = Path(buffer.value)
    else:
        documents = Path.home()/'Documents' 
    for folder in ('PowerShell',):
        profile = documents/folder/'profile.ps1'
        raw = profile.read_bytes() if profile.exists() else b''
        original = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
        changes.append(Change(profile, content=profile_text(original).encode('utf-8-sig'), expected=fingerprint(profile)))
    for name, prefix in {'cenv': [], **{k: v for k,v in COMMANDS.items() if k != 'codex'}}.items():
        path = core.local_dir()/'bin'/(name+'.cmd')
        # cmd cannot propagate parent-shell selection; cuse is supplied by PowerShell.
        args = ' '.join('"'+p+'"' for p in prefix)
        if any(c in str(sys.executable)+str(core.ROOT) for c in '%\r\n'):
            raise ValueError('Windows launcher path contains unsupported cmd expansion characters')
        content = f'@echo off\r\n"{sys.executable}" -I -X utf8 "{core.ROOT / "bin/cenv"}" {args} %*\r\nexit /b %errorlevel%\r\n'
        changes.append(Change(path, content=content.encode(), expected=fingerprint(path)))
    local = core.local_dir()/'local.toml'
    if not local.exists():
        changes.append(Change(local, content=core.dump({}), expected=fingerprint(local)))
    return changes
