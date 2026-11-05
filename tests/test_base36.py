"""Reviewed migration boundaries, rollback and common capabilities."""
import contextlib
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import base_monitoring as b
import config
import identity
import installer as i
import essential_upgrade
import minimal_host
import legacy_upgrade
import test_minimal_host35
from test_upgrade_rc15 import UpgradeTests, TARGET


class BaseTests(unittest.TestCase):
    def test_capabilities_do_not_invent_legacy_probes_or_lve(self):
        legacy = b.contract(False, {'lve_status':'available'})
        observer = b.contract(True, {'lve_status':'not_applicable'})
        self.assertFalse(legacy['storage_observer'])
        self.assertFalse(legacy['lve_history'])
        self.assertFalse(observer['lve_history'])
        self.assertEqual(legacy['application_logs'],'disabled')
        self.assertEqual(legacy['cpu_aggregation'],observer['cpu_aggregation'])

    def test_fresh_graph_never_has_per_core_output_and_preserves_transport(self):
        profile = dict(TARGET,monitoring_policy='essential-v1',disk_io_observer=True)
        old = config.make_config(profile,'TEST_SECRET',TARGET['ingesting_host'])
        new = config.make_config(dict(profile,base_monitoring=True),'TEST_SECRET',TARGET['ingesting_host'])
        self.assertEqual(new['sources'],old['sources'])
        self.assertEqual(new['data_dir'],old['data_dir'])
        self.assertEqual(new['sinks']['metrics']['buffer'],old['sinks']['metrics']['buffer'])
        self.assertNotIn('host_essential',new['sinks']['metrics']['inputs'])
        self.assertEqual(new['transforms']['base_cpu_reduce']['group_by'],['dt','tags.host','cpu_mode'])
        with self.assertRaisesRegex(RuntimeError,'unexpected_cpu_graph'):
            b.cpu_graph(new,'host_essential','metrics')

    def test_fresh_preflight_uses_history_not_full_live_account_scan(self):
        with patch.object(i,'base_preflight') as base, \
             patch.object(essential_upgrade,'disk_coverage',return_value={'devices':2}), \
             patch.object(essential_upgrade,'storage_coverage',return_value=1), \
             patch('collector.query_pressure',return_value=None), \
             patch.object(i,'resource_preflight') as live:
            b.fresh_preflight(i,TARGET,{'lve_status':'not_applicable'})
        base.assert_called_once_with(TARGET,essential_base=True)
        live.assert_not_called()

    def test_direct_install_stages_essential_selection_before_any_collector_start(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir='/root') as folder:
            root=Path(folder);(root/'state').mkdir()
            credential=root/'credential.json'
            credential.write_text(json.dumps({'source_id':TARGET['source_id'],
                'host':TARGET['ingesting_host'],'token':'TEST_SECRET'}))
            staged=[]
            def run(args,**kw):
                deployment=Path(args[1]).parent/'deployment.json'
                staged.append(json.loads(deployment.read_text()))
                self.assertTrue((deployment.parent/'base_monitoring.py').is_file())
                if '--preflight' not in args:
                    raise RuntimeError('stop_before_native_install')
                return subprocess.CompletedProcess(args,0,'','')
            with patch.object(i,'BASE',root/'base'),patch.object(i,'STATE',root/'state'), \
                 patch.object(i,'LEGACY',root/'legacy'),patch.object(b,'fresh_preflight'), \
                 patch.object(i,'run',side_effect=run),patch.object(i,'resource_preflight') as live:
                with self.assertRaisesRegex(RuntimeError,'stop_before_native_install'):
                    i.fresh(TARGET,{'os_id':'cloudlinux'},credential,essential_base=True)
            self.assertEqual(len(staged),2)
            self.assertTrue(all(x['base_monitoring'] is True for x in staged))
            live.assert_not_called()


class ObserverBaseTests(unittest.TestCase):
    previous_version = '4.0.0-rc34'
    def setUp(self):
        UpgradeTests.setUp(self)
        self.profile = dict(TARGET,agent_version=self.previous_version,
            monitoring_policy='essential-v1',disk_io_observer=True)
        (i.CLOUD/'profile.json').write_text(json.dumps(self.profile))
        (i.CLOUD/'vector.json').write_text(json.dumps(config.make_config(
            self.profile,'TEST_SECRET',TARGET['ingesting_host'])))
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'],self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        self.stack.enter_context(patch.object(i,'run',return_value=subprocess.CompletedProcess([],3,'','')))
        self.stack.enter_context(patch.object(identity,'status'))
        self.stack.enter_context(patch.object(i.context_cleanup,'validate'))
        self.stack.enter_context(patch.object(essential_upgrade,'storage_coverage',return_value=2))
        self.stack.enter_context(patch.object(essential_upgrade,'disk_coverage',return_value={'devices':2}))
        self.stack.enter_context(patch.object(i,'observer_quiet_point',side_effect=contextlib.nullcontext))
        self.stack.enter_context(patch.object(i,'wait_observer_inactive'))

    def test_plan_does_not_write_and_rejects_drift_even_rebaselined_graph(self):
        before = (i.CLOUD/'vector.json').read_bytes()
        with patch.object(i,'backup') as backup:
            old,new = b.plan(i,TARGET,{})
        backup.assert_not_called()
        self.assertEqual((i.CLOUD/'vector.json').read_bytes(),before)
        cfg = json.loads(before);cfg['sources']['host']['scrape_interval_secs']=10
        (i.CLOUD/'vector.json').write_text(json.dumps(cfg))
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(RuntimeError,'differs_from_reviewed_graph'):
            b.plan(i,TARGET,{})

    def test_upgrade_preserves_offsets_storage_and_rolls_back_failed_validation(self):
        checkpoint=i.CLOUD_DATA/'private/checkpoint';checkpoint.write_text('advanced')
        storage={p:p.read_bytes() for p in i.storage_paths()}
        calls=[]
        def execute(args,**kw):
            calls.append(args)
            if args[:3]==['runuser','-u','duplikaobs']:
                raise RuntimeError('native_failure')
            return subprocess.CompletedProcess(args,0,'','')
        with patch.object(i,'backup',return_value={'fixture':True}), \
             patch.object(i,'execute',side_effect=execute), \
             patch.object(i,'recover_failure') as recovery, patch.object(i,'activate') as activate:
            with self.assertRaisesRegex(RuntimeError,'native_failure'):
                b.migrate(i,TARGET,{},apply=True)
        recovery.assert_called_once();activate.assert_not_called()
        self.assertEqual(checkpoint.read_text(),'advanced')
        for p,value in storage.items():self.assertEqual(p.read_bytes(),value)

    def test_success_changes_only_monitor_graph_and_declares_preserved_storage(self):
        old=json.loads((i.CLOUD/'vector.json').read_text())
        with patch.object(i,'backup',return_value={}), \
             patch.object(i,'execute',return_value=subprocess.CompletedProcess([],0,'','')), \
             patch.object(i,'verify_new_observer_cycle') as cycle, patch.object(i,'activate') as activate:
            b.migrate(i,TARGET,{},apply=True)
        new=json.loads((i.CLOUD/'vector.json').read_text())
        self.assertEqual(new['sources'],old['sources'])
        self.assertEqual(new['data_dir'],old['data_dir'])
        self.assertEqual(new['sinks']['logs'],old['sinks']['logs'])
        self.assertEqual(new['sinks']['metrics']['auth'],old['sinks']['metrics']['auth'])
        self.assertTrue(activate.call_args.kwargs['storage_preserved'])
        cycle.assert_called_once()


class LegacyBaseTests(test_minimal_host35.MinimalHostTests):
    release='4.0.0-rc35'
    variant='almalinux-wordpress-v2'
    system=test_minimal_host35.MinimalHostTests.system
    save=test_minimal_host35.MinimalHostTests.save

    def setUp(self):
        test_minimal_host35.MinimalHostTests.setUp(self)
        self.path.write_text(json.dumps(minimal_host.prepare(self.before_config)))
        self.meta['migration_review']['minimal_host']=minimal_host.MARKER
        timer,service=minimal_host.retired_units(self.meta)
        self.meta['services']=[self.meta['services'][0],i.NAME+'.timer']
        self.meta['oneshots']=[]
        self.active.discard(timer);self.active.discard(service);self.enabled.discard(timer)
        self.save()

    def test_minimal_migration_and_repeat_preserve_no_logs_and_buffers(self):
        before=json.loads(self.path.read_text())
        b.migrate(i,TARGET,self.caps,apply=True)
        cfg=json.loads(self.path.read_text())
        self.assertEqual(cfg['sources'],before['sources'])
        self.assertEqual(cfg['data_dir'],before['data_dir'])
        self.assertEqual(cfg['sinks']['duplika_metrics']['buffer'],before['sinks']['duplika_metrics']['buffer'])
        meta=json.loads((i.BASE/'manifest.json').read_text())
        self.assertEqual(meta['base_template_id'],b.TEMPLATE)
        self.assertEqual(meta['monitoring_policy'],b.POLICY)
        self.assertFalse(meta['monitoring_capabilities']['storage_observer'])
        self.assertFalse(meta['monitoring_capabilities']['lve_history'])
        legacy_upgrade.validate(i,meta,TARGET)
        with patch.object(i,'backup') as backup:
            b.migrate(i,TARGET,self.caps,apply=True)
        backup.assert_not_called()

    def test_native_failure_restores_original_graph_and_retired_units(self):
        original={p:p.read_bytes() for p in [self.path,*i.runtime_files(),i.BASE/'manifest.json',i.BASE/'complete']}
        def fail(args,**kw):
            if args[:3]==['runuser','-u','vector']:raise RuntimeError('native_failure')
            return self.system(args,**kw)
        with patch.object(i,'execute',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'native_failure'):
                b.migrate(i,TARGET,self.caps,apply=True)
        for path,value in original.items():self.assertEqual(path.read_bytes(),value)
        minimal_host.ensure_retired(i,self.meta)


for inherited in dir(test_minimal_host35.MinimalHostTests):
    if inherited.startswith('test_') and inherited not in LegacyBaseTests.__dict__:
        setattr(LegacyBaseTests,inherited,None)


class LegacyContextBaseTests(LegacyBaseTests):
    variant='almalinux-wordpress-minimal-v4'


if __name__=='__main__':unittest.main()
