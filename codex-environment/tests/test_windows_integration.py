"""PowerShell plans and account operations in isolated fixtures, not Windows proof."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from codex_env import accounts, core, windows
from codex_env.transaction import Transaction


class WindowsIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home, self.root = self.base/'home', self.base/"source with ' quote"
        self.home.mkdir()
        for directory in ('config/accounts','assets/skills','assets/rules','assets/agents','shell'):
            (self.root/directory).mkdir(parents=True)
        (self.root/'accounts.toml').write_bytes(core.dump({'version':1,'accounts':[]}))
        (self.root/'sources.toml').write_bytes(core.dump({'version':1,'sources':[]}))
        (self.root/'config/common.toml').write_text('')
        (self.root/'config/defaults.toml').write_text('approval_policy = "on-request"\n')
        (self.root/'BUNDLE-INFO.json').write_text('{"scope":"toolkit"}')
        for p in (patch.object(Path,'home',return_value=self.home), patch.object(core,'ROOT',self.root),
                  contextlib.redirect_stdout(io.StringIO())):
            p.__enter__();self.addCleanup(p.__exit__,None,None,None)

    def test_install_has_no_links_and_does_not_add_accounts(self):
        profile = self.home/'Documents/PowerShell/profile.ps1'
        profile.parent.mkdir(parents=True)
        profile.write_text('# existing profile\n')
        plan = windows.install_changes([])
        self.assertTrue(all(c.link is None for c in plan))
        Transaction(core.state_dir()).apply(plan)
        self.assertEqual(core.load_accounts(), [])
        self.assertIn('# existing profile', profile.read_text(encoding='utf-8-sig'))
        shell = (core.local_dir()/'shell.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('function global:cusage', shell)
        self.assertIn('function global:cuse', shell)
        self.assertIn("source with '' quote", shell)
        self.assertTrue((core.local_dir()/'bin/cusage.cmd').exists())
        self.assertEqual(windows.profile_text(windows.profile_text('# existing profile')), windows.profile_text('# existing profile'))

    def test_windows_add_remove_refreshes_shell_and_preserves_native_home(self):
        with patch.object(accounts,'os',types.SimpleNamespace(name='nt',path=os.path)), patch('codex_env.tools.install_tools',return_value=0):
            args=argparse.Namespace(name='personal',alias=None,dry_run=False)
            self.assertEqual(accounts.register(args),0)
            home=self.home/'.codex-personal'
            self.assertTrue((home/'config.toml').exists())
            (home/'auth.json').write_text('fixture preserved')
            self.assertIn('function global:cpersonal',(core.local_dir()/'shell.ps1').read_text(encoding='utf-8-sig'))
            self.assertEqual(accounts.remove(argparse.Namespace(name='personal',dry_run=False,yes=True)),0)
        self.assertEqual(core.load_accounts(),[])
        self.assertEqual((home/'auth.json').read_text(),'fixture preserved')
        self.assertNotIn('function global:cpersonal',(core.local_dir()/'shell.ps1').read_text(encoding='utf-8-sig'))

    def test_global_and_account_copies_use_same_planned_content(self):
        source=self.root/'assets/skills/demo'
        source.mkdir();(source/'SKILL.md').write_text('new shared content')
        shared=self.home/'.agents/skills/demo'
        account=self.home/'.codex-personal/skills/demo'
        Transaction(core.state_dir()).apply(windows.assets_changes([(shared,source),(account,shared)]))
        self.assertEqual((shared/'SKILL.md').read_text(),'new shared content')
        self.assertEqual((account/'SKILL.md').read_text(),'new shared content')

    def test_asset_copies_update_owned_files_and_reject_user_edits(self):
        source=self.root/'assets/skills/demo'
        source.mkdir();(source/'SKILL.md').write_text('first')
        target=self.home/'.agents/skills/demo'
        Transaction(core.state_dir()).apply(windows.assets_changes([(target,source)]))
        (source/'SKILL.md').write_text('second')
        Transaction(core.state_dir()).apply(windows.assets_changes([(target,source)]))
        self.assertEqual((target/'SKILL.md').read_text(),'second')
        (target/'SKILL.md').write_text('user edit')
        with self.assertRaisesRegex(ValueError,'conflict'):
            windows.assets_changes([(target,source)])


if __name__=='__main__':
    unittest.main()
