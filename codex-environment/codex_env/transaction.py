"""Private, journaled replacements with drift-aware recovery."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import ctypes
import csv
import base64
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

WINDOWS = sys.platform == 'win32'
if WINDOWS:
    import msvcrt
else:
    import fcntl


@lru_cache(maxsize=1)
def _windows_sid():
    result = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/whoami.exe'),
                             '/user', '/fo', 'csv', '/nh'],
                            capture_output=True, text=True, errors='replace', check=True)
    sid = next(csv.reader(result.stdout.splitlines()))[-1]
    if not sid.startswith('S-1-') or any(c not in 'S-0123456789' for c in sid):
        raise RuntimeError('Unable to identify Windows user for private ACL')
    return sid


def _private(path):
    if not WINDOWS:
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    api.SetFileSecurityW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    inheritance = 'OICI' if path.is_dir() else ''
    sddl = f'D:P(A;{inheritance};FA;;;{_windows_sid()})'
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if not api.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


def _file_mode(mode):
    return (0o666 if mode & 0o200 else 0o444) if WINDOWS else mode


def _file_acl(path, saved=None):
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    if saved is None:
        api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
        api.GetFileSecurityW.restype = wintypes.BOOL
        size = wintypes.DWORD()
        api.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = ctypes.create_string_buffer(size.value)
        if not api.GetFileSecurityW(str(path), 4, descriptor, size.value, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return base64.b64encode(descriptor.raw).decode('ascii')
    descriptor = ctypes.create_string_buffer(base64.b64decode(saved, validate=True))
    protected = int.from_bytes(descriptor.raw[2:4], 'little') & 0x1000
    api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    api.SetFileSecurityW.restype = wintypes.BOOL
    if not api.SetFileSecurityW(str(path), 4 | (0x80000000 if protected else 0x20000000),
                                descriptor):
        raise ctypes.WinError(ctypes.get_last_error())


@dataclass
class Change:
    path: Path
    content: bytes | None = None
    link: Path | None = None
    mode: int = 0o600
    expected: dict | None = None
    remove: bool = False


def fingerprint(path: Path) -> dict:
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "missing"}
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "link", "target": os.readlink(path)}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISREG(info.st_mode):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"kind": "file", "sha256": digest.hexdigest(), "mode": mode}
    if stat.S_ISDIR(info.st_mode):
        return {"kind": "directory", "mode": mode,
                "entries": {child.name: fingerprint(child) for child in sorted(path.iterdir())}}
    raise ValueError(f"Unsupported filesystem object: {path}")


def _sync_directory(path):
    # Windows directories cannot be opened with the POSIX fsync interface.
    if WINDOWS:
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync_directory(path.parent)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def _remove(path):
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def _private_copy(source, target):
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        target.mkdir(mode=0o700)
        for child in source.iterdir():
            _private_copy(child, target / child.name)
        _sync_directory(target)
    else:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())


def _restore_modes(path, signature):
    if signature["kind"] in {"missing", "link"}:
        return
    if signature["kind"] == "directory":
        for name, child in signature["entries"].items():
            _restore_modes(path / name, child)
    path.chmod(signature["mode"])


class Transaction:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).absolute()

    @contextmanager
    def _lock(self):
        if self.state_dir.is_symlink():
            raise ValueError(f"State directory must not be a symlink: {self.state_dir}")
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private(self.state_dir)
        with (self.state_dir / "lock").open("a+b") as stream:
            _private(Path(stream.name))
            if WINDOWS:
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
            try:
                if WINDOWS:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                raise RuntimeError("Another environment transaction is running") from None
            try:
                yield
            finally:
                if WINDOWS:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def pending(self) -> list[str]:
        if not self.state_dir.exists():
            return []
        result = []
        for path in sorted(self.state_dir.glob("*/journal.json")):
            journal = json.loads(path.read_text())
            if journal["status"] not in {"applied", "rolled_back"}:
                result.append(path.parent.name)
        return result

    @staticmethod
    def _desired(change):
        if change.remove:
            if change.content is not None or change.link is not None:
                raise ValueError('Removal cannot include replacement content')
            return {'kind': 'missing'}
        if (change.content is None) == (change.link is None):
            raise ValueError("Each change needs exactly one of content or link")
        if change.link is not None:
            if WINDOWS:
                raise ValueError('Windows managed assets require materialized file content')
            return {"kind": "link", "target": str(change.link)}
        return {"kind": "file", "sha256": hashlib.sha256(change.content).hexdigest(),
                "mode": _file_mode(change.mode)}

    def apply(self, changes: list[Change], dry_run=False) -> str | None:
        if dry_run:
            return self._apply(changes, True)
        with self._lock():
            return self._apply(changes, False)

    def _apply(self, changes, dry_run):
        pending = self.pending()
        if pending:
            raise RuntimeError("Incomplete transaction; rollback required: " + ", ".join(pending))
        planned = []
        seen = []
        for change in changes:
            path = Path(os.path.abspath(change.path))
            if path != self.state_dir / "managed.json" and (
                path == self.state_dir or self.state_dir in path.parents or path in self.state_dir.parents
            ):
                raise ValueError(f"Destination overlaps transaction state: {path}")
            if any(path == other or path in other.parents or other in path.parents for other in seen):
                raise ValueError(f"Overlapping destination: {path}")
            seen.append(path)
            before = fingerprint(path)
            if WINDOWS and before['kind'] in {'directory', 'link'}:
                raise ValueError(f'Windows managed destinations must be regular files: {path}')
            if change.expected is not None and before != change.expected:
                raise RuntimeError(f"Changed since planning: {path}")
            after = self._desired(change)
            if before != after:
                destination = f" -> {change.link}" if change.link is not None else ""
                print(f"{'Would replace' if dry_run else 'Replace'} {path}{destination}")
                planned.append((change, path, before, after))
        if dry_run or not planned:
            return None
        missing_parents = set()
        for _, path, _, _ in planned:
            parent = path.parent
            while not os.path.lexists(parent):
                missing_parents.add(parent)
                parent = parent.parent
        transaction_id = uuid.uuid4().hex
        directory = self.state_dir / transaction_id
        directory.mkdir(mode=0o700)
        _sync_directory(self.state_dir)
        journal_path = directory / "journal.json"
        journal = {"id": transaction_id, "status": "preparing", "entries": [],
                   "parents": [{"path": str(path), "created": False}
                               for path in sorted(missing_parents, key=lambda p: (len(p.parts), str(p)))]}
        _write_json(journal_path, journal)
        try:
            for index, (change, path, before, after) in enumerate(planned):
                if fingerprint(path) != before:
                    raise RuntimeError(f"Changed since planning: {path}")
                backup = directory / str(index)
                if before["kind"] != "missing":
                    _private_copy(path, backup)
                if fingerprint(path) != before:
                    raise RuntimeError(f"Changed while backing up: {path}")
                entry = {"path": str(path), "before": before, "after": after,
                         "backup": str(index), "started": False, "restored": False,
                         "temp": str(path.parent / (".cenv-" + uuid.uuid4().hex)),
                         "stash": str(path.parent / (".cenv-original-" + uuid.uuid4().hex))}
                if WINDOWS and before['kind'] == 'file':
                    entry['before_acl'] = _file_acl(path)
                journal["entries"].append(entry)
                _write_json(journal_path, journal)
            journal["status"] = "applying"
            _write_json(journal_path, journal)
            for parent in journal["parents"]:
                path = Path(parent["path"])
                try:
                    path.mkdir(mode=0o700)
                    if WINDOWS:
                        _private(path)
                except FileExistsError:
                    raise RuntimeError(f"Parent appeared since planning: {path}") from None
                info = path.lstat()
                parent.update(created=True, device=info.st_dev, inode=info.st_ino)
                _write_json(journal_path, journal)
                _sync_directory(path.parent)
            for (change, path, before, after), entry in zip(planned, journal["entries"]):
                if fingerprint(path) != before:
                    raise RuntimeError(f"Changed since planning: {path}")
                temp = Path(entry["temp"])
                try:
                    if change.remove:
                        if before['kind'] == 'directory':
                            raise ValueError('Directory removal is not supported')
                    elif change.link is not None:
                        temp.symlink_to(change.link)
                    else:
                        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, change.mode)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(change.content)
                            stream.flush()
                            if not WINDOWS:
                                os.fchmod(stream.fileno(), change.mode)
                            os.fsync(stream.fileno())
                        if WINDOWS:
                            temp.chmod(change.mode)
                            _private(temp)
                    entry["started"] = True
                    _write_json(journal_path, journal)
                    if fingerprint(path) != before:
                        raise RuntimeError(f"Changed before replacement: {path}")
                    if before["kind"] == "directory":
                        os.rename(path, entry["stash"])
                    if change.remove:
                        path.unlink(missing_ok=True)
                    else:
                        os.replace(temp, path)
                    _sync_directory(path.parent)
                finally:
                    _remove(temp)
            journal["status"] = "applied"
            _write_json(journal_path, journal)
            self._cleanup(journal)
            return transaction_id
        except BaseException:
            # Journal survives abrupt process termination; normal failures recover immediately.
            self._rollback(directory, journal, False)
            raise

    @staticmethod
    def _cleanup(journal):
        for entry in journal["entries"]:
            for key, expected in (("temp", entry["after"]), ("stash", entry["before"])):
                path = Path(entry[key])
                if fingerprint(path) == expected:
                    _remove(path)

    @staticmethod
    def _cleanup_parents(journal, dry_run):
        for entry in reversed(journal.get("parents", [])):
            path = Path(entry["path"])
            if not entry["created"]:
                # shortcut: preserve uncertain mkdir ownership after a crash; stage directory identities if that window needs cleanup.
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISDIR(info.st_mode) or info.st_dev != entry["device"]
                    or info.st_ino != entry["inode"]):
                print(f"Preserved replaced parent: {path}")
                continue
            if dry_run:
                print(f"Would remove parent if empty: {path}")
                continue
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                    raise
                print(f"Preserved nonempty parent: {path}")
            else:
                _sync_directory(path.parent)

    def rollback(self, transaction_id: str, dry_run=False) -> bool:
        if not transaction_id or any(c not in "0123456789abcdef" for c in transaction_id):
            raise ValueError("Invalid transaction ID")
        directory = self.state_dir / transaction_id
        if dry_run:
            return self._rollback(directory, json.loads((directory / "journal.json").read_text()), True)
        with self._lock():
            return self._rollback(directory, json.loads((directory / "journal.json").read_text()), False)

    def _rollback(self, directory, journal, dry_run):
        if journal["status"] == "rolled_back":
            return True
        if not dry_run:
            journal.setdefault("rollback_origin", journal["status"])
            journal["status"] = "rolling_back"
            _write_json(directory / "journal.json", journal)
        clean = True
        for entry in reversed(journal["entries"]):
            path = Path(entry["path"])
            if entry["restored"] or not entry["started"]:
                continue
            current = fingerprint(path)
            if current == entry["before"]:
                if not dry_run:
                    entry["restored"] = True
                continue
            if current != entry["after"] and not (
                current["kind"] == "missing" and entry.get("restoring", False)
            ) and not (
                current["kind"] == "missing" and entry["before"]["kind"] == "directory"
                and journal.get("rollback_origin", journal["status"]) == "applying"
            ):
                print(f"Rollback blocked by external change: {path}")
                clean = False
                continue
            print(f"{'Would restore' if dry_run else 'Restore'} {path}")
            if dry_run:
                continue
            temp = Path(entry.setdefault("restore_temp", str(path.parent / (".cenv-" + uuid.uuid4().hex))))
            _write_json(directory / "journal.json", journal)
            try:
                if entry["before"]["kind"] != "missing":
                    if not os.path.lexists(temp):
                        _private_copy(directory / entry["backup"], temp)
                        _restore_modes(temp, entry["before"])
                        if WINDOWS and entry.get('before_acl'):
                            _file_acl(temp, entry['before_acl'])
                    if fingerprint(temp) != entry["before"]:
                        raise RuntimeError(f"Backup or recovery stage changed: {path}")
                if fingerprint(path) != current:
                    raise RuntimeError(f"Changed before rollback: {path}")
                entry["restoring"] = True
                _write_json(directory / "journal.json", journal)
                if entry["before"]["kind"] == "directory" or (path.is_dir() and not path.is_symlink()):
                    _remove(path)
                if entry["before"]["kind"] == "missing":
                    _remove(path)
                else:
                    os.replace(temp, path)
                _sync_directory(path.parent)
                entry["restored"] = True
                _write_json(directory / "journal.json", journal)
            finally:
                _remove(temp)
        if not dry_run:
            if clean:
                self._cleanup(journal)
            self._cleanup_parents(journal, False)
            journal["status"] = "rolled_back" if clean else "rollback_failed"
            _write_json(directory / "journal.json", journal)
        elif dry_run:
            self._cleanup_parents(journal, True)
        return clean
