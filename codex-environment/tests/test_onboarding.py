"""Fresh installations and account onboarding without inherited personal accounts."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from codex_env import cli, core
from codex_env.transaction import fingerprint


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cenv-onboarding-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'another person home'
        self.root = self.base / 'portable bundle'
        self.home.mkdir()
        for name in ['config/accounts', 'assets/skills', 'assets/rules', 'assets/agents',
                     'bin', 'shell', 'codex_env', 'vendor', 'tests']:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ['SPEC.md', 'README.md', 'sources.toml', 'assets-manifest.json']:
            (self.root / name).write_text('{}\n' if name.endswith('.json') else '')
        (self.root / 'assets/AGENTS.md').write_text('Shared instructions\n')
        (self.root / 'config/common.toml').write_text('[features]\nunified_exec = true\n')
        (self.root / 'config/defaults.toml').write_text('model = "fresh-default"\nmodel_reasoning_effort = "medium"\n')
        (self.root / 'shell/accounts.zsh').write_text('')
        self.write_accounts([])
        for patcher in [patch.object(core, 'ROOT', self.root),
                        patch.object(Path, 'home', return_value=self.home),
                        patch.object(core, 'resolve_cli', return_value='/usr/bin/true'),
                        patch('codex_env.tools.install_tools', return_value=0),
                        contextlib.redirect_stdout(io.StringIO())]:
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def write_accounts(self, accounts):
        (self.root / 'accounts.toml').write_bytes(core.dump({'version': 1, 'accounts': accounts}))

    @staticmethod
    def args(**values):
        return argparse.Namespace(**dict(name=None, alias=None, dry_run=False, no_login=False, **values))

    def test_empty_registry_and_account_without_codex_alias_are_valid(self):
        self.assertEqual(core.load_accounts(), [])
        account = {'id': 'alice', 'home': '~/.codex-alice', 'aliases': ['calice']}
        self.write_accounts([account])
        self.assertEqual(core.load_accounts()[0]['id'], 'alice')

    def test_first_account_uses_shared_defaults_not_previous_owner_preferences(self):
        (self.root / 'config/accounts/codex.toml').write_text('model = "previous-owner-model"\n')
        self.assertEqual(cli.account_add(argparse.Namespace(name='alice', alias='calice', dry_run=False)), 0)
        self.assertEqual([a['id'] for a in core.load_accounts()], ['alice'])
        for path in [self.root / 'config/accounts/alice.toml', self.home / '.codex-alice/config.toml']:
            self.assertEqual(core.read_toml(path)['model'], 'fresh-default')
        self.assertFalse((self.home / '.codex-work').exists())

    def test_export_defaults_to_no_accounts_and_explicit_migration_preserves_them(self):
        account = {'id': 'alice', 'home': '~/.codex-alice', 'aliases': ['calice']}
        self.write_accounts([account])
        (self.root / 'config/accounts/alice.toml').write_text('model = "personal-choice"\n')
        for include_accounts in [False, True]:
            with self.subTest(include_accounts=include_accounts):
                archive = self.base / f'export-{include_accounts}.tar.gz'
                kwargs = {'include_accounts': True} if include_accounts else {}
                self.assertEqual(cli.export_bundle(archive, **kwargs), 0)
                with tarfile.open(archive) as package:
                    registry = core.tomllib.loads(package.extractfile('codex-environment/accounts.toml').read().decode())
                    self.assertEqual([a['id'] for a in registry['accounts']], ['alice'] if include_accounts else [])
                    self.assertEqual('codex-environment/config/accounts/alice.toml' in package.getnames(), include_accounts)
                    self.assertIn('codex-environment/config/defaults.toml', package.getnames())

    def test_new_prompts_for_name_and_accepts_default_alias_then_logs_in(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['alice', '']), \
                patch.object(cli, 'account_add', return_value=0) as add, \
                patch.object(cli, 'login', return_value=0) as login:
            self.assertEqual(cli.account_new(self.args()), 0)
        self.assertEqual((add.call_args.args[0].name, add.call_args.args[0].alias), ('alice', 'calice'))
        login.assert_called_once_with('alice')

    def test_new_dryrun_and_no_login_do_not_open_login(self):
        for flag in ['dry_run', 'no_login']:
            values = dict(name='alice', alias='calice', dry_run=False, no_login=False)
            values[flag] = True
            with self.subTest(flag=flag), patch.object(cli, 'account_add', return_value=0) as add, \
                    patch.object(cli, 'login') as login:
                self.assertEqual(cli.account_new(argparse.Namespace(**values)), 0)
                self.assertEqual(add.call_args.args[0].dry_run, flag == 'dry_run')
                login.assert_not_called()

    def test_new_codex_uses_isolated_home_and_cancel_keeps_registration(self):
        with patch.object(cli, 'login', return_value=130) as login:
            result = cli.account_new(argparse.Namespace(name='codex', alias=None, dry_run=False, no_login=False))
        self.assertEqual(result, 130)
        self.assertEqual(core.load_accounts()[0]['aliases'], ['ccodex'])
        self.assertEqual(core.account_home(core.load_accounts()[0]), self.home / '.codex-codex')
        login.assert_called_once_with('codex')

    def test_new_registration_failure_does_not_login(self):
        with patch.object(cli, 'account_add', return_value=1), patch.object(cli, 'login') as login:
            self.assertEqual(cli.account_new(argparse.Namespace(name='alice', alias='calice', dry_run=False, no_login=False)), 1)
        login.assert_not_called()

    def test_empty_shell_rejects_implicit_account_and_reload_preserves_status_and_args(self):
        fake_cli = self.base / 'fake cli'
        fake_cli.write_text('#!/bin/sh\nexit 99\n')
        fake_cli.chmod(0o755)
        shell = self.root / 'shell/accounts.zsh'
        shell.write_text(core.shell_text([], str(fake_cli)))
        pointer = self.home / '.config/codex-environment/shell.zsh'
        pointer.parent.mkdir(parents=True)
        pointer.symlink_to(shell)
        dispatcher = self.root / 'bin/cenv'
        record = self.base / 'args.json'
        dispatcher.write_text('#!' + sys.executable + '\nimport json, pathlib, sys\n'
                              'if sys.argv[1] == "_default": raise SystemExit(0)\n'
                              'if sys.argv[1] == "_run": raise SystemExit(1)\n'
                              f'pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n'
                              f'with pathlib.Path({str(shell)!r}).open("a") as stream:\n'
                              ' stream.write("\\nnew_account_loaded() { print reloaded; }\\n")\n'
                              'raise SystemExit(130)\n')
        dispatcher.chmod(0o755)
        environment = dict(os.environ, HOME=str(self.home))
        for key in ['CODEX_HOME', 'CENV_ACCOUNT', 'CENV_DEFAULT_ACCOUNT']:
            environment.pop(key, None)
        arguments = ['alice', 'literal $(touch UNSAFE)', '한글']
        result = subprocess.run(['/bin/zsh', '-f', '-c',
                                 'source "$1"; shift; codex; print "empty_status=$?"; cnew "$@"; status_code=$?; new_account_loaded; exit "$status_code"',
                                 '--', str(shell), *arguments], env=environment,
                                capture_output=True, text=True, timeout=10, cwd=self.base)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['empty_status=1', 'reloaded'])
        self.assertEqual(json.loads(record.read_text()), ['account', 'add', *arguments])
        self.assertFalse((self.base / 'UNSAFE').exists())

    def test_tool_failure_keeps_registration_and_still_attempts_login(self):
        with patch('codex_env.tools.install_tools', return_value=1), patch.object(cli, 'login', return_value=0) as login:
            result = cli.account_new(argparse.Namespace(name='alice', alias='calice', dry_run=False, no_login=False))
        self.assertEqual(result, 1)
        self.assertEqual([a['id'] for a in core.load_accounts()], ['alice'])
        login.assert_called_once_with('alice')

    def test_new_dryrun_keeps_empty_installation_unchanged(self):
        before = fingerprint(self.root), fingerprint(self.home)
        with patch.object(cli, 'login') as login:
            self.assertEqual(cli.account_new(argparse.Namespace(name='alice', alias='calice', dry_run=True, no_login=False)), 0)
        self.assertEqual((fingerprint(self.root), fingerprint(self.home)), before)
        login.assert_not_called()


if __name__ == '__main__':
    unittest.main()
