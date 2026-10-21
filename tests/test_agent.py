import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import capabilities as c
import collector as col
import config
import health
import identity
import installer

TARGET = {'host':'cloud.example.net','server_ip':'203.0.113.20','source_id':1234568,
          'ingesting_host':'s1234568.us-west-2a.betterstackdata.com'}
PROFILE = {k:TARGET[k] for k in ('host','server_ip')}
SOLO = 'Command is skipped as LVE feature is not supported in current environment\n'

def result(out='', code=0, err=''):
    return subprocess.CompletedProcess([],code,out,err)

class ProbeTests(unittest.TestCase):
    def probe(self,distro='cloudlinux',edition='CloudLinux OS Solo',data=SOLO):
        calls=[]
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'os-release';path.write_text('ID='+distro+'\nVERSION_ID="9.8"\n')
            def runner(args):
                calls.append(args)
                return result(edition if '--detect-edition' in args else '--domains' if '--help' in args else data)
            answer=c.detect(path,runner,lambda name:'/usr/bin/'+name)
        return answer,calls
    def test_solo_skips_lve(self):
        p,calls=self.probe();self.assertEqual(p['lve_status'],'not_supported');self.assertEqual(len(calls),1)
    def test_alma_has_no_cloudlinux_probe(self):
        p,calls=self.probe(distro='almalinux');self.assertEqual(p['lve_status'],'not_applicable');self.assertEqual(calls,[])
    def test_shared_with_lve(self):
        p,_=self.probe(edition='CloudLinux OS Shared Pro',data='{"result":"success","users":[]}')
        self.assertEqual(p['lve_status'],'available');self.assertTrue(p['isolates_supported'])
    def test_shared_invalid_json_does_not_silently_downgrade(self):
        with self.assertRaisesRegex(c.ProbeError,'lve:invalid_json'):self.probe(edition='CloudLinux OS Shared Pro')
    def test_unknown_edition(self):
        with self.assertRaisesRegex(c.ProbeError,'edition_unrecognized'):self.probe(edition='unknown')
    def test_stderr_separate_from_json(self):self.assertEqual(c.parse_json(result('{}',err='warning'),'probe'),{})
    def test_stage_specific_errors_no_private_output(self):
        for response,message in [(result(),'empty_stdout'),(result('private'),'invalid_json'),(result('private',2),'command_failed:exit=2')]:
            with self.assertRaisesRegex(c.ProbeError,'lve:'+message) as error:c.parse_json(response,'lve')
            self.assertNotIn('private',str(error.exception))
    def test_target_validation(self):
        c.validate_target(copy.deepcopy(TARGET))
        for change in ({'source_id':1},{'host':'cloud.example.net\n'},{'server_ip':'wrong'}):
            with self.assertRaises((RuntimeError,ValueError)):c.validate_target(dict(TARGET,**change))
    def test_wordpress_unit_injection_rejected(self):
        target=dict(TARGET,wordpress=[{'user':'account','domain':'example.net','docroot':'/home/account/html\nExecStart=bad','expires_at':'2026-10-21T00:00:00+00:00'}])
        with self.assertRaisesRegex(c.ProbeError,'unsafe_docroot'):c.validate_target(target)

class MaintenanceTests(unittest.TestCase):
    def test_reboot_logs_are_private_bounded_and_removed(self):
        observed=[]
        def runner(args):
            folder=Path(next(arg.split('=',2)[2] for arg in args if arg.startswith('--setopt=logdir=')))
            observed.append(folder)
            self.assertEqual(folder.stat().st_mode & 0o777,0o700)
            for option,name in (('cachedir','cache'),('persistdir','state')):
                directory=Path(next(arg.split('=',2)[2] for arg in args if arg.startswith('--setopt='+option+'=')))
                self.assertEqual(directory,folder.parent/name)
                self.assertEqual(directory.stat().st_mode & 0o777,0o700)
            (folder.parent/'cache'/'metadata_lock.pid').write_text('lock')
            self.assertIn('--cacheonly',args)
            self.assertIn('--setopt=logfilelevel=0',args)
            self.assertIn('--setopt=log_size=64k',args)
            self.assertIn('--setopt=log_rotate=1',args)
            (folder/'dnf.log').write_text('private diagnostic')
            return result('Core libraries updated: kernel',1)
        answer=health.observe(runner,lambda name:'/usr/bin/needs-restarting' if name=='needs-restarting' else None)
        self.assertEqual(answer['reboot_status'],'indicated')
        self.assertTrue(observed)
        self.assertFalse(observed[0].exists())
        self.assertFalse(observed[0].parent.exists())
        self.assertNotIn('private diagnostic',json.dumps(answer))

    def test_reboot_logs_are_removed_when_probe_raises(self):
        observed=[]
        def runner(args):
            folder=Path(next(arg.split('=',2)[2] for arg in args if arg.startswith('--setopt=logdir=')))
            observed.append(folder);(folder/'dnf.log').write_text('diagnostic')
            raise c.ProbeError('timeout:needs-restarting')
        answer=health.observe(runner,lambda name:'/usr/bin/needs-restarting' if name=='needs-restarting' else None)
        self.assertEqual(answer['reason'],'reboot_probe_failed')
        self.assertFalse(observed[0].exists())

    def test_read_only_dnf_failure_is_not_misclassified_as_reboot(self):
        answer=health.observe(lambda args:result('',1,'Config error: read-only filesystem'),
            lambda name:'/usr/bin/needs-restarting' if name=='needs-restarting' else None)
        self.assertEqual(answer['reboot_status'],'unknown')
        self.assertEqual(answer['reason'],'reboot_probe_failed')

    def test_three_reboot_states_and_no_kernelcare_calls(self):
        for code,out,expected in ((0,'No core libraries updated',False),
                                  (1,'Core libraries updated: kernel',True),
                                  (2,'',None),(1,'',None),(0,'',None)):
            with self.subTest(code=code,out=out):
                def finder(name):
                    self.assertEqual(name,'needs-restarting')
                    return '/usr/bin/needs-restarting'
                answer=health.observe(lambda args:result(out,code),finder)
                self.assertIs(answer['reboot_recommended'],expected)
                self.assertNotIn('kernelcare',answer)

    def test_missing_probe_is_unknown(self):
        answer=health.observe(lambda args:None,lambda name:None)
        self.assertIsNone(answer['reboot_recommended'])
        self.assertEqual(answer['reason'],'reboot_probe_unavailable')

    def test_errors_with_stdout_are_not_reboot_recommendations(self):
        for code in (0,1):
            answer=health.observe(lambda args:result('partial output',code,'command error'),
                lambda name:'/usr/bin/needs-restarting')
            self.assertIsNone(answer['reboot_recommended'])
            self.assertEqual(answer['reason'],'reboot_probe_failed')

class CollectorTests(unittest.TestCase):
    def test_solo_no_lve_command_or_fake_zero(self):
        out=col.Output(PROFILE,{},now=0)
        with patch.object(col,'command',side_effect=AssertionError('unexpected')):
            self.assertEqual(col.collect_lve(out,dict(PROFILE,capabilities={'lve_status':'not_supported'})),'not_supported')
        self.assertEqual(out.metrics,[]);self.assertEqual(out.logs,[])
    def test_traffic_counts_preserved(self):
        state={};logs=[];metrics=[]
        for now in (60,120,180,240,300):
            out=col.Output(PROFILE,state,now=now)
            out.log('site_traffic',site='example.net',user='account',interval_seconds=60,coverage_complete=True,requests=7,errors_5xx=0,breakdown={'route:home':7})
            out.metric('requests',7/60);logs.extend(out.logs);metrics.extend(out.metrics);state=json.loads(json.dumps(state))
        self.assertEqual(len(logs),1);self.assertEqual(logs[0]['requests'],35);self.assertEqual(logs[0]['breakdown']['route:home'],35);self.assertEqual(len(metrics),5)
    def test_5xx_flushes_accumulated_counts(self):
        state={};fields=dict(site='example.net',user='account',interval_seconds=60,coverage_complete=True,requests=2,breakdown={})
        col.Output(PROFILE,state,now=60).log('site_traffic',errors_5xx=0,**fields)
        out=col.Output(PROFILE,state,now=120);out.log('site_traffic',errors_5xx=1,**fields)
        self.assertEqual(out.logs[0]['requests'],4)
    def test_blocked_processes_retained(self):
        state={};col.Output(PROFILE,state,now=0);out=col.Output(PROFILE,state,now=60)
        out.log('process_sample',state='R');out.log('process_sample',state='D');self.assertEqual(len(out.logs),1)
    def test_heartbeat_changes_and_recovery(self):
        state={};logs=[]
        for now,value in ((0,True),(60,True),(120,'failed'),(180,True)):
            out=col.Output(PROFILE,state,now=now);out.log('collector_heartbeat',components={'lve':value});logs.extend(out.logs)
        self.assertEqual(len(logs),3)
    def test_wordpress_eof_and_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);log=root/'error_log';log.write_text('PHP Fatal error historical token=private\n')
            p=dict(PROFILE,wordpress=[{'domain':'example.net','user':'account','docroot':str(root),'expires_at':'2099-01-01T00:00:00+00:00'}]);state={}
            col.collect_wordpress(col.Output(PROFILE,state),state,p)
            with log.open('a') as f:f.write('PHP Fatal error new customer@example.net token=private\n')
            out=col.Output(PROFILE,state);col.collect_wordpress(out,state,p)
            self.assertEqual(out.logs[0]['counts'],{'fatal':1});self.assertNotIn('private',json.dumps(out.logs));self.assertNotIn('customer@',json.dumps(out.logs))
    def test_expired_wordpress_not_read(self):
        p=dict(PROFILE,wordpress=[{'domain':'example.net','user':'account','docroot':'/missing','expires_at':'2000-01-01T00:00:00+00:00'}]);out=col.Output(PROFILE,{})
        col.collect_wordpress(out,{},p);self.assertEqual(out.logs,[])

class MigrationTests(unittest.TestCase):
    def test_vector_transport_unchanged(self):
        path=Path(__file__).parents[1]/'src/legacy/legacy_cloud_config.py';spec=importlib.util.spec_from_file_location('old_config',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        self.assertEqual(config.make_config(PROFILE,'TEST_SECRET',TARGET['ingesting_host']),old.make_config(PROFILE,'TEST_SECRET',TARGET['ingesting_host']))
    def test_cross_source_rejected(self):
        cfg=config.make_config(PROFILE,'TEST_SECRET',TARGET['ingesting_host']);identity.destination(cfg,TARGET);cfg['sinks']['logs']['uri']='https://example.net/'
        with self.assertRaisesRegex(RuntimeError,'unexpected_destination'):identity.destination(cfg,TARGET)
    def test_hash_excludes_token_rotation_but_detects_config_change(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'vector.json';cfg=config.make_config(PROFILE,'TEST_SECRET',TARGET['ingesting_host']);p.write_text(json.dumps(cfg));before=identity.snapshot([str(p)],str(p))
            for sink in cfg['sinks'].values():sink['auth']['token']='ROTATED_SECRET'
            p.write_text(json.dumps(cfg));self.assertEqual(before,identity.snapshot([str(p)],str(p)))
            cfg['sources']['host']['scrape_interval_secs']=10;p.write_text(json.dumps(cfg));self.assertNotEqual(before,identity.snapshot([str(p)],str(p)))
    def test_rollback_preserves_checkpoints(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            root=Path(d);state=root/'state';state.mkdir();p=root/'collector.py';p.write_text('old');checkpoint=root/'checkpoint';checkpoint.write_text('keep')
            with patch.object(installer,'STATE',state),patch.object(installer,'run',return_value=result(code=1)),patch.object(installer,'execute',return_value=result()):
                record=installer.backup([p,root/'new'],['test.timer']);p.write_text('new');(root/'new').write_text('new');installer.restore(record)
            self.assertEqual(p.read_text(),'old');self.assertEqual(checkpoint.read_text(),'keep');self.assertFalse((root/'new').exists())
    def test_unknown_legacy_not_adopted(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            root=Path(d);(root/'complete').write_text('yes');old={'spec':{'target':dict(TARGET,template_version='3'),'base_variant':'unknown'}}
            with patch.object(installer,'LEGACY',root),patch.object(installer,'load',return_value=old):
                with self.assertRaisesRegex(RuntimeError,'unknown_layout'):installer.validate_migration(TARGET)


class ReviewedMigrationTests(unittest.TestCase):
    def old(self,variant):
        paths={'cloudlinux-v2':('/etc/duplika-betterstack-cloudlinux/vector.json','/etc/duplika-betterstack-cloudlinux/profile.json'),
               'almalinux-wordpress-v2':('/etc/duplika-betterstack/vector.yaml','/etc/duplika-betterstack/profile.json'),
               'almalinux-wordpress-context-r6':('/etc/vector/vector.yaml','/etc/vector/duplika-profile.json')}
        cfg,profile=paths[variant]
        return {'baseline':{'files':{},'vector_running_binary_sha256':'a'*64},'spec':{'target':dict(TARGET,template_version='3'),
            'base_variant':variant,'config':cfg,'profile':profile}}
    def validate(self,variant,drift=False,healthy=True,binary_update=False,proof_error=False):
        import types
        old=self.old(variant)
        current=copy.deepcopy(old['baseline'])
        if binary_update:current['vector_running_binary_sha256']='b'*64
        if drift:current['unexpected_change']=True
        fake=types.SimpleNamespace(expected_config=lambda spec:None,
            observe=lambda spec:(current,{'service':healthy},'', ''))
        spec=types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module:None))
        with tempfile.TemporaryDirectory(dir='/root') as d:
            root=Path(d);(root/'complete').write_text('yes')
            with patch.object(installer,'LEGACY',root),patch.object(installer,'load',return_value=old),patch.object(installer.shutil,'copyfile'),patch.object(identity,'read',return_value=b'reviewed'),patch.object(installer.importlib.util,'spec_from_file_location',return_value=spec),patch.object(installer.importlib.util,'module_from_spec',return_value=fake),patch.object(installer,'verify_packaged_vector',return_value={'kind':'rpm_verified_vector_update'},side_effect=RuntimeError('rpm proof failed') if proof_error else None) as review:
                answer=installer.validate_migration(TARGET)
                if binary_update:review.assert_called_once_with(old['spec'],'b'*64)
                else:review.assert_not_called()
                self.assertEqual(old['baseline']['vector_running_binary_sha256'],'a'*64)
                return answer
    def test_all_three_reviewed_variants(self):
        for variant in ('cloudlinux-v2','almalinux-wordpress-v2','almalinux-wordpress-context-r6'):
            self.assertEqual(self.validate(variant)['spec']['base_variant'],variant)
    def test_drift_blocks_adoption(self):
        with self.assertRaisesRegex(RuntimeError,'drift_or_unhealthy'):self.validate('cloudlinux-v2',drift=True)
    def test_unhealthy_service_blocks_adoption(self):
        with self.assertRaisesRegex(RuntimeError,'drift_or_unhealthy'):self.validate('almalinux-wordpress-v2',healthy=False)
    def test_reviewed_package_update_allowed_for_all_three_v3_variants(self):
        for variant in ('cloudlinux-v2','almalinux-wordpress-v2','almalinux-wordpress-context-r6'):
            answer=self.validate(variant,binary_update=True)
            self.assertEqual(answer['migration_review']['previous_sha256'],'a'*64)
            self.assertEqual(answer['baseline']['vector_running_binary_sha256'],'a'*64)
    def test_package_update_does_not_hide_other_drift_or_failed_services(self):
        for change in ({'drift':True},{'healthy':False}):
            with self.assertRaisesRegex(RuntimeError,'drift_or_unhealthy'):
                self.validate('almalinux-wordpress-v2',binary_update=True,**change)
    def test_failed_package_verification_blocks_migration(self):
        with self.assertRaisesRegex(RuntimeError,'rpm proof failed'):
            self.validate('almalinux-wordpress-v2',binary_update=True,proof_error=True)

class VectorPackageTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        import hashlib
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        self.binary=self.root/'vector';self.binary.write_bytes(b'reviewed package binary')
        self.digest=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.metadata='vector\n0.60.0-1.x86_64\n8\n'
        self.listing=str(self.binary)+'\t'+self.digest+'\n/var/lib/vector\t\n'
        self.running=self.binary.stat()
        self.stack.enter_context(patch.object(installer,'trusted_executable',side_effect=lambda name:str(self.binary) if name=='vector' else '/usr/bin/rpm'))
        original_stat=Path.stat
        def stat(path,*args,**kwargs):
            if str(path)=='/proc/123/exe':return self.running
            return original_stat(path,*args,**kwargs)
        self.stack.enter_context(patch.object(Path,'stat',new=stat))
        def execute(args):
            if args[0]=='systemctl':return result('123\n')
            self.assertEqual(args[:3],['/usr/bin/rpm','-qf','--qf'])
            self.assertEqual(args[-1],str(self.binary))
            return result(self.listing if 'FILENAMES' in args[3] else self.metadata)
        self.execute=self.stack.enter_context(patch.object(installer,'execute',side_effect=execute))
    def verify(self):
        return installer.verify_packaged_vector({'vector_service':'fixture.service'},self.digest)
    def test_installed_running_binary_matches_package(self):
        review=self.verify()
        self.assertEqual(review['package'],'vector-0.60.0-1.x86_64')
        self.assertEqual(review['accepted_sha256'],self.digest)
    def test_changed_binary_rejected(self):
        self.listing=self.listing.replace(self.digest,'0'*64)
        with self.assertRaisesRegex(RuntimeError,'does_not_match_rpm_digest'):self.verify()
    def test_running_deleted_or_different_file_rejected(self):
        import types
        self.running=types.SimpleNamespace(st_dev=self.running.st_dev,st_ino=self.running.st_ino+1)
        with self.assertRaisesRegex(RuntimeError,'running_file_differs'):self.verify()
    def test_wrong_owner_package_or_algorithm_rejected(self):
        for metadata in ('other\n1-1.x86_64\n8\n','vector\n1-1.x86_64\n1\n'):
            self.metadata=metadata
            with self.assertRaisesRegex(RuntimeError,'unexpected_vector_package'):self.verify()
    def test_missing_or_duplicate_file_digest_rejected(self):
        for listing in ('/different/path\t'+self.digest+'\n',self.listing+self.listing):
            self.listing=listing
            with self.assertRaisesRegex(RuntimeError,'does_not_match_rpm_digest'):self.verify()

class ResumeTests(unittest.TestCase):
    def test_resume_rejects_changed_base_without_activation(self):
        pending = {'phase': 'base_complete', 'target': TARGET, 'paths': [], 'baseline': {'old': 'hash'}}
        with patch.object(installer, 'load', return_value=pending), patch.object(identity, 'snapshot', return_value={}), patch.object(installer, 'activate') as activate:
            with self.assertRaisesRegex(RuntimeError, 'base_changed'):
                installer.finish_install(TARGET, {})
            activate.assert_not_called()

    def test_resume_rejects_incomplete_base(self):
        with patch.object(installer, 'load', return_value={'phase': 'base_installing', 'target': TARGET}):
            with self.assertRaisesRegex(RuntimeError, 'base_not_complete'):
                installer.finish_install(TARGET, {})

if __name__ == '__main__':
    unittest.main()
