import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_env import core


@unittest.skipUnless(shutil.which('bash'), 'bash unavailable')
class BashShellTests(unittest.TestCase):
    def test_selection_refresh_removed_alias_and_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)/'home with spaces'; home.mkdir()
            root=home/'manager'; (root/'bin').mkdir(parents=True)
            pointer=home/'.config/codex-environment/shell.bash';pointer.parent.mkdir(parents=True)
            updated=root/'updated.bash'
            accounts=[{'id':'personal','aliases':['cpersonal']}]
            with patch.object(core,'ROOT',root):
                pointer.write_text(core.shell_text(accounts,shell='bash'))
                updated.write_text(core.shell_text([],shell='bash'))
            import shlex
            binary=root/'bin/cenv'
            binary.write_text('#!/bin/sh\ncase "$1" in\n_default) echo personal ;;\n_select) echo personal ;;\n_exists) exit 1 ;;\naccount) cp '+shlex.quote(str(updated))+' '+shlex.quote(str(pointer))+' ;;\nrun) printf "<%s>\\n" "$@" ;;\nesac\n')
            binary.chmod(0o755)
            result=subprocess.run(['bash','--noprofile','--norc','-c','source "$1"; cuse personal; printf "%s\\n" "$CENV_ACCOUNT"; cpersonal "한 글" ""; cdel personal; printf "%s:%s\\n" "${CENV_ACCOUNT-unset}" "$CENV_DEFAULT_ACCOUNT"; declare -F cpersonal && exit 9; exit 0','bash',str(pointer)],text=True,capture_output=True,env={**os.environ,'HOME':str(home)})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.splitlines(),['personal','<run>','<personal>','<-->','<한 글>','<>','unset:'])

    def test_bash_startup_block_idempotent(self):
        first=core.update_shell('export EXISTING=1\n',shell='bash')
        self.assertEqual(core.update_shell(first,shell='bash'),first)
        self.assertIn('shell.bash',first)
        self.assertNotIn('shell.zsh',first)

    def test_linux_install_plans_bash_without_native_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory).resolve();root=home/'manager'
            with patch.object(core,'ROOT',root), patch.object(Path,'home',return_value=home), \
                 patch.object(core.sys,'platform','linux'), patch.object(core,'config_changes',return_value=[]), \
                 patch('codex_env.assets.asset_links',return_value=[]), \
                 patch.object(core,'resolve_cli',side_effect=ValueError('missing')):
                plan=core.install_changes([])
            contents={change.path:change.content for change in plan}
            self.assertIn(b'shell.bash',contents[home/'.bashrc'])
            self.assertIn(b'shell.zsh',contents[home/'.zshrc'])
            self.assertEqual(contents[home/'.config/codex-environment/local.toml'],b'')
