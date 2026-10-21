from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import installer

TARGET={'host':'cloud.example.net','server_ip':'203.0.113.20','source_id':1234568,
        'ingesting_host':'s1234568.us-west-2a.betterstackdata.com'}


class BootstrapRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        folder=self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root'));self.root=Path(folder)
        for name in ('BASE','STATE','CLOUD','CLOUD_DATA','UNITS','LEGACY'):
            self.stack.enter_context(patch.object(installer,name,self.root/name))
        installer.STATE.mkdir();installer.BASE.mkdir();installer.UNITS.mkdir()
        self.pending=installer.STATE/'install-pending.json';self.failure=installer.STATE/'last-error.json'
        self.pending.write_text(json.dumps({'phase':'base_installing','target':TARGET}))
        self.failure.write_text(json.dumps({'stage':'install','exit_code':1,'diagnostic':'Preflight correcto.\nERROR: command_failed:dnf:exit=1\n'}))
        self.stack.enter_context(patch.object(installer.pwd,'getpwnam',side_effect=KeyError))

    def test_validation_preserves_state_and_never_installs_or_starts(self):
        before={p:p.read_bytes() for p in (self.pending,self.failure)}
        with patch.object(installer,'run') as run,patch.object(installer,'execute') as execute:
            installer.validate_bootstrap_recovery(TARGET)
            run.assert_not_called();execute.assert_not_called()
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_target_phase_failure_and_existing_reader_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'target_or_phase'):
            installer.validate_bootstrap_recovery(dict(TARGET,source_id=1))
        self.failure.write_text(json.dumps({'stage':'install','exit_code':1,'diagnostic':'another failure'}))
        with self.assertRaisesRegex(RuntimeError,'unreviewed_failure'):installer.validate_bootstrap_recovery(TARGET)
        self.failure.write_text(json.dumps({'stage':'install','exit_code':1,'diagnostic':'ERROR: command_failed:dnf:exit=1'}))
        with patch.object(installer.pwd,'getpwnam',return_value=object()):
            with self.assertRaisesRegex(RuntimeError,'existing_reader'):installer.validate_bootstrap_recovery(TARGET)

    def test_existing_base_symlink_or_unit_never_adopted(self):
        installer.CLOUD.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(RuntimeError,'existing_base'):installer.validate_bootstrap_recovery(TARGET)
        installer.CLOUD.unlink()
        (installer.UNITS/'duplika-storage-observer.service.d').mkdir()
        with self.assertRaisesRegex(RuntimeError,'existing_unit'):installer.validate_bootstrap_recovery(TARGET)

    def test_fresh_without_flag_still_rejects_pending(self):
        with self.assertRaisesRegex(RuntimeError,'existing_state'):
            installer.fresh(TARGET,{'os_id':'cloudlinux'},self.root/'credential')

    def test_resume_preserves_original_credential_and_saves_failed_marker(self):
        credential=self.root/'credential.json'
        credential.write_text(json.dumps({'source_id':1234568,'host':TARGET['ingesting_host'],'token':'TEST_SECRET'}))
        before=credential.read_bytes()
        def run(args,**kw):
            if args[1].endswith('install_base.py') and '--preflight' not in args:
                raise RuntimeError('stop before native installation')
            return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch.object(installer,'run',side_effect=run):
            transaction=installer.backup([self.pending,self.failure],[])
            with self.assertRaisesRegex(RuntimeError,'stop before native'):
                installer.fresh(TARGET,{'os_id':'cloudlinux'},credential,recover_bootstrap=True)
        self.assertEqual(credential.read_bytes(),before)
        self.assertIn(str(self.failure),transaction['files'])
        self.assertFalse(installer.CLOUD.exists())


class DnfDiagnosticsTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('test_install_base',Path(__file__).parents[1]/'src/agent/install_base.py')
        module=importlib.util.module_from_spec(spec)
        with patch.object(Path,'read_text',return_value=json.dumps(TARGET)):
            spec.loader.exec_module(module)
        return module

    def test_existing_certificates_skip_dnf_and_missing_installs_only_ca(self):
        m=self.module()
        with patch.object(m,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            m.ensure_certificates();self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0],['rpm','-q','ca-certificates'])
        with patch.object(m,'run',return_value=types.SimpleNamespace(returncode=1)) as run:
            m.ensure_certificates()
            self.assertEqual(run.call_args.args[0],['dnf','-y','install','ca-certificates'])
            self.assertEqual(run.call_args.kwargs['timeout'],600)

    def test_dnf_failure_keeps_bounded_detail_without_repo_url_secret(self):
        m=self.module();p=types.SimpleNamespace(returncode=1,stdout='Could not fetch https://user:PRIVATE_SECRET@repo.example.net/file?key=PRIVATE_SECRET\n',stderr='repository unavailable')
        with patch.object(m,'probe_run',return_value=p),patch('builtins.print') as output:
            with self.assertRaisesRegex(RuntimeError,'command_failed:dnf:exit=1'):m.run(['dnf','-y','install','vector'])
        text=str(output.call_args_list)
        self.assertIn('repository unavailable',text);self.assertNotIn('PRIVATE_SECRET',text)
