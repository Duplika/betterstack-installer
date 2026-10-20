import json
from pathlib import Path
import tempfile
import types
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
                (installer.UNITS/(installer.NAME+'.'+node.targets[0].id)).write_text(ast.literal_eval(node.value))
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

if __name__=='__main__':unittest.main()
