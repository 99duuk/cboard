import argparse
import contextlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_env import cli, core
from codex_env.transaction import Change, Transaction, fingerprint


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home with spaces'
        self.root = self.base / 'bundle with spaces'
        self.home.mkdir()
        self.root.mkdir()
        for directory in ['config/accounts', 'assets/skills/existing', 'assets/rules',
                          'bin', 'shell', 'codex_env', 'vendor', 'tests']:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.accounts = [{'id': 'codex', 'label': 'codex', 'home': '~/.codex',
                          'aliases': ['codex'], 'profile': '', 'color': '000000'}]
        (self.root / 'accounts.toml').write_bytes(core.dump({'version': 1, 'accounts': self.accounts}))
        (self.root / 'config/common.toml').write_text('[features]\nunified_exec = true\n')
        (self.root / 'config/defaults.toml').write_text('model = "default-example"\n')
        (self.root / 'config/accounts/codex.toml').write_text('model = "example"\n')
        (self.root / 'assets/AGENTS.md').write_text('Shared guidance\n')
        (self.root / 'assets/skills/existing/SKILL.md').write_text('Existing canonical asset\n')
        (self.root / 'assets/rules/default.rules').write_text('prefix_rule(pattern=["git", "status"], decision="allow")\n')
        for name in ['SPEC.md', 'README.md']:
            (self.root / name).write_text('Fixture documentation\n')
        (self.root / 'sources.toml').write_text('version = 1\n')
        (self.root / 'assets-manifest.json').write_text('{}\n')
        for name in ['cenv', 'cusage', 'creset', 'cnew']:
            (self.root / 'bin' / name).write_text('#!/bin/sh\nexit 0\n')
            (self.root / 'bin' / name).chmod(0o755)
        (self.root / 'shell/accounts.zsh').write_text('# fixture\n')
        (self.home / '.codex').mkdir()
        (self.home / '.codex/config.toml').write_text('model = "example"\n[projects."/preserved"]\ntrust_level = "trusted"\n')
        (self.home / '.codex/auth.json').write_text('{"fixture": "PRIVATE_AUTH_SENTINEL"}\n')
        (self.home / '.zshrc').write_text('export EXISTING_SETTING=preserved\n')
        for patcher in [patch.object(core, 'ROOT', self.root),
                        patch.object(Path, 'home', return_value=self.home),
                        patch.object(core, 'resolve_cli', return_value='/usr/bin/true'),
                        contextlib.redirect_stdout(io.StringIO())]:
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def test_dryrun_with_existing_registry_and_partial_assets_writes_nothing(self):
        (self.root / 'assets-manifest.json').unlink()
        source = self.home / '.agents/skills/additional'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('Additional source skill\n')
        before_root, before_home = fingerprint(self.root), fingerprint(self.home)
        self.assertEqual(cli.sync(dry_run=True), 0)
        self.assertEqual(fingerprint(self.root), before_root)
        self.assertEqual(fingerprint(self.home), before_home)
        self.assertFalse(core.state_dir().exists())
        self.assertEqual(core.ROOT, self.root)

    def test_translate_preserves_expected_for_unchanged_destination(self):
        config = self.home / '.codex/config.toml'
        before = fingerprint(config)
        change = Change(config, content=b'model = "next"\n', expected=before)
        config.write_text('model = "concurrent-edit"\n')
        stage = self.base / 'stage'
        cli.translate([change], stage, self.root)
        self.assertEqual(change.expected, before)
        with self.assertRaisesRegex(RuntimeError, 'Changed since planning'):
            Transaction(core.state_dir()).apply([change])
        self.assertEqual(config.read_text(), 'model = "concurrent-edit"\n')

    def test_export_rejects_literal_authorization_header_and_token_url(self):
        examples = [
            '[mcp_servers.test.http_headers]\nAuthorization = "Bearer TEST_SENTINEL"\n',
            '[mcp_servers.test]\nurl = "https://example.invalid/mcp?access_token=TEST_SENTINEL"\n',
        ]
        for index, content in enumerate(examples):
            with self.subTest(index=index):
                (self.root / 'config/common.toml').write_text(content)
                archive = self.base / f'blocked-{index}.tar.gz'
                with self.assertRaisesRegex(ValueError, '(?i)secret|credential|auth|token|sensitive'):
                    cli.export_bundle(archive, include_shared=True, yes=True)
                self.assertFalse(archive.exists())

    def test_shell_symlink_is_preserved_and_target_updated_once(self):
        dotfile = self.home / '.dotfiles/zsh/.zshrc'
        dotfile.parent.mkdir(parents=True)
        dotfile.write_text('export EXISTING_SETTING=preserved\n')
        shell = self.home / '.zshrc'
        shell.unlink()
        shell.symlink_to(dotfile)
        target_before = fingerprint(shell)
        changes = core.install_changes(core.load_accounts())
        targets = [change.path for change in changes]
        self.assertNotIn(shell, targets)
        self.assertEqual(targets.count(dotfile.resolve()), 1)
        txid = Transaction(core.state_dir()).apply(changes)
        self.assertEqual(fingerprint(shell), target_before)
        self.assertEqual(dotfile.read_text().count(core.BEGIN), 1)
        self.assertIn('EXISTING_SETTING=preserved', dotfile.read_text())
        self.assertTrue(Transaction(core.state_dir()).rollback(txid))
        self.assertEqual(fingerprint(shell), target_before)
        self.assertEqual(dotfile.read_text(), 'export EXISTING_SETTING=preserved\n')

    def test_shell_home_and_arguments_are_literal_with_metacharacters(self):
        home = self.home / 'acct $(printf CENV_UNSAFE) `printf BACKTICK` "quote" \'single'
        account = dict(self.accounts[0], home=str(home))
        fake_cli = self.base / 'fake cli'
        fake_cli.write_text(
            '#!' + sys.executable + '\n'
            'import json, os, sys\n'
            'print(json.dumps({"home": os.environ["CODEX_HOME"], "args": sys.argv[1:]}))\n'
            'raise SystemExit(37)\n')
        fake_cli.chmod(0o755)
        shell = self.root / 'shell/accounts.zsh'
        shell.write_text(core.shell_text([account], str(fake_cli)))
        (self.root/'accounts.toml').write_bytes(core.dump({'version': 1, 'accounts': [account]}))
        source = Path(__file__).resolve().parents[1]
        for name in ['bin', 'codex_env', 'vendor']:
            shutil.copytree(source/name, self.root/name, dirs_exist_ok=True)
        environment = os.environ.copy()
        environment.pop('CODEX_HOME', None)
        environment.pop('TERM_PROGRAM', None)
        environment.update(HOME=str(self.home), CENV_CLI=str(fake_cli), CENV_ACCOUNT='codex')
        result = subprocess.run(
            ['/bin/zsh', '-f', '-c', 'source "$1"; shift; codex "$@"',
             'zsh', str(shell), 'argument with spaces', '한글'],
            env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 37, result.stderr)
        received = json.loads(result.stdout)
        self.assertEqual(received['home'], str(home))
        self.assertEqual(received['args'], ['argument with spaces', '한글'])

    def test_account_add_preserves_dynamic_tool_keys_and_real_root(self):
        def rea_source():
            if core.ROOT != self.root:
                return {}
            return {'mcp_servers': {'rea': {'command': '/test/node',
                    'args': [str(self.root / 'assets/rea.mjs'), 'mcp']}}}

        def plugin_source():
            return {'marketplace': 'assets/plugins'} if core.ROOT == self.root else None

        with patch('codex_env.tools.rea_config', side_effect=rea_source), \
                patch('codex_env.tools._plugin_source', side_effect=plugin_source), \
                patch('codex_env.tools.install_tools', return_value=0) as installer:
            self.assertEqual(cli.sync(), 0)
            existing = self.home / '.codex/config.toml'
            existing_before = existing.read_bytes()
            self.assertEqual(cli.account_add(argparse.Namespace(
                name='extra', alias='cextra', dry_run=False)), 0)
            self.assertEqual(existing.read_bytes(), existing_before)
            for path in [existing, self.home / '.codex-extra/config.toml']:
                config = core.read_toml(path)
                self.assertEqual(config['mcp_servers']['rea']['args'],
                                 [str(self.root / 'assets/rea.mjs'), 'mcp'])
                self.assertEqual(config['marketplaces']['ponytail']['source'],
                                 str(self.root / 'assets/plugins'))
                self.assertEqual(config['marketplaces']['ponytail']['source_type'], 'local')
                self.assertNotIn('cenv-add-', path.read_text())
            installer.assert_called_once()
            self.assertEqual(installer.call_args.args[0][0]['id'], 'extra')

    def test_export_excludes_runtime_secret_in_local_config_and_backup(self):
        sentinel = secrets.token_urlsafe(40).encode()
        config = self.home / '.codex/config.toml'
        config.write_bytes(config.read_bytes() + b'\n[shell_environment_policy.set]\nPRIVATE_VALUE = "' + sentinel + b'"\n')
        txid = Transaction(core.state_dir()).apply([Change(config, content=config.read_bytes() + b'\n')])
        self.assertIn(sentinel, (core.state_dir() / txid / '0').read_bytes())
        archive_path = self.base / 'portable.tar.gz'
        self.assertEqual(cli.export_bundle(archive_path), 0)
        with tarfile.open(archive_path, 'r:gz') as archive:
            names = archive.getnames()
            self.assertTrue(any(name.endswith('MANIFEST.json') for name in names))
            self.assertFalse(any(name.endswith('auth.json') or '/state/' in name for name in names))
            for entry in archive.getmembers():
                if entry.isfile():
                    self.assertNotIn(sentinel, archive.extractfile(entry).read(), entry.name)
        self.assertEqual(archive_path.stat().st_mode & 0o777, 0o600)

    def test_login_cancellation_preserves_registration(self):
        before = (self.root / 'accounts.toml').read_bytes()
        cancelled = subprocess.CompletedProcess(['/usr/bin/true', 'login'], 130)
        output = io.StringIO()
        with patch('codex_env.cli.subprocess.run', return_value=cancelled) as runner, \
                patch('codex_env.usage.read_identity') as identity, \
                contextlib.redirect_stdout(output):
            self.assertEqual(cli.login('codex'), 130)
        identity.assert_not_called()
        self.assertEqual((self.root / 'accounts.toml').read_bytes(), before)
        self.assertEqual(runner.call_args.kwargs['env']['CODEX_HOME'], str(self.home / '.codex'))
        self.assertIn('registration preserved', output.getvalue())
        self.assertIn('cenv account login codex', output.getvalue())

    def test_login_identity_uses_allowlist_and_warns_on_duplicate_email(self):
        accounts = self.accounts + [dict(self.accounts[0], id='extra', home='~/.codex-extra', aliases=['cextra'])]
        (self.root / 'accounts.toml').write_bytes(core.dump({'version': 1, 'accounts': accounts}))
        sentinel = secrets.token_urlsafe(40)
        account_data = {'account': {'type': 'chatgpt', 'email': 'user@example.invalid',
                        'planType': 'plus', 'accessToken': sentinel, 'refreshToken': sentinel}}
        output = io.StringIO()
        with patch('codex_env.cli.subprocess.run', return_value=subprocess.CompletedProcess([], 0)), \
                patch('codex_env.usage.account_request', return_value=account_data), \
                contextlib.redirect_stdout(output):
            self.assertEqual(cli.login('codex'), 0)
        self.assertIn('Login confirmed: user@example.invalid', output.getvalue())
        self.assertIn('WARN same login email: codex, extra', output.getvalue())
        self.assertNotIn(sentinel, output.getvalue())
        self.assertNotIn('accessToken', output.getvalue())
        self.assertNotIn('refreshToken', output.getvalue())

    def test_adopt_preferences_preserves_native_file_and_supports_dryrun(self):
        cli.sync()
        native = self.home/'.codex/config.toml'
        document = core.tomlkit.parse(native.read_text())
        document['model'] = 'native-model-choice'
        document['model_reasoning_effort'] = 'high'
        native.write_text(core.tomlkit.dumps(document))
        before_home, before_root = fingerprint(self.home), fingerprint(self.root)
        self.assertEqual(cli.adopt_preferences('codex',dry_run=True),0)
        self.assertEqual(fingerprint(self.home),before_home)
        self.assertEqual(fingerprint(self.root),before_root)
        native_before = fingerprint(native)
        self.assertEqual(cli.adopt_preferences('codex'),0)
        self.assertEqual(fingerprint(native),native_before)
        source = core.read_toml(self.root/'config/accounts/codex.toml')
        self.assertEqual(source['model'],'native-model-choice')
        self.assertEqual(source['model_reasoning_effort'],'high')
        self.assertFalse(core.config_changes(core.load_accounts()))

    def test_adopt_cannot_accept_security_policy_change(self):
        path = self.root/'config/accounts/codex.toml'
        path.write_text('model = "example"\napproval_policy = "on-request"\n')
        cli.sync()
        native = self.home/'.codex/config.toml'
        document = core.tomlkit.parse(native.read_text())
        document['model'] = 'native-model-choice'
        document['approval_policy'] = 'never'
        native.write_text(core.tomlkit.dumps(document))
        native_before = fingerprint(native)
        self.assertEqual(cli.adopt_preferences('codex'),0)
        self.assertEqual(fingerprint(native),native_before)
        self.assertEqual(core.read_toml(path)['approval_policy'],'on-request')
        with self.assertRaisesRegex(ValueError,'approval_policy'):
            core.config_changes(core.load_accounts())

    def test_registration_preserves_existing_home_preferences(self):
        existing=self.home/'.codex-extra'
        existing.mkdir()
        (existing/'config.toml').write_text('model = "native-special"\nsandbox_mode = "read-only"\n')
        (existing/'auth.json').write_text('existing-auth-fixture')
        with self.assertRaisesRegex(ValueError, 'account home already exists'):
            cli.account_add(argparse.Namespace(name='extra', alias='cextra', dry_run=False))
        from codex_env.accounts import register
        with patch('codex_env.tools.install_tools', return_value=0):
            self.assertEqual(register(argparse.Namespace(name='extra', alias='cextra', home=str(existing), dry_run=False, adopt_existing=False), importing=True), 0)
        source=core.read_toml(self.root/'config/accounts/extra.toml')
        native=core.read_toml(existing/'config.toml')
        self.assertEqual(source['model'],'native-special')
        self.assertEqual(native['model'],'native-special')
        self.assertEqual(source['sandbox_mode'],'read-only')
        self.assertNotIn('approval_policy', source)
        self.assertEqual((existing/'auth.json').read_text(),'existing-auth-fixture')

    def test_account_add_dryrun_then_apply_and_rollback(self):
        before_home, before_root = fingerprint(self.home), fingerprint(self.root)
        args = argparse.Namespace(name='extra', alias='cextra', dry_run=True)
        self.assertEqual(cli.account_add(args), 0)
        self.assertEqual(fingerprint(self.home), before_home)
        self.assertEqual(fingerprint(self.root), before_root)
        args.dry_run = False
        self.assertEqual(cli.account_add(args), 0)
        accounts = core.load_accounts()
        self.assertEqual([a['id'] for a in accounts], ['codex', 'extra'])
        extra = self.home / '.codex-extra'
        self.assertTrue((extra / 'skills').is_symlink())
        self.assertTrue((extra / 'config.toml').is_file())
        self.assertFalse((extra / 'auth.json').exists())
        self.assertIn('cextra()', (self.root / 'shell/accounts.zsh').read_text())
        self.assertIn('PRIVATE_AUTH_SENTINEL', (self.home / '.codex/auth.json').read_text())
        journals = list(core.state_dir().glob('*/journal.json'))
        self.assertEqual(len(journals), 1)
        txid = journals[0].parent.name
        self.assertTrue(Transaction(core.state_dir()).rollback(txid))
        self.assertEqual([a['id'] for a in core.load_accounts()], ['codex'])
        self.assertFalse((self.root / 'config/accounts/extra.toml').exists())
        self.assertFalse((extra / 'config.toml').exists())
        self.assertFalse((extra / 'skills').is_symlink())

    def test_import_conflict_requires_explicit_adoption_and_preserves_native(self):
        from codex_env.accounts import register
        existing = self.home/'.codex-extra'
        existing.mkdir()
        (existing/'config.toml').write_text('[features]\nunified_exec = false\n')
        (existing/'auth.json').write_text('private-fixture')
        args = argparse.Namespace(name='extra', alias='cextra', home=str(existing), dry_run=False, adopt_existing=False)
        before = fingerprint(existing), fingerprint(self.root)
        with self.assertRaisesRegex(ValueError, 'import conflict'):
            register(args, importing=True)
        self.assertEqual((fingerprint(existing), fingerprint(self.root)), before)
        args.adopt_existing = True
        with patch('codex_env.tools.install_tools', return_value=0):
            self.assertEqual(register(args, importing=True), 0)
        self.assertFalse(core.read_toml(existing/'config.toml')['features']['unified_exec'])
        self.assertFalse(core.read_toml(self.root/'config/accounts/extra.toml')['features']['unified_exec'])
        self.assertEqual((existing/'auth.json').read_text(), 'private-fixture')

    def test_remove_preserves_native_data_clears_default_and_rollback_restores_registration(self):
        from codex_env.accounts import remove
        from codex_env.runtime import set_default
        cli.sync()
        set_default('codex')
        native = self.home/'.codex'
        (native/'sessions').mkdir()
        (native/'sessions/history.jsonl').write_text('preserved-history')
        before = fingerprint(native)
        source = (self.root/'config/accounts/codex.toml').read_bytes()
        journals_before = set(core.state_dir().glob('*/journal.json'))
        args = argparse.Namespace(name='codex', dry_run=True, yes=False)
        self.assertEqual(remove(args), 0)
        self.assertEqual(fingerprint(native), before)
        self.assertEqual([a['id'] for a in core.load_accounts()], ['codex'])
        args.dry_run, args.yes = False, True
        self.assertEqual(remove(args), 0)
        self.assertEqual(core.load_accounts(), [])
        self.assertEqual(fingerprint(native), before)
        self.assertIsNone(json.loads((core.local_dir()/'default.json').read_text())['account'])
        self.assertFalse((self.root/'config/accounts/codex.toml').exists())
        journals = set(core.state_dir().glob('*/journal.json'))-journals_before
        self.assertEqual(len(journals), 1)
        self.assertTrue(Transaction(core.state_dir()).rollback(next(iter(journals)).parent.name))
        self.assertEqual([a['id'] for a in core.load_accounts()], ['codex'])
        self.assertEqual((self.root/'config/accounts/codex.toml').read_bytes(), source)
        self.assertEqual(json.loads((core.local_dir()/'default.json').read_text())['account'], 'codex')
        self.assertEqual(fingerprint(native), before)

    def test_local_list_does_not_query_identity_and_online_refresh_uses_allowlist(self):
        with patch('codex_env.usage.read_identity', side_effect=AssertionError('unexpected network')):
            self.assertEqual(cli.account_list(), 0)
        output = io.StringIO()
        with patch('codex_env.usage.read_identity', return_value={'type': 'chatgpt', 'email': 'alice@example.invalid', 'accessToken': 'DO_NOT_CACHE'}), contextlib.redirect_stdout(output):
            self.assertEqual(cli.account_list(online=True), 0)
        cache = (core.local_dir()/'identities.json').read_text()
        self.assertIn('alice@example.invalid', cache)
        self.assertNotIn('DO_NOT_CACHE', cache)
        self.assertNotIn('accessToken', cache)
        output = io.StringIO()
        with patch('codex_env.usage.read_identity', side_effect=AssertionError('unexpected network')), contextlib.redirect_stdout(output):
            self.assertEqual(cli.account_list(), 0)
        self.assertIn('alice@example.invalid', output.getvalue())



if __name__ == '__main__':
    unittest.main()
