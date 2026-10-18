import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from codex_env.transaction import Change, Transaction, fingerprint
from codex_env import transaction


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.tx = Transaction(self.state)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_noop_and_dry_run_have_no_state_side_effects(self):
        path = self.root / 'config'
        path.write_bytes(b'old')
        path.chmod(0o600)
        self.assertIsNone(self.tx.apply([Change(path, b'new')], dry_run=True))
        self.assertFalse(self.state.exists())
        self.assertEqual(path.read_bytes(), b'old')
        self.assertIsNone(self.tx.apply([Change(path, b'old')]))
        self.assertEqual(list(self.state.glob('*/journal.json')), [])

    def test_file_backup_permissions_and_rollback(self):
        path = self.root / 'config'
        path.write_bytes(b'secret-original')
        path.chmod(0o644)
        txid = self.tx.apply([Change(path, b'secret-new')])
        directory = self.state / txid
        self.assertEqual(path.read_bytes(), b'secret-new')
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / '0').stat().st_mode & 0o777, 0o600)
        self.assertEqual((directory / 'journal.json').stat().st_mode & 0o777, 0o600)
        self.assertNotIn('secret-', (directory / 'journal.json').read_text())
        self.assertTrue(self.tx.rollback(txid, dry_run=True))
        self.assertEqual(path.read_bytes(), b'secret-new')
        self.assertTrue(self.tx.rollback(txid))
        self.assertEqual(path.read_bytes(), b'secret-original')
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertTrue(self.tx.rollback(txid))

    def test_directory_and_broken_symlink_restore(self):
        path = self.root / 'skills'
        path.mkdir(mode=0o755)
        (path / 'sub').mkdir(mode=0o750)
        file = path / 'sub' / 'script'
        file.write_bytes(b'#!/bin/sh')
        file.chmod(0o755)
        (path / 'broken').symlink_to('absent')
        before = fingerprint(path)
        target = self.root / 'new-skills'
        txid = self.tx.apply([Change(path, link=target)])
        self.assertTrue(path.is_symlink())
        backup = self.state / txid / '0'
        self.assertEqual((backup / 'sub').stat().st_mode & 0o777, 0o700)
        self.assertEqual((backup / 'sub' / 'script').stat().st_mode & 0o777, 0o600)
        self.assertTrue((backup / 'broken').is_symlink())
        self.assertTrue(self.tx.rollback(txid))
        self.assertEqual(fingerprint(path), before)
        self.assertEqual(list(self.root.glob('.cenv-*')), [])

    def test_external_changes_block_apply_and_rollback(self):
        path = self.root / 'config'
        path.write_bytes(b'old')
        observed = fingerprint(path)
        path.write_bytes(b'edited')
        with self.assertRaisesRegex(RuntimeError, 'Changed since planning'):
            self.tx.apply([Change(path, b'new', expected=observed)])
        txid = self.tx.apply([Change(path, b'new')])
        path.write_bytes(b'other writer')
        self.assertFalse(self.tx.rollback(txid))
        self.assertEqual(path.read_bytes(), b'other writer')
        self.assertEqual(self.tx.pending(), [txid])
        with self.assertRaisesRegex(RuntimeError, 'Incomplete transaction'):
            self.tx.apply([Change(self.root / 'another', b'x')])

    def test_apply_failure_rolls_back_previous_changes(self):
        first, second = self.root / 'first', self.root / 'second'
        first.write_bytes(b'old')
        second.write_bytes(b'old2')
        original_replace = os.replace

        def fail_second(source, target):
            if Path(target) == second:
                raise OSError('injected failure')
            return original_replace(source, target)

        with patch('codex_env.transaction.os.replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'injected failure'):
                self.tx.apply([Change(first, b'new'), Change(second, b'new2')])
        self.assertEqual(first.read_bytes(), b'old')
        self.assertEqual(second.read_bytes(), b'old2')
        self.assertEqual(self.tx.pending(), [])

    def test_crash_after_directory_move_is_recoverable(self):
        path = self.root / 'skills'
        path.mkdir()
        (path / 'note').write_bytes(b'original')
        before = fingerprint(path)
        code = '''
import os, sys
from pathlib import Path
from codex_env.transaction import Change, Transaction
path, state = map(Path, sys.argv[1:])
original = os.rename
def crash(source, target):
    original(source, target)
    os._exit(91)
os.rename = crash
Transaction(state).apply([Change(path, link=path.parent / 'shared')])
'''
        result = subprocess.run([sys.executable, '-c', code, str(path), str(self.state)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertEqual(result.returncode, 91, result.stderr)
        self.assertFalse(path.exists())
        pending = self.tx.pending()
        self.assertEqual(len(pending), 1)
        with self.assertRaisesRegex(RuntimeError, 'Incomplete transaction'):
            self.tx.apply([Change(self.root / 'other', b'value')])
        self.assertTrue(self.tx.rollback(pending[0]))
        self.assertEqual(fingerprint(path), before)
        self.assertEqual(list(self.root.glob('.cenv-*')), [])

    def test_interrupted_rollback_resumes_without_losing_directory(self):
        path = self.root / 'skills'
        path.mkdir()
        (path / 'note').write_bytes(b'original')
        before = fingerprint(path)
        txid = self.tx.apply([Change(path, link=self.root / 'shared')])
        code = '''
import os, sys
from pathlib import Path
from codex_env.transaction import Transaction
path, state, txid = sys.argv[1:]
original = os.unlink
def crash(target, *args, **kwargs):
    original(target, *args, **kwargs)
    if str(target) == path:
        os._exit(92)
os.unlink = crash
Transaction(Path(state)).rollback(txid)
'''
        result = subprocess.run([sys.executable, '-c', code, str(path), str(self.state), txid],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertEqual(result.returncode, 92, result.stderr)
        self.assertEqual(self.tx.pending(), [txid])
        self.assertTrue(self.tx.rollback(txid))
        self.assertEqual(fingerprint(path), before)
        self.assertEqual(list(self.root.glob('.cenv-*')), [])

    def test_corrupt_backup_does_not_replace_current_content(self):
        path = self.root / 'config'
        path.write_bytes(b'old')
        txid = self.tx.apply([Change(path, b'new')])
        (self.state / txid / '0').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'Backup or recovery stage changed'):
            self.tx.rollback(txid)
        self.assertEqual(path.read_bytes(), b'new')
        self.assertEqual(self.tx.pending(), [txid])

    def test_rollback_removes_only_new_empty_parents(self):
        existing = self.root / 'existing'
        existing.mkdir()
        target = existing / 'new' / 'nested' / 'config'
        txid = self.tx.apply([Change(target, b'new')])
        self.assertTrue(self.tx.rollback(txid, dry_run=True))
        self.assertTrue(target.exists())
        self.assertTrue(self.tx.rollback(txid))
        self.assertFalse((existing / 'new').exists())
        self.assertTrue(existing.is_dir())
        self.assertTrue(self.state.is_dir())

    def test_rollback_preserves_foreign_auth_file_in_new_parent(self):
        parent = self.root / 'account'
        target = parent / 'agents' / 'role.toml'
        txid = self.tx.apply([Change(target, b'model = "example"')])
        auth = parent / 'auth.json'
        auth.write_bytes(b'foreign-credentials')
        self.assertTrue(self.tx.rollback(txid))
        self.assertFalse((parent / 'agents').exists())
        self.assertEqual(auth.read_bytes(), b'foreign-credentials')

    def test_rollback_preserves_replaced_parent_directory(self):
        parent = self.root / 'account'
        target = parent / 'config'
        txid = self.tx.apply([Change(target, b'new')])
        parent.rename(self.root / 'moved-account')
        parent.mkdir()
        target.write_bytes(b'new')
        target.chmod(0o600)
        self.assertTrue(self.tx.rollback(txid))
        self.assertTrue(parent.is_dir())
        self.assertFalse(target.exists())
        self.assertEqual((self.root / 'moved-account/config').read_bytes(), b'new')

    def test_crash_after_file_replacement_cleans_recorded_parents(self):
        path = self.root / 'new' / 'nested' / 'config'
        code = '''
import os, sys
from pathlib import Path
from codex_env.transaction import Change, Transaction
path, state = map(Path, sys.argv[1:])
original = os.replace
def crash(source, target):
    original(source, target)
    if Path(target) == path:
        os._exit(93)
os.replace = crash
Transaction(state).apply([Change(path, b"new")])
'''
        result = subprocess.run([sys.executable, '-c', code, str(path), str(self.state)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertEqual(result.returncode, 93, result.stderr)
        pending = self.tx.pending()
        self.assertEqual(len(pending), 1)
        self.assertTrue(self.tx.rollback(pending[0]))
        self.assertFalse((self.root / 'new').exists())

    def test_managed_ledger_is_transactional_inside_state_directory(self):
        ledger = self.state / 'managed.json'
        txid = self.tx.apply([Change(ledger, b'{"key": "value"}')])
        self.assertEqual(ledger.read_bytes(), b'{"key": "value"}')
        self.assertTrue(self.tx.rollback(txid))
        self.assertFalse(ledger.exists())

    def test_concurrent_lock_rejected(self):
        with self.tx._lock():
            with self.assertRaisesRegex(RuntimeError, 'Another environment transaction'):
                self.tx.apply([Change(self.root / 'target', b'new')])

    def test_windows_modes_and_directory_sync_boundary(self):
        with patch.object(transaction, 'WINDOWS', True):
            self.assertEqual(Transaction._desired(Change(self.root / 'file', b'x'))['mode'], 0o666)
            self.assertEqual(Transaction._desired(Change(self.root / 'file', b'x', mode=0o400))['mode'], 0o444)
            with self.assertRaisesRegex(ValueError, 'materialized file content'):
                Transaction._desired(Change(self.root / 'file', link=self.root / 'source'))
            with patch.object(transaction.os, 'open') as opened:
                transaction._sync_directory(self.root)
            opened.assert_not_called()

    def test_windows_lock_uses_same_byte_and_unlocks_after_failure(self):
        calls = []
        def locking(fd, operation, size):
            calls.append((operation, size, os.lseek(fd, 0, os.SEEK_CUR)))
        fake = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
        with patch.object(transaction, 'WINDOWS', True), \
                patch.object(transaction, 'msvcrt', fake, create=True), \
                patch.object(transaction, '_private'):
            with self.assertRaisesRegex(RuntimeError, 'inside'):
                with self.tx._lock():
                    raise RuntimeError('inside')
        self.assertEqual(calls, [(1, 1, 0), (2, 1, 0)])
        self.assertEqual((self.state / 'lock').read_bytes(), b'\0')

    def test_windows_lock_contention_is_reported(self):
        def locking(*args):
            raise PermissionError(13, 'locked')
        fake = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
        with patch.object(transaction, 'WINDOWS', True), \
                patch.object(transaction, 'msvcrt', fake, create=True), \
                patch.object(transaction, '_private'):
            with self.assertRaisesRegex(RuntimeError, 'Another environment transaction'):
                with self.tx._lock():
                    self.fail('Contended lock acquired')

    def test_windows_private_directory_uses_protected_user_dacl(self):
        def convert(sddl, revision, descriptor, size):
            descriptor._obj.value = 123
            return True
        api = SimpleNamespace(
            ConvertStringSecurityDescriptorToSecurityDescriptorW=Mock(side_effect=convert),
            SetFileSecurityW=Mock(return_value=True))
        kernel = SimpleNamespace(LocalFree=Mock())
        with patch.object(transaction, 'WINDOWS', True), \
                patch.object(transaction, '_windows_sid', return_value='S-1-5-21-123'), \
                patch.object(transaction.ctypes, 'WinDLL', create=True,
                             side_effect=[api, kernel]):
            transaction._private(self.root)
        sddl = api.ConvertStringSecurityDescriptorToSecurityDescriptorW.call_args.args[0]
        self.assertEqual(sddl, 'D:P(A;OICI;FA;;;S-1-5-21-123)')
        self.assertEqual(api.SetFileSecurityW.call_args.args[:2], (str(self.root), 0x80000004))
        kernel.LocalFree.assert_called_once()

    def test_new_file_removed_on_rollback_and_overlap_rejected(self):
        target = self.root / 'new'
        txid = self.tx.apply([Change(target, b'new')])
        self.assertTrue(self.tx.rollback(txid))
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(ValueError, 'Overlapping destination'):
            self.tx.apply([Change(target, b'x'), Change(target / 'child', b'y')])
        with self.assertRaisesRegex(ValueError, 'overlaps transaction state'):
            self.tx.apply([Change(self.state / 'lock', b'x')])


if __name__ == '__main__':
    unittest.main()
