"""Exercise actual Windows file/RPC boundaries in disposable files, without accounts."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def check(manager):
    sys.path.insert(0, str(manager))
    from codex_env.transaction import Change, Transaction, fingerprint
    from codex_env.usage import receive, QueryRun, PipeReader
    checks = {}
    with tempfile.TemporaryDirectory(prefix='cboard-install-check-') as temporary:
        root = Path(temporary)
        target = root/'new directory'/'한글.txt'
        tx = Transaction(root/'state')
        identifier = tx.apply([Change(target, content='확인'.encode(), expected=fingerprint(target))])
        assert target.read_text(encoding='utf-8') == '확인'
        tx.rollback(identifier)
        assert not target.exists()
        checks['file_apply_and_rollback'] = True
        code = ('import sys;sys.path.insert(0,sys.argv[1]);'
                'from codex_env.transaction import Transaction;from pathlib import Path;'
                't=Transaction(Path(sys.argv[2]));\n'
                'try:\n with t._lock(): pass\n'
                'except RuntimeError: sys.exit(23)\n')
        with tx._lock():
            result = subprocess.run([sys.executable, '-I', '-c', code, str(manager), str(root/'state')],
                                    capture_output=True, timeout=10)
            assert result.returncode == 23, result.stderr.decode(errors='replace')
        checks['file_lock_contention'] = True
        child = subprocess.Popen([sys.executable, '-I', '-u', '-c',
            'import sys;sys.stdout.buffer.write(b\'{"id":1,"result":{"ok":true}}\\n\');sys.stdout.flush()'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        child._usage_reader = PipeReader(child.stdout)
        try:
            assert receive(child, 1, time.monotonic()+5, QueryRun()) == {'ok': True}
            checks['windows_pipe_rpc'] = True
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            reader = getattr(child, '_usage_reader', None)
            if reader:
                reader.stopped.set()
                reader.thread.join(timeout=2)
                assert not reader.thread.is_alive()
            child.stdout.close()
            child.stdin.close()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    checks = check(args.manager.resolve())
    args.output.write_text(json.dumps({'checks': checks, 'accounts_added': 0}, indent=2)+'\n', encoding='utf-8')
    print('Windows file, lock and pipe checks passed; no accounts added.')


if __name__ == '__main__':
    main()
