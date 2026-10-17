import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_env.assets import asset_links, capture_assets, inventory_sources


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.root = Path(self.tmp.name) / 'bundle'
        (self.home / '.agents/skills').mkdir(parents=True)
        self.root.mkdir()

    def test_capture_external_working_tree_and_preserve_canonical_edits(self):
        external = Path(self.tmp.name) / 'external'
        external.mkdir()
        (external / 'SKILL.md').write_text('uncommitted content')
        for ignored in ('.git', 'node_modules', '__pycache__'):
            (external / ignored).mkdir()
            (external / ignored / 'private').write_text('excluded')
        subprocess.run(['git', 'init', '-q', str(external)], check=True)
        (external / '.gitignore').write_text('output/\n')
        (external / 'output').mkdir()
        (external / 'output/generated').write_text('excluded')
        (external / '.env').write_text('secret')
        (external / 'assets').mkdir()
        (external / 'assets/file.txt').write_text('keep')
        (self.home / '.agents/skills/demo').symlink_to(external)
        result = capture_assets(self.root, self.home)
        target = self.root / 'assets/skills/demo'
        self.assertEqual((target / 'SKILL.md').read_text(), 'uncommitted content')
        self.assertFalse(target.is_symlink())
        self.assertEqual(sorted(p.name for p in target.iterdir()), ['.gitignore', 'SKILL.md', 'assets'])
        self.assertTrue(result['assets'][0]['sha256'])
        (target / 'SKILL.md').write_text('canonical edit')
        capture_assets(self.root, self.home)
        self.assertEqual((target / 'SKILL.md').read_text(), 'canonical edit')
        self.assertEqual((external / 'SKILL.md').read_text(), 'uncommitted content')
        self.assertIn((self.home / '.agents/skills/demo', target), asset_links(self.root, self.home, [{'home': '.codex-work'}]))

    def test_nested_escape_rejected_without_partial_asset(self):
        skill = self.home / '.agents/skills/demo'
        skill.mkdir()
        private = self.home / 'auth.json'
        private.write_text('secret')
        (skill / 'innocent.txt').symlink_to(private)
        with self.assertRaises(ValueError):
            capture_assets(self.root, self.home)
        self.assertFalse((self.root / 'assets/skills/demo').exists())

    def test_inventory_allowlists_metadata_and_redacts_url_credentials(self):
        (self.home / '.codex').mkdir()
        (self.home / '.codex/config.toml').write_text('''api_key = "PRIVATE"
[marketplaces.example]
source_type = "git"
source = "https://user:SECRET@example.com/repo.git?token=SECRET"
last_revision = "abc"
[plugins."demo@example"]
enabled = false
''')
        result = inventory_sources(self.root, self.home)
        text = str(result)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('PRIVATE', text)
        self.assertEqual(result['sources'][0]['install'][-2:], ['--ref', 'abc'])
        self.assertFalse(result['sources'][1]['enabled'])


if __name__ == '__main__':
    unittest.main()
