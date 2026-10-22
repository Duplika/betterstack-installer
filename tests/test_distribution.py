import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch
import installer
import identity
import capabilities

TARGET={'host':'cloud.example.net','server_ip':'203.0.113.20','source_id':1234568,
        'ingesting_host':'s1234568.us-west-2a.betterstackdata.com'}

class ConfigurationTests(unittest.TestCase):
    def args(self,path):
        return types.SimpleNamespace(target=path,source_id=TARGET['source_id'],
            ingesting_host=TARGET['ingesting_host'],server_ip=TARGET['server_ip'],host=TARGET['host'])
    def test_configuration_has_no_token_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'profile.json'
            with patch.object(installer,'secure_target',return_value={}):
                installer.create_configuration(self.args(p));before=p.stat().st_mtime_ns
                installer.create_configuration(self.args(p))
            self.assertEqual(json.loads(p.read_text()),TARGET)
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertEqual(p.stat().st_mtime_ns,before)
    def test_configuration_never_overwrites_another_target(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'profile.json';p.write_text(json.dumps(dict(TARGET,source_id=1)))
            with patch.object(installer,'secure_target',return_value={}):
                with self.assertRaisesRegex(RuntimeError,'existing_profile_differs'):installer.create_configuration(self.args(p))
            self.assertEqual(json.loads(p.read_text())['source_id'],1)
    def test_credentials_require_tty(self):
        with patch.object(installer.sys.stdin,'isatty',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'interactive_terminal'):installer.create_credential(TARGET,Path('/root/nonexistent-test-source.json'))
    def test_secret_is_not_printed_or_in_arguments(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'source.json'
            with patch.object(installer.sys.stdin,'isatty',return_value=True),patch('getpass.getpass',return_value='TEST_SECRET'),patch('builtins.print') as output:
                installer.create_credential(TARGET,p)
            self.assertEqual(json.loads(p.read_text())['token'],'TEST_SECRET')
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertNotIn('TEST_SECRET',str(output.call_args_list))

class PackagingTests(unittest.TestCase):
    def test_bootstrap_skips_old_system_python_without_changing_it(self):
        import importlib.util
        import subprocess
        import sys
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('public_builder',root/'scripts/build.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            folder=Path(directory)
            old=folder/'python3';old.write_text('#!/bin/sh\nexit 1\n');old.chmod(0o700)
            selected=folder/'python3.12'
            selected.write_text('#!/bin/sh\nexec '+sys.executable+' "$@"\n');selected.chmod(0o700)
            builder.build(folder/'output')
            script=folder/'output/installer.sh'
            script.write_text(script.read_text().replace('/usr/bin/python3.12',str(selected))
                              .replace('/usr/bin/python3.11',str(folder/'missing11'))
                              .replace('/usr/bin/python3.9',str(folder/'missing9'))
                              .replace('/usr/bin/python3 ',str(old)+' '))
            before=old.read_bytes()
            result=subprocess.run(['bash',str(script),'--help'],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--simplify-context',result.stdout)
            self.assertEqual(old.read_bytes(),before)

    def test_gzip_header_is_normalized_across_python_variants(self):
        import base64
        import importlib.util
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('public_builder',root/'scripts/build.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        original=builder.gzip.compress
        with tempfile.TemporaryDirectory() as directory:
            variants=[]
            for os_byte in (3,255):
                def compress(data,mtime=0):
                    value=original(data,mtime=mtime)
                    return value[:9]+bytes([os_byte])+value[10:]
                output=Path(directory)/str(os_byte)
                with patch.object(builder.gzip,'compress',side_effect=compress):
                    variants.append(builder.build(output))
                body=(output/'installer.sh').read_text()
                raw=base64.b64decode(body.split("<<'DUPLIKA_4_PAYLOAD'\n",1)[1].split('DUPLIKA_4_PAYLOAD',1)[0])
                self.assertEqual(raw[9],255)
            self.assertEqual(variants[0],variants[1])

class ExecutableTests(unittest.TestCase):
    def test_multicall_symlink_preserves_invocation_name(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root=Path(directory)
            executable=root/'dispatcher'
            executable.write_text('#!/usr/bin/python3\nimport os, sys\nprint(os.path.basename(sys.argv[0]))\n')
            executable.chmod(0o700)
            link=root/'needs-restarting';link.symlink_to(executable)
            with patch.object(capabilities.shutil,'which',return_value=str(link)):
                found=capabilities.trusted_executable('needs-restarting')
            self.assertEqual(found,str(link))
            self.assertEqual(capabilities.run([found,'-r']).stdout.strip(),'needs-restarting')

    def test_writable_target_and_writable_alias_parent_are_rejected(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root=Path(directory);executable=root/'dispatcher';executable.write_text('test');executable.chmod(0o777)
            alias=root/'alias';alias.mkdir();link=alias/'needs-restarting';link.symlink_to(executable)
            with patch.object(capabilities.shutil,'which',return_value=str(link)):
                with self.assertRaisesRegex(capabilities.ProbeError,'unsafe_executable'):
                    capabilities.trusted_executable('needs-restarting')
                executable.chmod(0o700);alias.chmod(0o777)
                with self.assertRaisesRegex(capabilities.ProbeError,'unsafe_executable'):
                    capabilities.trusted_executable('needs-restarting')

class CandidateUpgradeTests(unittest.TestCase):
    previous_version='4.0.0-rc3'
    def setUp(self):
        import ast, base64, contextlib, io, tarfile
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        directory=self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root'))
        self.root=Path(directory)
        stage=self.root/'payload';stage.mkdir()
        for source in installer.HERE.iterdir():
            if source.is_file():(stage/source.name).write_bytes(source.read_bytes())
        self.stack.enter_context(patch.object(installer,'HERE',stage))
        for name in ('BASE','STATE','CLOUD','UNITS'):
            folder=self.root/name;folder.mkdir()
            self.stack.enter_context(patch.object(installer,name,folder))
        self.stack.enter_context(patch.object(identity,'BASE',installer.BASE))
        self.stack.enter_context(patch.object(identity,'STATE',installer.STATE))
        script=(Path(__file__).parents[1]/('releases/'+self.previous_version+'/installer.sh')).read_text()
        payload=base64.b64decode(script.split("<<'DUPLIKA_4_PAYLOAD'\n",1)[1].split('DUPLIKA_4_PAYLOAD',1)[0])
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
            files={member.name:archive.extractfile(member).read() for member in archive}
        for path in installer.runtime_files()+[installer.CLOUD/'collector.py']:
            path.write_bytes(files[path.name])
        tree=ast.parse(files['installer.py'])
        activate=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='activate')
        for node in activate.body:
            if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('service','timer'):
                value = node.value
                template = ast.literal_eval(value.func.value if isinstance(value, ast.Call) else value)
                (installer.UNITS/(installer.NAME+'.'+node.targets[0].id)).write_text(
                    template.replace('__PYTHON__', sys.executable))
        for path in installer.observer_base_paths():
            if not path.exists():path.write_text('{}')
        (installer.CLOUD/'vector.json').write_text(json.dumps(__import__('config').make_config(TARGET,'TEST_SECRET',TARGET['ingesting_host'])))
        (installer.BASE/'complete').write_text(self.previous_version+'\n')
        self.caps={'os_id':'cloudlinux','edition':'CloudLinux OS Solo'}
        paths=installer.observer_base_paths()+installer.runtime_files()+[installer.UNITS/(installer.NAME+ext) for ext in ('.service','.timer')]
        self.meta={'agent_version':self.previous_version,'installer_version':self.previous_version,'target':TARGET,'capabilities':self.caps,
            'configuration_schema':1,'pipeline_variant':'observer-v4','config_path':str(installer.CLOUD/'vector.json'),
            'profile_path':str(installer.CLOUD/'profile.json'),'critical_paths':sorted(map(str,paths)),
            'services':['duplika-betterstack-cloudlinux.service','duplika-cloudlinux-observer.timer',installer.NAME+'.timer'],
            'oneshots':['duplika-cloudlinux-observer.service'],'vector_binary':'/usr/bin/vector'}
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],self.meta['config_path'])
        self.save_meta()
        self.status=self.stack.enter_context(patch.object(identity,'status',return_value={}))
        self.execute=self.stack.enter_context(patch.object(installer,'execute',return_value=types.SimpleNamespace(returncode=0)))
        self.stack.enter_context(patch.object(installer,'run',return_value=types.SimpleNamespace(returncode=0)))
        self.stack.enter_context(patch.object(installer,'trusted_executable',return_value='/usr/bin/vector'))

    def save_meta(self):
        (installer.BASE/'manifest.json').write_bytes(identity.canonical(self.meta))

    def test_reviewed_rc3_upgrade_preserves_transport_and_state(self):
        checkpoint=self.root/'checkpoint';checkpoint.write_text('keep offsets')
        spool=self.root/'spool';spool.write_text('pending events')
        preserved={p:p.read_bytes() for p in installer.observer_base_paths()+[checkpoint,spool]}
        installer.upgrade(TARGET,self.caps)
        self.assertEqual(installer.load(installer.BASE/'manifest.json')['agent_version'],capabilities.VERSION)
        self.assertEqual((installer.BASE/'complete').read_text(),capabilities.VERSION+'\n')
        for path,data in preserved.items():self.assertEqual(path.read_bytes(),data)
        commands=[call.args[0] for call in self.execute.call_args_list]
        self.assertFalse(any('duplika-betterstack-cloudlinux.service' in command for command in commands))
        self.assertTrue((installer.STATE/'last-transaction.json').exists())
        before=(installer.BASE/'manifest.json').read_bytes()
        installer.upgrade(TARGET,self.caps)
        self.assertEqual(before,(installer.BASE/'manifest.json').read_bytes())

    def test_failed_activation_restores_old_runtime(self):
        paths=installer.runtime_files()+[installer.BASE/'complete',installer.BASE/'manifest.json']
        before={p:p.read_bytes() for p in paths}
        self.status.side_effect=[{},RuntimeError('failed verification')]
        with self.assertRaisesRegex(RuntimeError,'failed verification'):
            installer.upgrade(TARGET,self.caps)
        for path,data in before.items():self.assertEqual(path.read_bytes(),data)

    def test_rebaselined_unknown_code_is_rejected_before_backup(self):
        (installer.BASE/'health.py').write_text('changed helper')
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],self.meta['config_path']);self.save_meta()
        with self.assertRaisesRegex(RuntimeError,'unreviewed_v4_runtime'):
            installer.upgrade(TARGET,self.caps)
        self.assertFalse((installer.STATE/'last-transaction.json').exists())

    def test_configuration_drift_is_rejected_before_backup(self):
        config=installer.load(installer.CLOUD/'vector.json');config['sources']['host']['scrape_interval_secs']=10
        (installer.CLOUD/'vector.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(RuntimeError,'v4_drift'):installer.upgrade(TARGET,self.caps)
        self.assertFalse((installer.STATE/'last-transaction.json').exists())

    def test_unknown_version_or_changed_capabilities_require_review(self):
        with self.assertRaisesRegex(RuntimeError,'capabilities_changed'):
            installer.upgrade(TARGET,dict(self.caps,edition='other'))
        self.meta['agent_version']='4.0.0-rc1';self.save_meta()
        with self.assertRaisesRegex(RuntimeError,'unreviewed_v4_version'):
            installer.upgrade(TARGET,self.caps)

class Rc4UpgradeTests(CandidateUpgradeTests):
    previous_version='4.0.0-rc4'

class Rc5UpgradeTests(CandidateUpgradeTests):
    previous_version='4.0.0-rc5'

class Rc6UpgradeTests(CandidateUpgradeTests):
    previous_version='4.0.0-rc6'

class Rc7UpgradeTests(CandidateUpgradeTests):
    previous_version='4.0.0-rc7'

class Rc12UpgradeTests(CandidateUpgradeTests):
    previous_version = '4.0.0-rc12'

    def setUp(self):
        super().setUp()
        data = self.root / 'observer-data'; data.mkdir()
        private = data / 'private'; private.mkdir()
        (private / 'collector-health.json').write_text(json.dumps({'at': __import__('time').time(), 'components': {'lve': True}}))
        (private / 'collector.lock').write_bytes(b'')
        (private / 'state.json').write_bytes(b'{"offset":987654}')
        self.stack.enter_context(patch.object(installer, 'CLOUD_DATA', data))
        (installer.CLOUD / 'profile.json').write_text(json.dumps({'agent_version': self.previous_version}))
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        self.save_meta()

    def test_reviewed_rc3_upgrade_preserves_transport_and_state(self):
        paths = [p for p in installer.observer_base_paths() if p.name not in ('collector.py', 'profile.json')]
        paths += [installer.CLOUD_DATA / 'private/state.json']
        before = {p:p.read_bytes() for p in paths}
        old_collector = (installer.CLOUD / 'collector.py').read_bytes()
        installer.upgrade(TARGET, self.caps)
        self.assertEqual(before, {p:p.read_bytes() for p in paths})
        self.assertNotEqual(old_collector, (installer.CLOUD / 'collector.py').read_bytes())
        self.assertEqual((installer.CLOUD / 'collector.py').read_bytes(), (installer.HERE / 'collector.py').read_bytes())
        self.assertEqual(installer.load(installer.CLOUD / 'profile.json')['agent_version'], capabilities.VERSION)
        commands = [call.args[0] for call in self.execute.call_args_list]
        self.assertIn(['systemctl','start','duplika-cloudlinux-observer.timer'], commands)
        self.assertFalse(any('duplika-betterstack-cloudlinux.service' in command for command in commands))
        self.assertIn(['systemctl', 'start', 'duplika-cloudlinux-observer.service'], commands)

    def test_failed_activation_restores_old_runtime(self):
        before = {p:p.read_bytes() for p in installer.observer_base_paths()+installer.runtime_files()+[
            installer.BASE/'manifest.json', installer.BASE/'complete', installer.CLOUD_DATA/'private/state.json']}
        self.status.side_effect = [{}, RuntimeError('failed verification')]
        with self.assertRaisesRegex(RuntimeError, 'failed verification'):
            installer.upgrade(TARGET,self.caps)
        self.assertEqual(before, {p:p.read_bytes() for p in before})

    def test_busy_observer_does_not_overwrite_collector(self):
        before = (installer.CLOUD / 'collector.py').read_bytes()
        import contextlib
        @contextlib.contextmanager
        def busy():
            raise RuntimeError('observer_busy')
            yield
        with patch.object(installer,'observer_quiet_point',busy):
            with self.assertRaisesRegex(RuntimeError,'observer_busy'):
                installer.upgrade(TARGET,self.caps)
        self.assertEqual(before,(installer.CLOUD/'collector.py').read_bytes())

    def test_quiet_point_times_out_without_writes(self):
        before = (installer.CLOUD_DATA/'private/state.json').read_bytes()
        with patch.object(installer.fcntl,'flock',side_effect=BlockingIOError):
            with self.assertRaisesRegex(RuntimeError,'observer_busy_retry_later'):
                with installer.observer_quiet_point(timeout=0):
                    self.fail('must not enter while busy')
        self.assertEqual(before,(installer.CLOUD_DATA/'private/state.json').read_bytes())

class IdentityDeliveryTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        import config
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        self.stack.enter_context(patch.object(identity,'BASE',root))
        self.stack.enter_context(patch.object(identity,'STATE',root))
        config_path=root/'vector.json'
        config_path.write_text(json.dumps(config.make_config(TARGET,'TEST_SECRET',TARGET['ingesting_host'])))
        meta={'target':TARGET,'critical_paths':[],'baseline':{},'config_path':str(config_path),
            'services':['fixture.timer'],'vector_binary':'/usr/bin/vector','capabilities':{'os_id':'cloudlinux'},
            'installer_version':capabilities.VERSION,'agent_version':capabilities.VERSION,'pipeline_variant':'observer-v4'}
        (root/'manifest.json').write_text(json.dumps(meta))
        self.last=root/'last-send.json'
        self.clock=self.stack.enter_context(patch.object(identity.time,'time',return_value=1000000))
        self.stack.enter_context(patch.object(identity.health,'run',return_value=types.SimpleNamespace(returncode=0,stdout='vector fixture')))
        self.probe=self.stack.enter_context(patch.object(identity.health,'observe',return_value={
            'reboot_recommended':False,'reboot_status':'not_indicated','reason':'needs_restarting_no_hint','reboot_probe_exit_code':0}))
        self.send=self.stack.enter_context(patch.object(identity,'send'))
        self.output=self.stack.enter_context(patch('builtins.print'))

    def test_initial_change_recovery_and_daily_delivery_without_hourly_writes(self):
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,1)
        before=self.last.read_bytes();stamp=self.last.stat().st_mtime_ns
        self.clock.return_value+=3600
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,1)
        self.assertEqual(self.last.read_bytes(),before)
        self.assertEqual(self.last.stat().st_mtime_ns,stamp)
        self.probe.return_value={'reboot_recommended':True,'reboot_status':'indicated',
            'reason':'updates_since_boot','reboot_probe_exit_code':1}
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,2)
        self.probe.return_value={'reboot_recommended':None,'reboot_status':'unknown','reason':'reboot_probe_failed'}
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,3)
        self.probe.return_value={'reboot_recommended':False,'reboot_status':'not_indicated',
            'reason':'needs_restarting_no_hint','reboot_probe_exit_code':0}
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,4)
        self.clock.return_value=1000000+86400
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,5)
        self.assertTrue(self.send.call_args.args[2]['inventory_daily'])

    def test_failed_delivery_preserves_state_and_is_retried(self):
        identity.status(publish=True);before=self.last.read_bytes()
        self.probe.return_value={'reboot_recommended':None,'reboot_status':'unknown','reason':'reboot_probe_failed'}
        self.send.side_effect=RuntimeError('identity_delivery_unconfirmed')
        with self.assertRaisesRegex(RuntimeError,'delivery_unconfirmed'):identity.status(publish=True)
        self.assertEqual(self.last.read_bytes(),before)
        self.send.side_effect=None
        identity.status(publish=True)
        self.assertEqual(self.send.call_count,3)

    def test_manual_verification_never_sends_or_creates_delivery_state(self):
        identity.status()
        self.send.assert_not_called()
        self.assertFalse(self.last.exists())

    def test_local_health_marker_replaces_daily_log_as_liveness_evidence(self):
        manifest = identity.BASE / 'manifest.json'
        meta = json.loads(manifest.read_text())
        meta.update(config_path='/etc/duplika-betterstack-cloudlinux/vector.json', resource_sampling={'lve_seconds': 300})
        original_read = identity.read
        marker = {'at': 999999, 'components': {'lve': True, 'processes': 200, 'access': {}}}
        def read(path):
            if str(path).endswith('/collector-health.json'): return identity.canonical(marker)
            if str(path) == '/etc/duplika-betterstack-cloudlinux/vector.json': return original_read(identity.BASE / 'vector.json')
            return original_read(path)
        manifest.write_text(json.dumps(meta))
        with patch.object(identity, 'read', side_effect=read):
            self.assertTrue(identity.status()['services_healthy'])
            marker['at'] = 999000
            with self.assertRaisesRegex(RuntimeError, 'integrity_or_service_failure'): identity.status()
            marker['at'] = 999999; marker['components']['lve'] = 'failed'
            with self.assertRaisesRegex(RuntimeError, 'integrity_or_service_failure'): identity.status()

if __name__=='__main__':unittest.main()


class Rc13StorageUpgradeTests(Rc12UpgradeTests):
    previous_version = '4.0.0-rc13'

    def setUp(self):
        super().setUp()
        # The mocked native unit completes a real first storage cycle before activation.
        (installer.CLOUD_DATA / 'private/storage-health.json').write_text(
            json.dumps({'at': __import__('time').time(), 'probe_failures': 0}))
        self.stack.enter_context(patch.object(installer, 'run', side_effect=self.unit_state))

    def unit_state(self, args, **kwargs):
        code = 3 if len(args) > 1 and args[1] in ('is-active', 'is-enabled') and any('duplika-storage-observer' in a for a in args) else 0
        return types.SimpleNamespace(returncode=code)

    def test_reviewed_rc3_upgrade_preserves_transport_and_state(self):
        before = {p:p.read_bytes() for p in installer.observer_base_paths() if p.name not in ('collector.py','profile.json')}
        cursor = installer.CLOUD_DATA / 'private/state.json'; cursor_before = cursor.read_bytes()
        installer.upgrade(TARGET, self.caps)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(cursor.read_bytes(),cursor_before)
        self.assertTrue(all(p.exists() for p in installer.storage_paths()))
        meta=installer.load(installer.BASE/'manifest.json')
        self.assertTrue(meta['storage_observer'])
        self.assertIn('duplika-storage-observer.timer',meta['services'])
        self.assertIn('duplika-storage-observer.service',meta['oneshots'])
        transaction=installer.load(installer.load(installer.STATE/'last-transaction.json')['path'])
        self.assertIn(str(installer.BASE/'manifest.json'),transaction['files'])
        self.assertIn(str(installer.CLOUD/'storage.py'),transaction['files'])
        commands=[call.args[0] for call in self.execute.call_args_list]
        self.assertFalse(any('duplika-betterstack-cloudlinux.service' in cmd for cmd in commands))

    def test_storage_failure_rolls_back_runtime_without_touching_cursors(self):
        before={p:p.read_bytes() for p in installer.observer_base_paths()+installer.runtime_files()+[
            installer.BASE/'manifest.json',installer.BASE/'complete',installer.CLOUD_DATA/'private/state.json']}
        def execute(args, **kwargs):
            if args == ['systemctl','start','duplika-storage-observer.service']:
                raise RuntimeError('storage native failure')
            return types.SimpleNamespace(returncode=0)
        self.execute.side_effect=execute
        with self.assertRaisesRegex(RuntimeError,'storage native failure'):
            installer.upgrade(TARGET,self.caps)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertFalse(any(p.exists() for p in installer.storage_paths()))
