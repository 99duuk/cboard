"""Disposable console-entry launcher for PowerShell argv validation, without production imports."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

MODULE = '''import json, subprocess, sys

def main():
    recorder = 'import json,sys;sys.stdout.buffer.write(json.dumps(sys.argv[1:],ensure_ascii=False).encode("utf-8"))'
    child = subprocess.run([sys.executable, '-I', '-c', recorder, *sys.argv[1:]],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sys.stdout.buffer.write(child.stdout)
    sys.stderr.buffer.write(child.stderr)
    return child.returncode
'''


def prepare(directory):
    directory = Path(directory).absolute()
    if directory.exists():
        raise ValueError('launcher directory already exists; choose a fresh temporary path')
    venv.EnvBuilder(with_pip=True, symlinks=False).create(directory)
    python = directory/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    code = '''import json, pathlib, sysconfig
from pip._vendor.distlib.scripts import ScriptMaker
purelib = pathlib.Path(sysconfig.get_path('purelib'))
(purelib/'cenv_g0_argv.py').write_text(__import__('sys').argv[1], encoding='utf-8')
maker = ScriptMaker(None, sysconfig.get_path('scripts'))
maker.variants = {''}
maker.clobber = False
files = maker.make('cenv-g0 = cenv_g0_argv:main', options={'interpreter_args': ['-I']})
print(json.dumps({'launcher': files[0], 'kind': 'temporary-console-entry', 'native_recorder': 'python-executable', 'production_launcher': False}))
'''
    result = subprocess.run([str(python), '-I', '-c', code, MODULE],
                            capture_output=True, text=True, timeout=60, check=True,
                            encoding='utf-8')
    return json.loads(result.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.prepare)))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('argv probe preparation failed: '+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
