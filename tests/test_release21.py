"""Log volume controls preserve incident detail and per-cycle metrics."""
import copy
import json
import unittest
from unittest.mock import patch
import collector as c
import error_aggregation as a
import test_upgrade_rc15

P = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20'}

class Rc20UpgradeTests(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc20'

class LogTests(unittest.TestCase):
    def test_routine_process_details_suppressed_at_every_resource_sample(self):
        state = {}; logs = []; metrics = []
        for now in range(0, 1801, 300):
            out=c.Output(P,state,now)
            for status in ('R','S','I'):
                out.log('process_sample', state=status, cpu_percent=None if now == 0 else 2)
            out.metric('resource_cpu',2)
            logs += out.logs; metrics += out.metrics
        self.assertEqual(logs,[]); self.assertEqual(len(metrics),7)

    def test_incident_detail_is_per_account_reason_not_pid_and_survives_restart(self):
        state={}; logs=[]
        for now in (0,300,900,1000):
            out=c.Output(P,state,now)
            for pid in range(20):
                out.log('process_sample',user='account',uid=1001,pid=pid+now,state='D',cpu_percent=0)
                out.log('process_sample',user='account',uid=1001,pid=pid+now,state='R',cpu_percent=90)
            logs += out.logs;state=json.loads(json.dumps(state))
        self.assertEqual(len(logs),4)
        self.assertEqual({x['log_reason'] for x in logs},{'blocked_process','high_cpu_process'})
        out=c.Output(P,state,now=1);out.log('process_sample',user='account',uid=1001,state='D')
        self.assertEqual(len(out.logs),1)

    def test_detail_state_is_bounded_and_pruned(self):
        state={};out=c.Output(P,state,now=0)
        for uid in range(1100):out.log('process_sample',uid=uid,state='D')
        self.assertLessEqual(len(state['process_detail_v21']),1024)
        c.Output(P,state,now=86401).log('process_sample',state='S')
        self.assertEqual(state['process_detail_v21'],{})

    def test_coverage_flapping_keeps_metrics_and_does_not_emit_false_recovery(self):
        state={};logs=[];metrics=[]
        sequence=[False,True,False,True,False,True,True,True]
        for n,complete in enumerate(sequence):
            out=c.Output(P,state,now=n*60)
            out.log('access_log_coverage',coverage_complete=complete)
            out.metric('duplika_access_coverage_complete',int(complete))
            logs+=out.logs;metrics+=out.metrics
        self.assertEqual([x['coverage_complete'] for x in logs],[False,True])
        self.assertEqual([x['gauge']['value'] for x in metrics],list(map(int,sequence)))

    def test_long_gap_or_clock_rollback_resets_recovery_streak(self):
        for times in ((0,60,600),(100,160,1)):
            state={}
            for now in times:
                out=c.Output(P,state,now);out.log('access_log_coverage',coverage_complete=True)
            self.assertEqual(out.logs,[])

class AggregationTests(unittest.TestCase):
    def test_only_log_sink_changes_and_original_remains_immutable(self):
        cfg={'sources':{'file':{}},'transforms':{'useful_errors':{}},
             'sinks':{'duplika_logs':{'inputs':['useful_errors'],'auth':{'token':'TEST_SECRET'}},
                      'metrics':{'inputs':['metrics']}}}
        before=copy.deepcopy(cfg);after=a.prepare(cfg)
        self.assertEqual(cfg,before);self.assertEqual(after['sources'],cfg['sources'])
        self.assertEqual(after['sinks']['metrics'],cfg['sinks']['metrics'])
        self.assertEqual(after['sinks']['duplika_logs']['auth'],cfg['sinks']['duplika_logs']['auth'])
        self.assertEqual(after['transforms']['repeat_reduce']['end_every_period_ms'],300000)
        self.assertNotIn('message',after['transforms']['repeat_reduce']['group_by'])
        self.assertIn('.level != "critical"',a.CONDITION)

    def test_unknown_sink_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'unexpected_log_sink'):
            a.prepare({'sinks':{'duplika_logs':{'inputs':['unknown']}}})

import installer as i
import identity
import legacy_upgrade
from pathlib import Path
from test_release18 import MinimalLegacyTests
from test_upgrade_rc15 import TARGET

class MinimalAggregationMigrationTests(MinimalLegacyTests):
    def test_explicit_upgrade_preserves_owner_mode_sources_metrics_and_checkpoint(self):
        path=Path(self.meta['config_path']);path.chmod(0o640)
        original=json.loads(path.read_text());before=path.stat()
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],str(path))
        cursor=i.CLOUD_DATA/'private/checkpoint';cursor.write_text('offset retained')
        with patch.object(i,'validate_v4_upgrade',return_value=self.meta),patch.object(identity,'status'),patch.object(i,'execute'),patch.object(i,'trusted_executable',return_value=None),patch.object(i.context_cleanup,'validate'),patch.object(i,'activate') as activate,patch.object(i,'backup',return_value={}):
            legacy_upgrade.upgrade(i,TARGET,{},True)
        changed=json.loads(path.read_text())
        self.assertEqual(changed['sources'],original['sources'])
        self.assertEqual(changed['sinks']['duplika_metrics'],original['sinks']['duplika_metrics'])
        self.assertEqual(cursor.read_text(),'offset retained')
        self.assertEqual((path.stat().st_uid,path.stat().st_gid,path.stat().st_mode),(before.st_uid,before.st_gid,before.st_mode))
        self.assertEqual(activate.call_args.args[9]['error_aggregation'],a.MARKER)

    def test_failed_transport_restart_uses_transaction_recovery(self):
        with patch.object(i,'validate_v4_upgrade',return_value=self.meta),patch.object(identity,'status'),patch.object(i,'trusted_executable',return_value=None),patch.object(i.context_cleanup,'validate'),patch.object(i,'backup',return_value={'transaction':'fixture'}),patch.object(i,'recover_failure') as recovery:
            def execute(args):
                if 'restart' in args:raise RuntimeError('transport_failed')
            with patch.object(i,'execute',side_effect=execute):
                with self.assertRaisesRegex(RuntimeError,'transport_failed'):legacy_upgrade.upgrade(i,TARGET,{},True)
        recovery.assert_called_once()
        self.assertEqual(recovery.call_args.args[0],{'transaction':'fixture'})

    def test_marked_config_must_match_aggregation_blueprint(self):
        path=Path(self.meta['config_path']);path.write_text(json.dumps(a.prepare(json.loads(path.read_text()))))
        self.meta['migration_review']={'error_aggregation':a.MARKER}
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],str(path))
        self.assertEqual(legacy_upgrade.validate(i,self.meta,TARGET),self.meta)
        value=json.loads(path.read_text());value['transforms']['repeat_reduce']['end_every_period_ms']=1
        path.write_text(json.dumps(value));self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],str(path))
        with self.assertRaisesRegex(RuntimeError,'differs_from_blueprint'):legacy_upgrade.validate(i,self.meta,TARGET)
