"""Persist installer paths through the same guarded transaction as account commands."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', required=True, type=Path)
    parser.add_argument('--cli', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.manager))
    from codex_env import core
    from codex_env.windows import quote
    from codex_env.transaction import Change, Transaction, fingerprint
    local = core.local_dir()/'local.toml'
    document = core.tomlkit.parse(local.read_text(encoding='utf-8-sig')) if local.exists() else core.tomlkit.document()
    document['cli'] = str(args.cli)
    board = core.local_dir()/'board.ps1'
    content = ('function global:cboard {\n'
               '  $oldCli = $env:CENV_CLI; $oldRegistry = $env:CENV_REGISTRY\n'
               f'  $env:CENV_CLI = {quote(args.cli)}\n'
               f'  $env:CENV_REGISTRY = {quote(core.ROOT/"accounts.toml")}\n'
               '  try {\n'
               f'    & {quote(sys.executable)} -I -X utf8 -m cboard @args\n'
               '    $status = $LASTEXITCODE\n'
               '  } finally { $env:CENV_CLI = $oldCli; $env:CENV_REGISTRY = $oldRegistry }\n'
               '  $global:LASTEXITCODE = $status\n'
               '}\n')
    Transaction(core.state_dir()).apply([
        Change(local, content=core.tomlkit.dumps(document).encode('utf-8'), expected=fingerprint(local)),
        Change(board, content=content.encode('utf-8-sig'), expected=fingerprint(board))])


if __name__ == '__main__':
    main()
