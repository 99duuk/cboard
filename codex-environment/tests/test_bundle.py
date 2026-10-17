"""Export scope, private-data exclusion, validation and migration contracts."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from codex_env import bundle, core


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base/'source'
        self.home = self.base/'owner'
        self.home.mkdir()
        for directory in ['bin', 'codex_env', 'vendor', 'tests', 'config/accounts', 'assets/rules', 'assets/skills']:
            (self.root/directory).mkdir(parents=True)
        (self.root/'bin/cenv').write_text('tool')
        (self.root/'config/common.toml').write_text('[features]\ngoals = true\n')
        (self.root/'config/defaults.toml').write_text('model = "default-model"\n')
        (self.root/'config/accounts/owner.toml').write_text('model = "private-choice"\n')
        (self.root/'assets/AGENTS.md').write_text('personal instructions SENTINEL')
        (self.root/'assets/rules/default.rules').write_text('prefix_rule(pattern=["'+str(self.home)+'/tool"], decision="allow")\n')
        (self.root/'accounts.toml').write_text('version = 1\n[[accounts]]\nid = "owner"\nhome = "~/.codex-owner"\naliases = ["cowner"]\n')
        (self.root/'sources.toml').write_text('version = 1\n[[sources]]\nname = "shared-dependency"\nstatus = "manual"\n')
        (self.root/'assets-manifest.json').write_text('{"source_owner":"PRIVATE"}')
        (self.root/'README.md').write_text('private docs SENTINEL')
        self.patches = [mock.patch.object(core, 'ROOT', self.root), mock.patch.object(Path, 'home', return_value=self.home), contextlib.redirect_stdout(io.StringIO())]
        for patch in self.patches:
            patch.__enter__()
            self.addCleanup(patch.__exit__, None, None, None)

    def unpack(self, name='bundle.tar.gz', **options):
        archive = self.base/name
        self.assertEqual(bundle.export_bundle(archive, **options), 0)
        destination = self.base/name.replace('.tar.gz', '')
        with tarfile.open(archive) as stream:
            self.assertTrue(all(not member.issym() and not member.islnk() for member in stream.getmembers()))
            stream.extractall(destination, filter='data')
        return destination/'codex-environment'

    def test_default_empty_registry_no_personal_assets_or_docs(self):
        package = self.unpack()
        self.assertEqual(core.read_toml(package/'accounts.toml')['accounts'], [])
        self.assertEqual(core.read_toml(package/'config/common.toml'), {})
        self.assertEqual(core.read_toml(package/'config/defaults.toml'), {'approval_policy': 'on-request', 'sandbox_mode': 'workspace-write', 'approvals_reviewer': 'user'})
        self.assertEqual(core.read_toml(package/'sources.toml')['sources'], [])
        self.assertEqual((package/'assets/AGENTS.md').read_text(), '')
        self.assertEqual(list((package/'config/accounts').iterdir()), [])
        self.assertFalse((package/'config/default.json').exists())
        self.assertNotIn(b'SENTINEL', b''.join(path.read_bytes() for path in package.rglob('*') if path.is_file()))
        self.assertEqual(json.loads((package/'assets-manifest.json').read_text()), {'assets': []})
        manifest = json.loads((package/'MANIFEST.json').read_text())['files']
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((package/name).read_bytes()).hexdigest(), digest)

    def test_shared_has_assets_without_accounts_and_requires_policy_review(self):
        with mock.patch('sys.stdin.isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, '--yes'):
                bundle.export_bundle(self.base/'unapproved.tar.gz', include_shared=True)
        package = self.unpack(include_shared=True, yes=True)
        self.assertIn('SENTINEL', (package/'assets/AGENTS.md').read_text())
        self.assertEqual(core.read_toml(package/'accounts.toml')['accounts'], [])
        self.assertEqual(list((package/'config/accounts').iterdir()), [])
        self.assertIn('${HOME}', (package/'assets/rules/default.rules').read_text())
        info = json.loads((package/'BUNDLE-INFO.json').read_text())
        self.assertEqual(info['dependencies'][0]['name'], 'shared-dependency')
        self.assertTrue(info['permission_review'])

    def test_migration_has_portable_accounts_and_default_without_auth(self):
        core.local_dir().mkdir(parents=True)
        (core.local_dir()/'default.json').write_text('{"account":"owner"}')
        (self.home/'.codex-owner').mkdir()
        (self.home/'.codex-owner/auth.json').write_text('private authentication')
        package = self.unpack(include_accounts=True, yes=True)
        self.assertEqual(core.read_toml(package/'accounts.toml')['accounts'][0]['home'], '~/.codex-owner')
        self.assertEqual(core.read_toml(package/'config/accounts/owner.toml')['model'], 'private-choice')
        self.assertEqual(json.loads((package/'config/default.json').read_text()), {'account': 'owner'})
        self.assertFalse(any(path.name == 'auth.json' for path in package.rglob('*')))

    def test_dryrun_no_output_and_no_clobber(self):
        output = self.base/'missing'/'archive.tar.gz'
        self.assertEqual(bundle.export_bundle(output, include_shared=True, dry_run=True), 0)
        self.assertFalse(output.parent.exists())
        output = self.base/'existing.tar.gz'
        output.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            bundle.export_bundle(output)
        self.assertEqual(output.read_bytes(), b'existing')

    def test_secrets_home_paths_and_external_links_fail(self):
        for content, expected in [('personal /Users/someone/repo', 'personal home'), ('sk-'+('a'*40), 'credential pattern')]:
            (self.root/'assets/AGENTS.md').write_text(content)
            with self.assertRaisesRegex(ValueError, expected):
                bundle.export_bundle(self.base/'blocked.tar.gz', include_shared=True, yes=True)
        (self.root/'assets/AGENTS.md').write_text('safe')
        (self.root/'assets/skills/linked').symlink_to(self.home)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            bundle.export_bundle(self.base/'blocked.tar.gz', include_shared=True, yes=True)

    def test_secret_setting_and_dependency_url_fail(self):
        (self.root/'config/common.toml').write_text('[mcp_servers.example]\nhttp_headers = { Authorization = "Bearer secret" }\n')
        with self.assertRaisesRegex(ValueError, 'authentication header'):
            bundle.export_bundle(self.base/'blocked.tar.gz', include_shared=True, yes=True)
        (self.root/'config/common.toml').write_text('')
        (self.root/'sources.toml').write_text('[[sources]]\nname="example"\nsource="https://example.test/?token=private"\n')
        with self.assertRaisesRegex(ValueError, 'credential-bearing URL'):
            bundle.export_bundle(self.base/'blocked.tar.gz', include_shared=True, yes=True)


if __name__ == '__main__':
    unittest.main()
