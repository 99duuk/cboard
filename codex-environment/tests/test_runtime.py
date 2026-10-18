"""Isolated selection, process forwarding and shell lifecycle checks."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import select
import signal
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

from codex_env import core, runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'bundle with spaces'
        self.home = Path(temporary.name)/'home'
        self.root.mkdir()
        self.home.mkdir()
        self.accounts = [{'id':'personal','home':'~/.codex-personal','aliases':['cpersonal']},
                         {'id':'business','home':'~/.codex-business','aliases':['cbiz']}]
        (self.root/'accounts.toml').write_bytes(core.dump({'version':1,'accounts':self.accounts}))
        for patch in [mock.patch.object(core,'ROOT',self.root), mock.patch.object(Path,'home',return_value=self.home),
                      mock.patch.dict(os.environ,{},clear=True)]:
            patch.start()
            self.addCleanup(patch.stop)

    def test_priority_and_deleted_selection_never_falls_through(self):
        runtime.set_default('business')
        self.assertEqual(runtime.resolve()[0]['id'],'business')
        os.environ['CENV_ACCOUNT']='personal'
        self.assertEqual(runtime.resolve()[0]['id'],'personal')
        os.environ['CODEX_HOME']=str(self.home/'external')
        self.assertEqual(runtime.resolve()[2],'CODEX_HOME')
        self.assertEqual(runtime.resolve('business')[0]['id'],'business')
        del os.environ['CODEX_HOME']
        os.environ['CENV_ACCOUNT']='removed'
        with self.assertRaisesRegex(ValueError,'Unknown account'):
            runtime.resolve()

    def test_default_snapshot_and_external_conflict(self):
        runtime.set_default('business')
        os.environ['CENV_DEFAULT_ACCOUNT']=''
        self.assertIsNone(runtime.resolve()[1])
        os.environ['CODEX_HOME']='/external'
        with self.assertRaisesRegex(ValueError,'CODEX_HOME'):
            runtime.select('personal')
        self.assertEqual(runtime.find_account('cbiz')['id'],'business')
        runtime.set_default(None)
        self.assertEqual(json.loads((core.local_dir()/'default.json').read_text()),{'account':None})

    def test_native_arguments_environment_and_exit_preserved(self):
        executable=self.root/'native'
        executable.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps([os.environ["CODEX_HOME"],sys.argv[1:]]))\nsys.exit(37)\n')
        executable.chmod(0o755)
        output=self.root/'output'
        native_popen=subprocess.Popen
        args=['한 글','quote\"', '$(touch no)','--flag']
        with output.open('w') as stream, mock.patch.object(core,'resolve_cli',return_value=str(executable)), mock.patch.object(subprocess,'Popen',side_effect=lambda command, **kwargs: native_popen(command, stdout=stream, **kwargs)):
            self.assertEqual(runtime.run('personal',args),37)
        self.assertEqual(json.loads(output.read_text()),[str(self.home/'.codex-personal'),args])
        self.assertNotIn('CODEX_HOME',os.environ)
        child=mock.Mock()
        child.wait.return_value=-15
        with mock.patch.object(core,'resolve_cli',return_value=str(executable)), mock.patch.object(subprocess,'Popen',return_value=child):
            self.assertEqual(runtime.run('personal'),143)

    def test_windows_runtime_uses_owned_console_group_and_ctrl_break(self):
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt(), 0]
        with mock.patch.object(core, 'resolve_cli', return_value='/native/codex.exe'), \
             mock.patch.object(runtime.sys, 'platform', 'win32'), \
             mock.patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True), \
             mock.patch.object(signal, 'CTRL_BREAK_EVENT', 1, create=True), \
             mock.patch.object(subprocess, 'Popen', return_value=child) as popen:
            self.assertEqual(runtime.run('personal'), 130)
        self.assertEqual(popen.call_args.kwargs['creationflags'], 512)
        child.send_signal.assert_called_once_with(1)

    def test_manager_sigterm_forwards_to_native_and_reaps_child(self):
        native=self.root/'native-wait'
        native.write_text('#!'+sys.executable+'\nimport os,signal\nprint("READY",os.getpid(),flush=True)\nwhile True: signal.pause()\n')
        native.chmod(0o755)
        script=('import os,sys; from pathlib import Path; '
                'from codex_env import core,runtime; '
                'core.ROOT=Path(os.environ["CENV_TEST_ROOT"]); '
                'sys.exit(runtime.run("personal"))')
        environment={'HOME':str(self.home),'CENV_TEST_ROOT':str(self.root),
                     'CENV_CLI':str(native),'PYTHONPATH':str(Path(core.__file__).resolve().parents[1])}
        manager=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,start_new_session=True)
        native_pid=None
        try:
            readable,_,_=select.select([manager.stdout],[],[],5)
            self.assertTrue(readable,'native readiness timed out')
            line=manager.stdout.readline().decode().strip()
            self.assertTrue(line.startswith('READY '),repr(line))
            native_pid=int(line.split()[1])
            manager.send_signal(signal.SIGTERM)
            self.assertEqual(manager.wait(timeout=5),143)
            with self.assertRaises(ProcessLookupError):
                os.kill(native_pid,0)
        finally:
            try:
                os.killpg(manager.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            if manager.poll() is None:
                manager.kill()
                manager.wait(timeout=5)
            if native_pid is not None:
                try:
                    os.kill(native_pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
            manager.stdout.close()
            manager.stderr.close()

    def test_keyboard_interrupt_forwards_sigint_and_restores_handlers(self):
        before={number:signal.getsignal(number) for number in (signal.SIGTERM,signal.SIGHUP)}
        child=mock.Mock()
        child.poll.return_value=None
        child.wait.side_effect=[KeyboardInterrupt(),0]
        with mock.patch.object(core,'resolve_cli',return_value='/unused/native'), mock.patch.object(subprocess,'Popen',return_value=child):
            self.assertEqual(runtime.run('personal'),130)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(child.wait.call_count,2)
        self.assertEqual(before,{number:signal.getsignal(number) for number in before})

    def test_empty_bootstrap_captures_no_native_configuration(self):
        (self.root/'accounts.toml').unlink()
        native=self.home/'.codex'
        native.mkdir()
        (native/'config.toml').write_text('model="private-model"\n[features]\ngoals=true\n')
        core.bootstrap()
        self.assertEqual(core.load_accounts(),[])
        self.assertEqual(core.read_toml(self.root/'config/common.toml'),{})

    def test_managed_shell_collision_detection(self):
        with self.assertRaisesRegex(ValueError,'collision'):
            core.update_shell('cnew() { echo custom; }\n', managed_names={'cnew'})
        text=core.shell_block()+'\n'
        self.assertEqual(core.update_shell(text,managed_names={'cnew'}).count(core.BEGIN),1)

    def test_toolkit_preserves_global_assets_and_avoids_broken_links(self):
        from codex_env.assets import asset_links
        (self.root/'assets/skills').mkdir(parents=True)
        (self.root/'assets/rules').mkdir()
        (self.root/'assets/AGENTS.md').write_text('')
        (self.root/'BUNDLE-INFO.json').write_text('{"scope":"toolkit"}')
        self.assertEqual(asset_links(self.root,self.home,self.accounts),[])
        global_assets=self.home/'.agents'
        global_assets.mkdir()
        instructions=global_assets/'AGENTS.md'
        instructions.write_text('existing instructions')
        (global_assets/'skills').mkdir()
        links=asset_links(self.root,self.home,self.accounts)
        self.assertNotIn(instructions,[destination for destination,source in links])
        self.assertIn((self.home/'.codex-personal/AGENTS.md',instructions),links)
        self.assertIn((self.home/'.codex-personal/skills',global_assets/'skills'),links)
        self.assertFalse(any(destination.name == 'rules' for destination,source in links))

    def test_defaults_are_lower_priority_than_shared_and_profile_values(self):
        (self.root/'config').mkdir()
        (self.root/'config/defaults.toml').write_text('model="default"\napproval_policy="on-request"\n')
        values=core.desired_config(self.accounts[0], account_override={'model':'profile'}, shared=({('model',):'shared'},{}))
        self.assertEqual(values[('model',)],'profile')
        self.assertEqual(values[('approval_policy',)],'on-request')

    @unittest.skipUnless(shutil.which('zsh'),'zsh unavailable')
    def test_shell_remove_clears_deleted_selection_and_alias(self):
        binary=self.root/'bin/cenv'
        binary.parent.mkdir()
        initial=self.root/'initial.zsh'
        updated=self.root/'updated.zsh'
        pointer=self.home/'.config/codex-environment/shell.zsh'
        pointer.parent.mkdir(parents=True)
        initial.write_text(core.shell_text(self.accounts,'/unused'))
        updated.write_text(core.shell_text(self.accounts[1:],'/unused'))
        pointer.write_text(initial.read_text())
        import shlex
        for default, expected in [('business','unset:business'),('personal','unset:')]:
            with self.subTest(default=default):
                pointer.write_text(initial.read_text())
                binary.write_text('#!/bin/sh\ncase "$1" in\n_default) echo '+default+' ;;\n_select) echo personal ;;\n_exists) [ "$2" = business ]; exit $? ;;\naccount) cp '+shlex.quote(str(updated))+' '+shlex.quote(str(pointer))+' ;;\nesac\n')
                binary.chmod(0o755)
                result=subprocess.run(['zsh','-fc','source "$1"; cuse personal; cdel personal; print -r -- "${CENV_ACCOUNT-unset}:$CENV_DEFAULT_ACCOUNT"; (( ${+functions[cpersonal]} )) && exit 9; exit 0','zsh',str(pointer)],capture_output=True,text=True,env={'HOME':str(self.home),'PATH':'/usr/bin:/bin'})
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    @unittest.skipUnless(shutil.which('zsh'),'zsh unavailable')
    def test_shell_selection_aliases_and_no_parent_home_leak(self):
        binary=self.root/'bin/cenv'
        binary.parent.mkdir()
        binary.write_text('''#!/bin/sh
case "$1" in
_default) echo business ;;
_select) if [ "$2" = personal ]; then echo personal; else exit 2; fi ;;
account) exit 0 ;;
run) printf '%s\\n' "$@" ;;
esac
''')
        binary.chmod(0o755)
        source=self.root/'shell.zsh'
        source.write_text(core.shell_text(self.accounts,'/unused'))
        result=subprocess.run(['zsh','-fc','source "$1"; cuse personal; print -r -- "$CENV_ACCOUNT:$CENV_DEFAULT_ACCOUNT:${CODEX_HOME-unset}"; cpersonal "한 글"; cuse --clear; print -r -- "${CENV_ACCOUNT-unset}"','zsh',str(source)],capture_output=True,text=True,env={'HOME':str(self.home),'PATH':'/usr/bin:/bin'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),['personal:business:unset','run','personal','--','한 글','unset'])


if __name__ == '__main__':
    unittest.main()
