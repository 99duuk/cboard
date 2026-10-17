"""Manager failure paths and cross-command contracts in isolated user homes."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_env import accounts, cli, core, usage
from codex_env.transaction import Transaction, fingerprint


class ManagerV2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cenv-manager-v2-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)/'other user'
        self.root = Path(temporary.name)/'portable bundle'
        self.home.mkdir()
        for directory in ['config/accounts','assets/skills','assets/rules','assets/agents','shell','bin']:
            (self.root/directory).mkdir(parents=True)
        (self.root/'accounts.toml').write_bytes(core.dump({'version':1,'accounts':[]}))
        (self.root/'config/common.toml').write_text('[features]\nunified_exec=true\n')
        (self.root/'config/defaults.toml').write_text('model="fresh-default"\n')
        (self.root/'sources.toml').write_text('version=1\n')
        (self.root/'assets-manifest.json').write_text('{}\n')
        (self.root/'assets/AGENTS.md').write_text('Shared instructions\n')
        (self.root/'shell/accounts.zsh').write_text('')
        self.output = io.StringIO()
        for patcher in [patch.object(core,'ROOT',self.root), patch.object(Path,'home',return_value=self.home),
                        patch.object(core,'resolve_cli',return_value='/usr/bin/true'),
                        patch('codex_env.tools.install_tools',return_value=0),
                        contextlib.redirect_stdout(self.output)]:
            patcher.__enter__()
            self.addCleanup(patcher.__exit__,None,None,None)

    @staticmethod
    def args(name='alice', **options):
        values=dict(name=name,alias=None,dry_run=False,no_login=False)
        values.update(options)
        return argparse.Namespace(**values)

    def register(self, name='alice'):
        self.assertEqual(cli.account_add(self.args(name)),0)
        return core.account_home(accounts.find(name))

    def test_tool_subprocess_failure_still_attempts_login(self):
        with patch('codex_env.tools.install_tools',side_effect=subprocess.CalledProcessError(1,['node','--version'])), patch.object(cli,'login',return_value=0) as login:
            self.assertEqual(cli.account_new(self.args()),1)
        login.assert_called_once_with('alice')
        self.assertEqual(accounts.find('alice')['id'],'alice')
        self.assertIn('Tools incomplete',self.output.getvalue())

    def test_default_change_during_remove_confirmation_blocks_removal(self):
        self.register()
        path=core.local_dir()/'default.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{"account":null}\n')
        before=fingerprint(self.root/'accounts.toml')
        def confirm(_):
            path.write_text('{"account":"alice"}\n')
            return 'yes'
        with patch('sys.stdin.isatty',return_value=True), patch('builtins.input',side_effect=confirm):
            with self.assertRaisesRegex(RuntimeError,'Changed since planning'):
                accounts.remove(argparse.Namespace(name='alice',dry_run=False,yes=False))
        self.assertEqual(fingerprint(self.root/'accounts.toml'),before)
        self.assertTrue((self.root/'config/accounts/alice.toml').exists())
        self.assertEqual(json.loads(path.read_text()),{'account':'alice'})

    def test_import_cannot_claim_adoption_of_machine_owned_tool_path(self):
        home=self.home/'.existing'
        home.mkdir()
        config=home/'config.toml'
        config.write_text('[mcp_servers.rea]\ncommand="/native/rea"\n')
        before=fingerprint(home)
        with patch.object(core,'shared_config',return_value=({}, {('mcp_servers','rea','command'):'/machine/rea'})):
            with self.assertRaisesRegex(ValueError,'machine-owned import conflict'):
                accounts.register(self.args(home=str(home),adopt_existing=True),importing=True)
        self.assertEqual(fingerprint(home),before)
        self.assertEqual(core.load_accounts(),[])

    def test_source_change_after_register_planning_blocks_application(self):
        original=Transaction.apply
        source=self.root/'config/common.toml'
        def race(transaction, changes, dry_run=False):
            source.write_text('[features]\nunified_exec=false\n')
            return original(transaction,changes,dry_run=dry_run)
        with patch.object(Transaction,'apply',race):
            with self.assertRaisesRegex(RuntimeError,'Changed since planning'):
                cli.account_add(self.args())
        self.assertEqual(core.load_accounts(),[])
        self.assertFalse((self.home/'.codex-alice').exists())

    def test_native_change_after_adopt_planning_blocks_source_update(self):
        home=self.register()
        source=self.root/'config/accounts/alice.toml'
        before=fingerprint(source)
        native=home/'config.toml'
        original=Transaction.apply
        def race(transaction,changes,dry_run=False):
            native.write_text('model="concurrent-choice"\n')
            return original(transaction,changes,dry_run=dry_run)
        with patch.object(Transaction,'apply',race):
            with self.assertRaisesRegex(RuntimeError,'Changed since planning'):
                cli.adopt_preferences('alice')
        self.assertEqual(fingerprint(source),before)
        self.assertEqual(core.read_toml(native)['model'],'concurrent-choice')

    def test_remove_preserves_native_home_and_clears_identity_cache(self):
        home=self.register()
        (home/'auth.json').write_text('{"fixture":"PRIVATE"}\n')
        (home/'sessions').mkdir()
        (home/'sessions/conversation.jsonl').write_text('history\n')
        before=fingerprint(home)
        cache=core.local_dir()/'identities.json'
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps({'alice':{'confirmed':True,'identity':{'email':'alice@example.invalid'}}}))
        self.assertEqual(accounts.remove(argparse.Namespace(name='alice',dry_run=False,yes=True)),0)
        self.assertEqual(fingerprint(home),before)
        self.assertNotIn('alice',json.loads(cache.read_text()))
        self.assertEqual(core.load_accounts(),[])
        self.assertFalse((self.root/'config/accounts/alice.toml').exists())
        self.assertEqual(len(list((self.root/'config/removed').glob('alice-*.json'))),1)

    def test_usage_filter_empty_and_sanitized_cached_identity(self):
        with patch.object(usage,'snapshot') as snapshot:
            self.assertEqual(usage.main([]),0)
            snapshot.assert_not_called()
        self.register()
        account=accounts.find('alice')
        cache=core.local_dir()/'identities.json'
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps({'alice':{'confirmed':True,'identity':{'email':'alice\u001b[31m@example.invalid'},'checked_at':'2026-10-17'}}))
        with patch.object(usage,'account_request',return_value={'rateLimits':{'planType':'plus'}}):
            result=usage.fetch_account(account)
        self.assertIn('alice',result['name'])
        self.assertIn('cached 2026-10-17',result['identity'])
        self.assertNotIn('\u001b',result['identity'])
        with patch.object(usage,'snapshot',return_value=[]) as snapshot, patch.object(usage,'render'):
            self.assertEqual(usage.main(['calice']),0)
        self.assertEqual([a['id'] for a in snapshot.call_args.args[0]],['alice'])

    def test_input_errors_and_external_use_return_two(self):
        with patch('sys.stdin.isatty',return_value=False), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['account','add']),2)
            self.assertEqual(cli.main(['use','alice']),2)
        self.assertEqual(core.load_accounts(),[])

    def test_alias_cannot_shadow_another_account_identity(self):
        self.register('alice')
        before=fingerprint(self.root/'accounts.toml')
        with self.assertRaisesRegex(argparse.ArgumentError,'collides'):
            cli.account_add(self.args('calice'))
        self.assertEqual(fingerprint(self.root/'accounts.toml'),before)
        self.assertFalse((self.home/'.codex-calice').exists())

    def test_install_previews_unregistered_native_home_without_import_or_changes(self):
        native=self.home/'.codex'
        native.mkdir()
        config=native/'config.toml'
        config.write_text('model="existing-native"\n')
        before=fingerprint(self.home),fingerprint(self.root)
        self.assertEqual(cli.sync(dry_run=True,install=True),0)
        self.assertEqual((fingerprint(self.home),fingerprint(self.root)),before)
        self.assertEqual(core.load_accounts(),[])
        self.assertIn('unregistered native Codex home preserved',self.output.getvalue())
        self.assertIn('account import existing',self.output.getvalue())

    def test_new_codex_uses_ccodex_alias_and_isolated_home(self):
        with patch.object(cli,'login',return_value=130):
            self.assertEqual(cli.account_new(self.args('codex')),130)
        account=accounts.find('codex')
        self.assertEqual(account['aliases'],['ccodex'])
        self.assertEqual(core.account_home(account),self.home/'.codex-codex')


if __name__ == '__main__':
    unittest.main()
