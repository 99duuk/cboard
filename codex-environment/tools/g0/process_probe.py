#!/usr/bin/env python3
"""Disposable Windows Job Object experiment; no production imports or credentials."""
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time


def child(role, directory):
    roles = ['manager', 'child', 'grandchild']
    index = roles.index(role)
    descendant = None
    if index < 2:
        descendant = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()),
                                       '--child', roles[index + 1], '--directory', directory],
                                      creationflags=subprocess.CREATE_NO_WINDOW)
    pending = Path(directory) / (role + '.tmp')
    pending.write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
    pending.replace(Path(directory) / (role + '.json'))
    while True:
        time.sleep(1)


def windows_probe():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    size = ctypes.c_size_t

    class Limits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', w.DWORD), ('minimum', size), ('maximum', size),
                    ('active', w.DWORD), ('affinity', size), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', Limits), ('io', Counters), ('process_memory', size), ('job_memory', size),
                    ('peak_process_memory', size), ('peak_job_memory', size)]

    class Startup(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR), ('title', w.LPWSTR),
                    ('x', w.DWORD), ('y', w.DWORD), ('width', w.DWORD), ('height', w.DWORD),
                    ('chars_x', w.DWORD), ('chars_y', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                    ('show', w.WORD), ('reserved_size', w.WORD), ('reserved_bytes', ctypes.POINTER(w.BYTE)),
                    ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]

    signatures = {
        'CreateJobObjectW': ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        'SetInformationJobObject': ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        'CreateProcessW': ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                            ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(ProcessInfo)], w.BOOL),
        'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
        'ResumeThread': ([w.HANDLE], w.DWORD),
        'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'IsProcessInJob': ([w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
        'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
        'GetExitCodeProcess': ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
        'CloseHandle': ([w.HANDLE], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result

    def check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    job = None
    handles = []
    info = ProcessInfo()
    observations = {'console_ctrl_c': {'status': 'not_run', 'reason': 'No console control event sent; isolated interactive console probe pending'},
                    'manager_abnormal_exit': {'status': 'not_run', 'reason': 'Job-close experiment only; supervising manager crash scenario pending'}}
    with tempfile.TemporaryDirectory(prefix='cenv-g0-process-') as directory:
        try:
            job = check(kernel.CreateJobObjectW(None, None))
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            check(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            startup = Startup()
            startup.cb = ctypes.sizeof(startup)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(
                [sys.executable, "-I", str(Path(__file__).resolve()), '--child', 'manager', '--directory', directory]))
            check(kernel.CreateProcessW(sys.executable, command, None, None, False,
                                        0x4 | 0x08000000, None, None, ctypes.byref(startup), ctypes.byref(info)))
            handles.append(('manager', info.pid, info.process))
            check(kernel.AssignProcessToJobObject(job, info.process))
            if kernel.ResumeThread(info.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            check(kernel.CloseHandle(info.thread))
            info.thread = None
            deadline = time.monotonic() + 10
            paths = [Path(directory) / (role + '.json') for role in ('manager', 'child', 'grandchild')]
            while not all(path.exists() for path in paths):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Disposable descendants did not report within 10 seconds')
                time.sleep(.025)
            for role, path in zip(('child', 'grandchild'), paths[1:]):
                # Membership check on a held handle prevents PID-only ownership assumptions.
                pid = json.loads(path.read_text(encoding='utf-8'))['pid']
                handle = check(kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid))
                member = w.BOOL()
                try:
                    check(kernel.IsProcessInJob(handle, job, ctypes.byref(member)))
                    if not member.value:
                        raise RuntimeError('Reported process is not in the disposable Job Object')
                except BaseException:
                    kernel.CloseHandle(handle)
                    raise
                handles.append((role, pid, handle))
            if any(kernel.WaitForSingleObject(handle, 0) != 258 for _, _, handle in handles):
                raise RuntimeError('Disposable process exited before job-close measurement')
            started = time.monotonic()
            check(kernel.CloseHandle(job))
            job = None
            results = []
            for role, pid, handle in handles:
                remaining = max(0, int((3 - (time.monotonic() - started)) * 1000))
                wait = kernel.WaitForSingleObject(handle, remaining)
                code = w.DWORD()
                check(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                results.append({'role': role, 'pid': pid, 'owned_job_member': True,
                                'terminated': wait == 0, 'exit_code_unsigned': code.value,
                                'observed_seconds': round(time.monotonic() - started, 6)})
            elapsed = time.monotonic() - started
            passed = all(item['terminated'] for item in results) and elapsed <= 3
            observations['job_close_cleanup'] = {'status': 'pass' if passed else 'fail',
                                                  'elapsed_seconds': round(elapsed, 6), 'budget_seconds': 3,
                                                  'processes': results}
        except Exception as error:
            observations['job_close_cleanup'] = {'status': 'fail', 'error_type': type(error).__name__,
                                                  'reason': 'Windows Job Object experiment failed',
                                                  'winerror': getattr(error, 'winerror', None)}
        finally:
            if job:
                kernel.CloseHandle(job)
            if info.thread:
                kernel.CloseHandle(info.thread)
            for _, _, handle in handles:
                if kernel.WaitForSingleObject(handle, 0) == 258:
                    kernel.TerminateProcess(handle, 1)
                    kernel.WaitForSingleObject(handle, 3000)
                kernel.CloseHandle(handle)
    status = 'partial' if observations['job_close_cleanup']['status'] == 'pass' else 'fail'
    return {'status': status, 'observations': observations}


def collect():
    report = {'schema_version': 1, 'environment': {'os': platform.system(), 'release': platform.release(),
                                                  'machine': platform.machine(), 'python': platform.python_version()},
              'safety': {'production_imports': False, 'credentials_accessed': False,
                         'console_events_sent': False, 'global_process_kill': False}}
    if os.name != 'nt':
        result = {'status': 'not_applicable', 'observations': {}, 'reason': 'Actual Windows execution required'}
    else:
        result = windows_probe()
    report['checks'] = {'T05': result}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--child', choices=('manager', 'child', 'grandchild'), help=argparse.SUPPRESS)
    parser.add_argument('--directory', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        if not args.directory:
            parser.error('--directory required for disposable child')
        child(args.child, args.directory)
        return 0
    if args.output is None:
        parser.error('--output required')
    # Reserve before starting processes; never overwrite existing evidence.
    with args.output.open('x', encoding='utf-8') as stream:
        report = collect()
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return 0 if report['checks']['T05']['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
