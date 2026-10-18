"""Isolated account registry, settings ownership and zsh integration checks."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from codex_env import core, cli


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cenv-core-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base/'home'
        self.root = self.base/'bundle with spaces'
        self.home.mkdir()
        (self.root/'config/accounts').mkdir(parents=True)
        (self.root/'shell').mkdir()
        for patch in (mock.patch.object(Path, 'home', return_value=self.home),
                      mock.patch.object(core, 'ROOT', self.root)):
            patch.start()
            self.addCleanup(patch.stop)
        self.accounts = [self.account('codex', '~/.codex', ['codex']),
                         self.account('work', '~/.codex-work', ['cwork', 'codex-work'])]
        self.write_registry(self.accounts)
        self.write_source({'features': {'multi_agent': True}, 'model': 'common-model'})
        for account in self.accounts:
            (self.root/f'config/accounts/{account["id"]}.toml').write_text('')
        self.config = self.home/'.codex/config.toml'
        self.config.parent.mkdir()
        self.config.write_text('# keep header\nmodel = "original" # keep model comment\n'
                               'notify = ["/local/notification"] # keep notify\n'
                               '[projects."/private/project"]\ntrust_level = "trusted"\n')

    @staticmethod
    def account(name, home, aliases):
        return {'id': name, 'label': aliases[0], 'home': home, 'aliases': aliases,
                'profile': 'Profile Name', 'color': '123abc'}

    def write_registry(self, accounts):
        (self.root/'accounts.toml').write_bytes(core.dump({'version': 1, 'accounts': accounts}))

    def write_source(self, data):
        (self.root/'config/common.toml').write_bytes(core.dump(data))

    @staticmethod
    def materialize(changes):
        # Config-unit fixture application, independent of journal implementation.
        for change in changes:
            change.path.parent.mkdir(parents=True, exist_ok=True)
            change.path.write_bytes(change.content)

    def test_valid_registry_expands_homes(self):
        self.assertEqual(core.load_accounts(), self.accounts)
        self.assertEqual(core.account_home(self.accounts[1]), self.home/'.codex-work')

    def test_cli_resolution_supports_both_homebrew_locations(self):
        with mock.patch.dict(os.environ, {'CENV_CLI': ''}), mock.patch.object(shutil,'which',return_value=None):
            for target in ['/opt/homebrew/bin/codex','/usr/local/bin/codex']:
                with self.subTest(target=target), \
                     mock.patch.object(Path,'is_file',lambda path: str(path)==target), \
                     mock.patch('os.access',return_value=True):
                    self.assertEqual(core.resolve_cli(),target)

    def test_windows_cli_resolves_native_npm_package_without_running_cmd(self):
        package = self.home/'npm/node_modules/@openai/codex-win32-x64'
        binary = package/'vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fixture')
        binary.chmod(0o755)
        (package/'package.json').write_text('{"name":"@openai/codex","version":"0.163.0-win32-x64"}')
        shim = self.home/'npm/codex.cmd'
        shim.write_text('must not execute')
        with mock.patch.object(core.sys, 'platform', 'win32'), \
             mock.patch.object(shutil, 'which', side_effect=lambda name: str(shim) if name == 'codex.cmd' else None), \
             mock.patch.dict(os.environ, {'CENV_CLI': ''}):
            self.assertEqual(core.resolve_cli(), str(binary))
            with mock.patch.dict(os.environ, {'CENV_CLI': str(shim)}):
                with self.assertRaisesRegex(ValueError, 'native executable'):
                    core.resolve_cli()

    def test_registry_schema_and_display_fields_fail_cleanly(self):
        (self.root/'accounts.toml').write_bytes(core.dump({'version':2,'accounts':self.accounts}))
        with self.assertRaisesRegex(ValueError,'version'):
            core.load_accounts()
        for field,value in [('color',123),('label','bad\x1bvalue'),('home','~/.ssh')]:
            items=copy.deepcopy(self.accounts)
            items[1][field]=value
            self.write_registry(items)
            with self.subTest(field=field),self.assertRaises(ValueError):
                core.load_accounts()

    def test_source_edit_during_planning_blocks_all_writes(self):
        before=self.config.read_bytes()
        desired=core.desired_config
        changed=False
        def concurrent_edit(*args,**kwargs):
            nonlocal changed
            result=desired(*args,**kwargs)
            if not changed:
                changed=True
                with (self.root/'config/common.toml').open('a') as stream:
                    stream.write('\n# concurrent source edit\n')
            return result
        with mock.patch.object(core,'desired_config',side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError,'source changed'):
                core.config_changes(self.accounts)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse((core.state_dir()/'managed.json').exists())

    def test_rejects_duplicate_id_alias_or_home(self):
        for field, value in [('id', 'codex'), ('aliases', ['codex']), ('home', '~/.codex')]:
            accounts = copy.deepcopy(self.accounts)
            accounts[1][field] = value
            self.write_registry(accounts)
            with self.subTest(field=field), self.assertRaises(ValueError):
                core.load_accounts()

    def test_rejects_reserved_and_unsafe_identifiers(self):
        for alias in ['cenv', 'cusage', 'creset', 'source', 'eval', 'exec', 'exit', 'bad;name', 'UPPER']:
            accounts = copy.deepcopy(self.accounts)
            accounts[1]['aliases'] = [alias]
            self.write_registry(accounts)
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                core.load_accounts()
        accounts = copy.deepcopy(self.accounts)
        accounts[1]['id'] = '../bad'
        self.write_registry(accounts)
        with self.assertRaises(ValueError):
            core.load_accounts()

    def test_rejects_relative_nested_and_protected_homes(self):
        for home in ['relative', '~/.codex/nested', str(self.home), '/', '~/.agents']:
            accounts = copy.deepcopy(self.accounts)
            accounts[1]['home'] = home
            self.write_registry(accounts)
            with self.subTest(home=home), self.assertRaises(ValueError):
                core.load_accounts()

    def test_accepts_missing_default_alias_but_rejects_control_profile(self):
        accounts = copy.deepcopy(self.accounts)
        accounts[0]['aliases'] = ['main']
        self.write_registry(accounts)
        self.assertEqual(core.load_accounts(), accounts)
        accounts = copy.deepcopy(self.accounts)
        accounts[0]['profile'] = 'bad\x1bprofile'
        self.write_registry(accounts)
        with self.assertRaises(ValueError):
            core.load_accounts()

    def test_updates_managed_values_preserves_unmanaged_and_comments(self):
        self.materialize(core.config_changes(self.accounts[:1]))
        raw = self.config.read_text()
        data = core.read_toml(self.config)
        self.assertEqual(data['model'], 'common-model')
        self.assertTrue(data['features']['multi_agent'])
        self.assertEqual(data['notify'], ['/local/notification'])
        self.assertEqual(data['projects']['/private/project']['trust_level'], 'trusted')
        for comment in ['# keep header', '# keep model comment', '# keep notify']:
            self.assertIn(comment, raw)
        self.assertEqual(core.config_changes(self.accounts[:1]), [])

    def test_source_update_and_deletion_remove_only_owned_keys(self):
        self.materialize(core.config_changes(self.accounts[:1]))
        self.write_source({'model': 'updated'})
        self.materialize(core.config_changes(self.accounts[:1]))
        data = core.read_toml(self.config)
        self.assertEqual(data['model'], 'updated')
        self.assertNotIn('multi_agent', data.get('features', {}))
        self.assertEqual(data['notify'], ['/local/notification'])
        self.assertIn('/private/project', data['projects'])

    def test_external_managed_edit_conflicts_without_touching_files(self):
        self.materialize(core.config_changes(self.accounts[:1]))
        self.config.write_text(self.config.read_text().replace('common-model', 'external-model'))
        before = self.config.read_bytes()
        self.write_source({'model': 'next-model'})
        with self.assertRaisesRegex(ValueError, 'conflict at model'):
            core.config_changes(self.accounts[:1])
        self.assertEqual(self.config.read_bytes(), before)

    def test_deletion_conflicts_with_external_edit(self):
        self.materialize(core.config_changes(self.accounts[:1]))
        self.config.write_text(self.config.read_text().replace('common-model', 'external-model'))
        self.write_source({})
        with self.assertRaisesRegex(ValueError, 'conflict at model'):
            core.config_changes(self.accounts[:1])

    def test_external_value_equal_to_new_source_is_accepted(self):
        self.materialize(core.config_changes(self.accounts[:1]))
        self.config.write_text(self.config.read_text().replace('common-model', 'next-model'))
        self.write_source({'model': 'next-model'})
        self.materialize(core.config_changes(self.accounts[:1]))
        self.assertEqual(core.read_toml(self.config)['model'], 'next-model')

    def test_account_override_and_portable_path_expansion(self):
        self.write_source({'model': 'common', 'agents': {'worker': {
            'config_file': '${ROOT}/assets/agents/worker.toml'}}})
        (self.root/'config/accounts/codex.toml').write_text('model = "override"\n')
        values = core.desired_config(self.accounts[0])
        self.assertEqual(values[('model',)], 'override')
        self.assertEqual(values[('agents', 'worker', 'config_file')], str(self.root/'assets/agents/worker.toml'))

    def test_rejects_secrets_and_app_owned_source_keys(self):
        for source in [{'notify': ['anything']}, {'mcp_servers': {'node_repl': {'command': 'node'}}},
                       {'mcp_servers': {'remote': {'api_key': 'never-portable'}}}]:
            self.write_source(source)
            with self.subTest(source=source), self.assertRaises(ValueError):
                core.desired_config(self.accounts[0])

    @unittest.skipUnless(shutil.which('zsh'), 'zsh required')
    def test_generated_shell_quotes_args_and_does_not_leak_account_home(self):
        executable = self.base/'fake cli with spaces'
        executable.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                              'print(json.dumps({"home":os.getenv("CODEX_HOME"),"args":sys.argv[1:]}))\n')
        executable.chmod(0o755)
        shell = self.root/'shell/accounts.zsh'
        shell.write_text(core.shell_text(self.accounts, str(executable)))
        source = Path(__file__).resolve().parents[1]
        for name in ['bin', 'codex_env', 'vendor']:
            shutil.copytree(source/name, self.root/name, dirs_exist_ok=True)
        arguments = ['two words', '$(touch injected)', '; echo injection', 'single\'quote', '']
        command = 'source "$1"; shift; unset CODEX_HOME; cwork "$@"; print -r -- "${CODEX_HOME-unset}"'
        result = subprocess.run(['zsh', '-f', '-c', command, '--', str(shell), *arguments],
                                env={**os.environ, 'HOME': str(self.home), 'CENV_CLI': str(executable)}, cwd=self.base, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(json.loads(lines[0]), {'home': str(self.home/'.codex-work'), 'args': arguments})
        self.assertEqual(lines[1], 'unset')
        self.assertFalse((self.base/'injected').exists())
        command = 'source "$1"; CODEX_HOME=sentinel; export CODEX_HOME; cwork arg; print -r -- "$CODEX_HOME"'
        result = subprocess.run(['zsh','-f','-c',command,'--',str(shell)],
                                env={**os.environ,'HOME':str(self.home),'CENV_CLI':str(executable)},cwd=self.base,text=True,capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1], 'sentinel')

    def test_shell_block_update_is_idempotent_and_preserves_unrelated_content(self):
        old = '# personal shell\nexport MY_SETTING=ok\n'
        first = core.update_shell(old)
        self.assertEqual(core.update_shell(first), first)
        self.assertIn('export MY_SETTING=ok', first)
        self.assertEqual(first.count(core.BEGIN), 1)
        with self.assertRaises(ValueError):
            core.update_shell(core.BEGIN+'\nmissing end\n')

    def test_account_add_dry_run_does_not_change_registry_or_home(self):
        before = (self.root/'accounts.toml').read_bytes()
        args = types.SimpleNamespace(name='new', alias='cadded', dry_run=True)
        with mock.patch.object(core, 'resolve_cli', return_value='/fake/codex'), \
             mock.patch.object(cli, 'asset_links', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.account_add(args), 0)
        self.assertEqual((self.root/'accounts.toml').read_bytes(), before)
        self.assertFalse((self.root/'config/accounts/new.toml').exists())
        self.assertFalse((self.home/'.codex-new').exists())
        self.assertFalse(core.state_dir().exists())

    def test_account_add_registers_and_preserves_existing_auth(self):
        auth = self.config.parent/'auth.json'
        auth.write_text('local credential fixture')
        args = types.SimpleNamespace(name='new', alias='cadded', dry_run=False)
        with mock.patch.object(core, 'resolve_cli', return_value='/fake/codex'), \
             mock.patch.object(cli, 'asset_links', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.account_add(args), 0)
        added = core.load_accounts()[-1]
        self.assertEqual(added['id'], 'new')
        self.assertEqual(core.account_home(added), self.home/'.codex-new')
        self.assertTrue((self.home/'.codex-new/config.toml').is_file())
        self.assertFalse((self.home/'.codex-new/auth.json').exists())
        self.assertEqual(auth.read_text(), 'local credential fixture')
        self.assertIn('cadded()', (self.root/'shell/accounts.zsh').read_text())
        self.assertEqual(core.read_toml(self.root/'config/accounts/new.toml')['sandbox_mode'], 'workspace-write')


if __name__ == '__main__':
    unittest.main()
