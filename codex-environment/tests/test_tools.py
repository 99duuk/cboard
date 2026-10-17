import contextlib
import io
import json
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_env import core, tools
from codex_env.assets import tree_hash


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)/'home'
        self.home.mkdir()
        self.root = Path(self.tmp.name)/'bundle'
        self.root.mkdir()
        self.plugin = self.root/'assets/plugins/ponytail'
        (self.plugin/'.codex-plugin').mkdir(parents=True)
        (self.plugin/'.codex-plugin/plugin.json').write_text(json.dumps({'name':'ponytail','version':'5.2.0'}))
        descriptor = self.root/'assets/plugins/.agents/plugins'
        descriptor.mkdir(parents=True)
        (descriptor/'marketplace.json').write_text(json.dumps({'name':'ponytail','plugins':[{'name':'ponytail','source':{'source':'local','path':'./ponytail'}}]}))
        self.accounts = [{'id': 'one', 'home': '~/.codex'}, {'id': 'two', 'home': '~/.codex-two'}]
        self.sources = [
            {'kind': 'marketplace', 'name': 'ponytail', 'source': 'https://example.com/ponytail.git', 'revision': 'a'*40},
            {'kind': 'plugin', 'name': 'ponytail@ponytail', 'enabled': True, 'version':'5.2.0',
             'path':'assets/plugins/ponytail','marketplace':'assets/plugins','sha256':tree_hash(self.plugin)},
            {'kind': 'plugin', 'name': 'omo@sisyphuslabs', 'enabled': False},
            {'kind': 'plugin', 'name': 'browser@openai-bundled', 'enabled': True},
        ]
        patches = [patch.object(Path, 'home', return_value=self.home),
                   patch.object(core, 'ROOT', self.root),
                   patch.object(core, 'resolve_cli', return_value='/usr/bin/codex'),
                   patch.object(tools, '_sources', side_effect=lambda: self.sources)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_plans_only_enabled_user_plugin_per_account_and_dry_run_has_no_writes(self):
        with patch.object(tools.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            plan = tools.tool_plan(self.accounts)
            self.assertEqual(len(plan), 2)
            self.assertEqual([p['account'] for p in plan], ['one','two'])
            self.assertNotIn('omo', str(plan))
            self.assertNotIn('browser', str(plan))
            self.assertEqual(tools.install_tools(self.accounts, dry_run=True), 0)
            run.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_matching_plugin_is_noop_and_missing_cache_reinstalls_plugin_only(self):
        home = self.home/'.codex'
        home.mkdir()
        (home/'config.toml').write_bytes(core.dump({
            'marketplaces': {'ponytail': {'source': self.sources[0]['source'], 'last_revision': 'a'*40}},
            'plugins': {'ponytail@ponytail': {'enabled': True}}}))
        self.assertEqual(len(tools.tool_plan(self.accounts[:1])), 1)
        directory = home/'plugins/cache/ponytail/ponytail/5.2.0'
        shutil.copytree(self.plugin, directory)
        self.assertEqual(tools.tool_plan(self.accounts[:1]), [])

    def test_provider_failure_keeps_private_backup_and_stops(self):
        account = self.home/'.codex'
        account.mkdir()
        original = b'keep = "original"\n'
        (account/'config.toml').write_bytes(original)
        with patch.object(tools.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'provider')) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tools.install_tools(self.accounts), 1)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(account))
        backups = list((core.state_dir()/'tools').glob('*/one.toml'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_tampered_canonical_source_rejected_before_provider(self):
        (self.plugin/'unexpected.txt').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            tools.tool_plan(self.accounts)

    def test_rea_pinned_shared_install_and_portable_fragment(self):
        self.sources.append({'kind':'npm','name':'rea-agents','version':'6.5.0'})
        with patch.object(tools.shutil, 'which', return_value='/usr/bin/npm'), patch.object(tools, 'node_path', return_value='/usr/local/bin/node'):
            plan = tools.tool_plan(self.accounts)
            self.assertEqual(plan[0]['argv'][-1], 'rea-agents@6.5.0')
            self.assertEqual(sum(p['account'] is None for p in plan), 1)
            config = tools.rea_config()['mcp_servers']['rea']
            self.assertEqual(config['args'][0], '${HOME}/.local/share/rea/node_modules/rea-agents/scripts/rea.mjs')
        directory = self.home/'.local/share/rea/node_modules/rea-agents'
        (directory/'scripts').mkdir(parents=True)
        (directory/'scripts/rea.mjs').write_text('// installed')
        (directory/'package.json').write_text(json.dumps({'version':'6.5.0'}))
        self.assertTrue(tools._rea_ready(self.sources[-1]))


if __name__ == '__main__':
    unittest.main()
