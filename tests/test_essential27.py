"""Behavioral regressions for bounded, pressure-only collection and migration."""
import contextlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
import types
from unittest.mock import patch

import collector as c
import config
import essential_upgrade as e
import installer as i
from test_upgrade_rc15 import UpgradeTests
from test_release26 import builder_module, unpack


def row(user='example', **faults):
    return {'username': user, 'faults': {key: {'lve': value} for key, value in faults.items()},
            'usage': {'cpu': {'lve': 90, 'mysql': 20}}, 'limits': {'cpu': {'lve': 100}}}


class PressureTests(unittest.TestCase):
    def out(self):
        return c.Output({'host': 'cloud.example.net', 'server_ip': '203.0.113.20'}, {}, 1800000100)

    def test_threshold_is_strict_per_resource_not_combined(self):
        data = {'result': 'success', 'users': [row('first', cpu=6, pmem=6), row('second', cpu=10), row('third', cpu=11)]}
        values = c.pressure_rows(data)
        self.assertEqual([x['user'] for x in values], ['third'])
        self.assertEqual(values[0]['resources']['cpu'], {'faults': 11, 'usage_average': 90.0, 'limit': 100.0})

    def test_no_usage_percent_filter_or_normal_account_metrics(self):
        self.assertEqual(c.pressure_rows({'result': 'success', 'users': [row(cpu=0)]}), [])

    def test_malformed_counts_and_unknown_schema_fail_closed(self):
        for bad in (-1, float('nan'), 1.5, '11', True, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(RuntimeError, 'invalid_fault_count'):
                c.pressure_rows({'result': 'success', 'users': [row(cpu=bad)]})
        for data in ({'result': 'failure', 'users': []}, {'result': 'success', 'users': [{}]},
                     {'result': 'success', 'users': [{'faults': {}}]}):
            with self.assertRaises(RuntimeError): c.pressure_rows(data)

    def test_duplicate_selected_identity_is_not_double_counted(self):
        with self.assertRaisesRegex(RuntimeError, 'duplicate_account'):
            c.pressure_rows({'result': 'success', 'users': [row(cpu=11), row(pmem=15)]})

    def test_one_successful_window_once_and_failure_does_not_advance(self):
        out, state, now = self.out(), {}, 1800000100
        start, end = c.pressure_window(now)
        with patch.object(c, 'query_pressure', return_value=(start, end, c.pressure_rows({'result':'success','users':[row(cpu=12)]}))) as query:
            self.assertEqual(c.collect_pressure(out, state, {}, now), 'available')
            c.collect_pressure(out, state, {}, now + 60)
        query.assert_called_once()
        self.assertEqual(len([x for x in out.logs if x['event_kind']=='account_resource_faults']), 1)
        self.assertFalse(any(x['name'].startswith('duplika_lve_cpu') for x in out.metrics))
        with patch.object(c, 'query_pressure', side_effect=RuntimeError('private output')):
            self.assertEqual(c.collect_pressure(out, state, {}, now + 900), 'failed')
        self.assertEqual(state['pressure_v27']['window_end'], end)
        self.assertNotIn('private output', json.dumps(out.logs))

    def test_query_uses_closed_utc_windows_and_no_result_truncation(self):
        with patch.object(c.shutil, 'which', return_value='/usr/bin/python3'), \
             patch.object(c, 'command', return_value=json.dumps({'result':'success','users':[]})) as command:
            start, end, records = c.query_pressure({}, 1800000100)
        args, kwargs = command.call_args
        self.assertEqual(end-start, 900)
        self.assertLessEqual(end, 1800000100-60)
        self.assertNotIn('--period', args[0])
        self.assertIn('--limit=0', args[0]); self.assertIn('--threshold=10', args[0])
        self.assertTrue(kwargs['utc'])

    def test_restart_does_not_replay_and_clock_rollback_is_explicit(self):
        out, state, now = self.out(), {}, 1800000100
        start, end = c.pressure_window(now)
        with patch.object(c, 'query_pressure', return_value=(start, end, [])):
            c.collect_pressure(out, state, {}, now)
        restored = json.loads(json.dumps(state))
        with patch.object(c, 'query_pressure') as query:
            c.collect_pressure(out, restored, {}, now+60)
            c.collect_pressure(out, restored, {}, now-1800)
        query.assert_not_called()
        self.assertTrue(any(x['name']=='duplika_lve_history_clock_rollback' for x in out.metrics))


class EssentialCycleTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        for name in ('base','data/private','spool'): (self.root/name).mkdir(parents=True)
        for key,path in [('BASE','base'),('ROOT','data'),('STATE','data/private'),('SPOOL','spool')]:
            self.stack.enter_context(patch.object(c,key,self.root/path))
        self.profile={'host':'cloud.example.net','server_ip':'203.0.113.20','reader_gid':os.getgid(),
            'monitoring_policy':'essential-v1','capabilities':{'lve_status':'not_applicable'},'error_logs':[]}
        (c.BASE/'profile.json').write_text(json.dumps(self.profile))
        self.stack.enter_context(patch.object(c,'essential_inventory',return_value=({'example.net':'example'},{'example'},{1000:'example'})))

    def test_actual_main_never_opens_routine_site_or_process_readers(self):
        with patch.object(c,'collect_access') as access, patch.object(c,'collect_processes') as processes, \
             patch.object(c,'collect_wordpress') as wordpress, patch.object(c,'collect_lve') as lve, \
             patch.object(c,'collect_kernel',return_value=None), contextlib.redirect_stdout(io.StringIO()):
            c.main(); c.main()
        for function in (access,processes,wordpress,lve): function.assert_not_called()
        metrics=[json.loads(line) for line in (c.SPOOL/'metrics.jsonl').read_text().splitlines()]
        self.assertEqual(len([x for x in metrics if x['name']=='duplika_cpanel_accounts']),1)
        self.assertEqual(len([x for x in metrics if x['name']=='duplika_collector_heartbeat']),2)
        marker=json.loads((c.STATE/'collector-health.json').read_text())
        self.assertEqual(marker['monitoring_policy'],'essential-v1')
        self.assertEqual(marker['components']['access'],'disabled')

    def test_expired_or_nonfinite_diagnostic_never_runs(self):
        path=c.STATE/'diagnostic.json'
        path.write_text(json.dumps({'user':'example','created_at':100,'expires_at':200,'readers':['access']}));path.chmod(0o600)
        self.assertIsNone(c.active_diagnostic(300,{'example'}))
        for key in ('created_at','expires_at'):
            item={'user':'example','created_at':100,'expires_at':200,'readers':['access']};item[key]=float('nan')
            path.write_text(json.dumps(item))
            with self.assertRaises(RuntimeError):c.active_diagnostic(150,{'example'})

    def test_diagnostic_targets_only_selected_domains_then_expires(self):
        now=time.time();path=c.STATE/'diagnostic.json'
        path.write_text(json.dumps({'user':'example','created_at':now,'expires_at':now+60,'readers':['access']}));path.chmod(0o600)
        with patch.object(c,'collect_access',return_value={}) as access, \
             patch.object(c,'collect_kernel',return_value=None), contextlib.redirect_stdout(io.StringIO()):
            c.main()
        self.assertEqual(access.call_args.args[2],{'example.net':'example'})
        self.assertIsNone(c.active_diagnostic(now+61,{'example'}))

    def test_graph_keeps_sinks_and_core_but_removes_duplicate_scraping(self):
        old=config.make_config(self.profile | {'monitoring_policy':None},'TEST_SECRET','s1234568.us-west-2a.betterstackdata.com')
        new=config.essential_config(old)
        self.assertNotIn('filesystem',new['sources']['host']['collectors'])
        self.assertEqual(new['sources']['host_slow']['scrape_interval_secs'],300)
        for name in old['sinks']:
            for field in ('uri','auth','buffer','batch'):
                self.assertEqual(old['sinks'][name][field],new['sinks'][name][field])
        self.assertEqual(old['data_dir'],new['data_dir'])

    def test_distributed_essential_cycle_sets_real_policy_and_release_marker(self):
        builder_module().build(self.root/'release')
        files=unpack((self.root/'release/installer.sh').read_text())
        packaged=types.ModuleType('packaged_essential')
        exec(compile(files['collector.py'],'packaged-essential.py','exec'),packaged.__dict__)
        with contextlib.ExitStack() as stack:
            for key in ('BASE','ROOT','STATE','SPOOL'):
                stack.enter_context(patch.object(packaged,key,getattr(c,key)))
            stack.enter_context(patch.object(packaged,'essential_inventory',return_value=({'example.net':'example'},{'example'},{1000:'example'})))
            stack.enter_context(patch.object(packaged,'collect_kernel',return_value=None))
            access=stack.enter_context(patch.object(packaged,'collect_access'))
            started=time.time()
            with contextlib.redirect_stdout(io.StringIO()):packaged.main()
            access.assert_not_called()
        marker=json.loads((c.STATE/'collector-health.json').read_text())
        self.assertEqual(marker['monitoring_policy'],'essential-v1')
        self.assertEqual(marker['agent_version'],i.VERSION)
        with patch.object(i,'CLOUD_DATA',c.ROOT):i.verify_new_observer_cycle(started)


class InventoryCacheTests(unittest.TestCase):
    def test_map_changes_invalidate_cache_without_periodic_passwd_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);domains=root/'userdomains';passwd=root/'passwd'
            domains.write_text('first');passwd.write_text('first')
            mapper=lambda name: {'/etc/userdomains':domains,'/etc/passwd':passwd}.get(name,Path(name))
            state={}
            with patch.object(c,'Path',side_effect=mapper),patch.object(c,'inventory',side_effect=[({'example.net':'example'},{'example'},{1000:'example'}),({'other.example.net':'other'},{'other'},{1001:'other'})]) as inventory:
                c.essential_inventory(state);c.essential_inventory(state)
                self.assertEqual(inventory.call_count,1)
                stamp=domains.stat().st_mtime_ns+1000000000;os.utime(domains,ns=(stamp,stamp))
                result=c.essential_inventory(state)
                self.assertEqual(inventory.call_count,2)
                self.assertEqual(result[1],{'other'})


class Rc26ProofTests(UpgradeTests):
    previous_version='4.0.0-rc26'


class ExplicitMigrationTests(Rc26ProofTests):
    def test_real_transaction_restores_old_config_and_metadata_not_offsets(self):
        originals={p:p.read_bytes() for p in [i.CLOUD/'vector.json',i.CLOUD/'profile.json',i.CLOUD/'collector.py',i.BASE/'manifest.json',i.BASE/'complete']}
        cursor=i.CLOUD_DATA/'private/checkpoint';cursor.write_text('initial')
        def execute(args,**kwargs):
            if args[0]=='runuser':
                cursor.write_text('advanced')
                raise RuntimeError('reader failed')
        with patch.object(e,'plan',return_value=(self.meta,{}, {'fixture':True},{})), \
             patch.object(i,'execute',side_effect=execute),patch.object(i,'observer_quiet_point',side_effect=contextlib.nullcontext):
            with self.assertRaisesRegex(RuntimeError,'reader failed'):e.migrate(i,self.meta['target'],{},apply=True)
        for p,data in originals.items():self.assertEqual(p.read_bytes(),data)
        self.assertEqual(cursor.read_text(),'advanced')

    def test_success_preserves_offsets_storage_and_requires_real_policy_marker(self):
        before={p:p.read_bytes() for p in i.storage_paths()}
        checkpoint=i.CLOUD_DATA/'private/checkpoint';checkpoint.write_text('keep cursor')
        proposed={'fixture':'reviewed graph'};calls=[]
        def execute(args,**kwargs):
            calls.append(args)
            if args==['systemctl','start','duplika-cloudlinux-observer.service']:
                (i.CLOUD_DATA/'private/collector-health.json').write_text(json.dumps({'at':time.time(),'started_at':time.time(),
                    'agent_version':i.VERSION,'monitoring_policy':'essential-v1','components':{'processes':'disabled','access':'disabled','lve':'available'}}))
        with patch.object(e,'plan',return_value=(self.meta,{'agent_version':'4.0.0-rc26'},proposed,{})), \
             patch.object(i,'execute',side_effect=execute),patch.object(i,'observer_quiet_point',side_effect=contextlib.nullcontext), \
             patch.object(i,'wait_observer_inactive'),patch.object(i,'backup',return_value={}),patch.object(i,'activate') as activate:
            e.migrate(i,i.TARGET if hasattr(i,'TARGET') else self.meta['target'],{},apply=True)
        activate.assert_called_once();self.assertEqual(checkpoint.read_text(),'keep cursor')
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)
        self.assertEqual(json.loads((i.CLOUD/'profile.json').read_text())['monitoring_policy'],'essential-v1')

    def test_native_failure_rolls_back_without_activation(self):
        def execute(args,**kwargs):
            if args[0]=='runuser':raise RuntimeError('reader denied')
        with patch.object(e,'plan',return_value=(self.meta,{}, {'fixture':True},{})), \
             patch.object(i,'execute',side_effect=execute),patch.object(i,'observer_quiet_point',side_effect=contextlib.nullcontext), \
             patch.object(i,'backup',return_value={'fixture':True}),patch.object(i,'recover_failure') as rollback,patch.object(i,'activate') as activate:
            with self.assertRaisesRegex(RuntimeError,'reader denied'):e.migrate(i,self.meta['target'],{},apply=True)
        rollback.assert_called_once();activate.assert_not_called()
