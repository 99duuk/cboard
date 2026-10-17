"""Export the actual declarative bundle and install it into an unrelated home."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from codex_env import cli, core, tools

BUNDLE = Path(__file__).resolve().parents[1]


class PortabilityTests(unittest.TestCase):
    def test_export_relocate_install_and_missing_auth_diagnosis(self):
        original_home = str(Path.home())
        with tempfile.TemporaryDirectory(prefix='cenv-portability-') as temporary:
            base = Path(temporary).resolve()
            archive = base/'export.tar.gz'
            fake_node = base/'fake node executable'
            fake_node.write_text('#!/bin/sh\nprintf "v26.0.0\\n"\n')
            fake_node.chmod(0o755)
            with mock.patch.object(core, 'ROOT', BUNDLE), \
                 mock.patch.object(tools, 'node_path', return_value=str(fake_node)), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.export_bundle(str(archive), include_accounts=True, yes=True), 0)
            destination = base/'different destination with spaces'
            destination.mkdir()
            with tarfile.open(archive) as package:
                package.extractall(destination, filter='data')
            relocated = destination/'codex-environment'
            manifest = json.loads((relocated/'MANIFEST.json').read_text())['files']
            for name, expected in manifest.items():
                self.assertEqual(hashlib.sha256((relocated/name).read_bytes()).hexdigest(), expected, name)
            self.assertFalse((relocated/'shell/accounts.zsh').exists())
            self.assertFalse(any(p.name == 'auth.json' for p in relocated.rglob('*')))
            rule = relocated/'assets/rules/default.rules'
            self.assertIn('${HOME}', rule.read_text())
            self.assertNotIn(original_home, rule.read_text())
            # The exported skill is self-contained without the original Git checkout.
            self.assertTrue((relocated/'assets/skills/uia-e/SKILL.md').is_file())
            self.assertFalse(any(p.is_symlink() for p in (relocated/'assets').rglob('*')))

            new_home = base/'new Mac home with spaces'
            new_home.mkdir()
            fake_cli = base/'fake codex executable'
            fake_cli.write_text('#!/bin/sh\nprintf "%s\\n" "$CODEX_HOME"\n')
            fake_cli.chmod(0o755)
            with mock.patch.object(core, 'ROOT', relocated), \
                 mock.patch.object(Path, 'home', return_value=new_home), \
                 mock.patch.object(core, 'resolve_cli', return_value=str(fake_cli)), \
                 mock.patch.object(tools, 'node_path', return_value=str(fake_node)), \
                 mock.patch.object(tools, 'install_tools', side_effect=AssertionError('unexpected provider install')), \
                 mock.patch.dict(os.environ, {'HOME': str(new_home)}), \
                 contextlib.redirect_stdout(io.StringIO()):
                before = rule.read_bytes()
                self.assertEqual(cli.sync(dry_run=True, install=True), 0)
                self.assertEqual(rule.read_bytes(), before)
                self.assertFalse((new_home/'.codex').exists())
                self.assertFalse(core.state_dir().exists())
                self.assertEqual(cli.sync(install=True), 0)
                accounts = core.load_accounts()
                self.assertNotIn('${HOME}', rule.read_text())
                self.assertNotIn(original_home, rule.read_text())
                self.assertIn(str(new_home), rule.read_text())
                self.assertEqual((new_home/'.agents/rules/default.rules').resolve(), rule)
                self.assertEqual((new_home/'.agents/skills/uia-e').resolve(), relocated/'assets/skills/uia-e')
                for account in accounts:
                    home = core.account_home(account)
                    config = (home/'config.toml').read_text()
                    self.assertNotIn(original_home, config)
                    self.assertNotIn('${ROOT}', config)
                    self.assertNotIn('${HOME}', config)
                    parsed = core.read_toml(home/'config.toml')
                    self.assertEqual(parsed['mcp_servers']['rea']['command'], str(fake_node))
                    self.assertTrue(parsed['mcp_servers']['rea']['args'][0].startswith(str(new_home)))
                    for agent in parsed['agents'].values():
                        if isinstance(agent, dict) and 'config_file' in agent:
                            self.assertTrue(agent['config_file'].startswith(str(relocated)))
                    self.assertEqual((home/'skills/uia-e').resolve(), relocated/'assets/skills/uia-e')
                    self.assertFalse((home/'auth.json').exists())
                shell = (relocated/'shell/accounts.zsh').read_text()
                self.assertNotIn(original_home, shell)
                self.assertIn(str(relocated), shell)
                self.assertEqual(core.config_changes(accounts), [])
                self.assertEqual(cli.sync(install=True), 0)
                diagnosis = io.StringIO()
                with contextlib.redirect_stdout(diagnosis):
                    self.assertEqual(cli.doctor(), 1)
                report = diagnosis.getvalue()
                self.assertEqual(report.count(': login required'), len(accounts))
                self.assertNotIn('sync required:', report)
                self.assertNotIn(': missing skills', report)
                self.assertIn('REA', report)
                self.assertRegex(report, r'Ponytail(?: [0-9]+\.[0-9]+\.[0-9]+)? missing')
                if shutil.which('zsh'):
                    result = subprocess.run(['zsh','-f','-c','source "$1"; cwork --version',
                                             '--',str(relocated/'shell/accounts.zsh')],
                                            env={**os.environ, 'HOME': str(new_home), 'CENV_CLI': str(fake_cli)}, capture_output=True,text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), str(new_home/'.codex-work'))

    @unittest.skipUnless(shutil.which('zsh'), 'zsh required')
    def test_default_toolkit_new_user_two_profiles_full_shell_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='cenv-new-user-') as temporary:
            base = Path(temporary).resolve()
            archive = base/'toolkit.tar.gz'
            with mock.patch.object(core, 'ROOT', BUNDLE), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.export_bundle(archive), 0)
            destination = base/'new install with spaces'
            destination.mkdir()
            with tarfile.open(archive) as package:
                package.extractall(destination, filter='data')
            relocated = destination/'codex-environment'
            new_home = base/'someone else home'
            new_home.mkdir()
            agents = new_home/'.agents/AGENTS.md'
            agents.parent.mkdir()
            agents.write_text('existing-user-instructions')
            fake_cli = base/'fake codex'
            fake_cli.write_text('#!/usr/bin/env python3\nimport json, os, sys\nprint(json.dumps({"home": os.environ.get("CODEX_HOME"), "args": sys.argv[1:]}))\n')
            fake_cli.chmod(0o755)
            env = dict(os.environ, HOME=str(new_home), CENV_CLI=str(fake_cli))
            for key in ['CODEX_HOME', 'CENV_ACCOUNT', 'CENV_DEFAULT_ACCOUNT']:
                env.pop(key, None)
            install = subprocess.run([str(relocated/'bin/cenv'), 'install'], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertEqual(agents.read_text(), 'existing-user-instructions')
            self.assertEqual(core.read_toml(relocated/'accounts.toml')['accounts'], [])
            script = r'''source "$HOME/.config/codex-environment/shell.zsh" || exit $?
                cnew alpha --no-login || exit $?
                (( ${+functions[calpha]} )) || exit 51
                cnew beta --alias cbeta --no-login || exit $?
                (( ${+functions[cbeta]} )) || exit 52
                cuse alpha || exit $?
                codex 'two words' '한글' '$(touch UNSAFE)' || exit $?
                crun beta -- 'one shot' || exit $?
                cdefault beta || exit $?
                print -r -- 'native-history' > "$HOME/.codex-alpha/history.jsonl"
                cdel alpha --yes || exit $?
                (( ! ${+functions[calpha]} )) || exit 53
                [[ -z "${CENV_ACCOUNT:-}" ]] || exit 54
                cenv account import alpha --home "$HOME/.codex-alpha" || exit $?
                (( ${+functions[calpha]} )) || exit 55
                calpha restored || exit $?
                clist || exit $?
                chelp || exit $?'''
            result = subprocess.run(['zsh', '-f', '-c', script], env=env, cwd=base, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr+'\n'+result.stdout)
            records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            self.assertEqual(records, [
                {'home': str(new_home/'.codex-alpha'), 'args': ['two words', '한글', '$(touch UNSAFE)']},
                {'home': str(new_home/'.codex-beta'), 'args': ['one shot']},
                {'home': str(new_home/'.codex-alpha'), 'args': ['restored']}])
            self.assertFalse((base/'UNSAFE').exists())
            self.assertEqual((new_home/'.codex-alpha/history.jsonl').read_text().strip(), 'native-history')
            self.assertFalse(any(path.name == 'auth.json' for path in new_home.rglob('*')))
            self.assertEqual([item['id'] for item in core.read_toml(relocated/'accounts.toml')['accounts']], ['beta', 'alpha'])
            fresh = subprocess.run(['zsh', '-f', '-c', 'source "$HOME/.config/codex-environment/shell.zsh"; codex fresh'], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            self.assertEqual(json.loads(fresh.stdout), {'home': str(new_home/'.codex-beta'), 'args': ['fresh']})



if __name__ == '__main__':
    unittest.main()
