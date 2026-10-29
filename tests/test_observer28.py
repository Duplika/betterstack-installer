"""Behavioral proof of liveness/degradation, privileged I/O and reviewed migrations."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import collector as c
import config
import essential_upgrade as e
import identity
import installer as i
import test_essential27 as previous

DISKS = ('8 0 sda 10 0 20 0 30 0 40 0 0 0 0\n'
         '259 0 nvme0n1 1 0 2 0 3 0 4 0 0 0 0 0 0 0 0\n')
PROFILE = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20'}


class DiskTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'diskstats'; path.write_text(text)
            return c.read_diskstats(path)

    def test_exact_vector_names_units_and_partition_labels(self):
        rows = self.parse(DISKS + '8 1 sda1 0 0 0 0 0 0 0 0 0 0 0\n')
        self.assertEqual(rows['sda'], {'disk_read_bytes_total': 10240,
            'disk_reads_completed_total': 10, 'disk_written_bytes_total': 20480,
            'disk_writes_completed_total': 30})
        self.assertEqual(rows['nvme0n1']['disk_written_bytes_total'], 2048)
        self.assertEqual(rows['sda1']['disk_reads_completed_total'], 0)

    def test_empty_truncated_negative_duplicate_and_oversized_are_failures(self):
        for text in ('', '8 0 sda 0\n', DISKS.replace(' 20 ', ' -1 '), DISKS + DISKS, 'x' * (1024 * 1024 + 1)):
            with self.subTest(text=text[:20]), self.assertRaises(RuntimeError): self.parse(text)

    def test_no_partial_or_stale_counter_replay_and_failure_remains_visible(self):
        state = {}; out = c.Output(PROFILE, state, now=1000)
        with patch.object(c, 'read_diskstats', return_value=self.parse(DISKS)) as read:
            self.assertEqual(c.collect_disk_io(out, state, 1000, 'boot-a'), 'available')
            c.collect_disk_io(out, state, 1299, 'boot-a')
            read.assert_called_once()
        self.assertEqual(len([x for x in out.metrics if 'counter' in x]), 8)
        failed = c.Output(PROFILE, state, now=1300)
        with patch.object(c, 'read_diskstats', side_effect=PermissionError(1, 'PRIVATE_SECRET')):
            self.assertEqual(c.collect_disk_io(failed, state, 1300, 'boot-a'), 'failed')
        self.assertFalse(any('counter' in x for x in failed.metrics))
        self.assertNotIn('PRIVATE_SECRET', json.dumps(failed.logs))
        self.assertEqual(failed.logs[0]['errno'], 1)
        self.assertEqual(next(x['gauge']['value'] for x in failed.metrics if x['name']=='duplika_disk_io_sample_failed'), 1)
        recovery = c.Output(PROFILE, state, now=1600)
        with patch.object(c, 'read_diskstats', return_value=self.parse(DISKS)):
            self.assertEqual(c.collect_disk_io(recovery, state, 1600, 'boot-a'), 'available')

    def test_boot_change_and_clock_rollback_resample_without_waiting(self):
        state = {}; out = c.Output(PROFILE, state, now=1000)
        with patch.object(c, 'read_diskstats', return_value=self.parse(DISKS)) as read:
            for now, boot in ((1000,'a'), (1060,'a'), (1120,'b'), (100,'b')):
                c.collect_disk_io(out, state, now, boot)
            self.assertEqual(read.call_count, 3)

    def test_upgrade_generation_forces_real_sample_after_prior_rollback(self):
        state={};out=c.Output(PROFILE,state,now=1000)
        with patch.object(c,'read_diskstats',return_value=self.parse(DISKS)) as read:
            c.collect_disk_io(out,state,1000,'a',generation=1)
            c.collect_disk_io(out,state,1060,'a',generation=2)
        self.assertEqual(read.call_count,2)

    def test_network_only_graph_never_changes_sink_or_transport_permissions(self):
        old = config.make_config(dict(PROFILE, monitoring_policy='essential-v1'), 'TEST_SECRET',
                                 's1234568.us-west-2a.betterstackdata.com')
        new = config.make_config(dict(PROFILE, monitoring_policy='essential-v1', disk_io_observer=True),
                                 'TEST_SECRET', 's1234568.us-west-2a.betterstackdata.com')
        self.assertEqual(new['sources']['host_slow']['collectors'], ['network'])
        self.assertEqual(new['sources']['host_slow']['scrape_interval_secs'], 300)
        self.assertEqual(old['sinks'], new['sinks'])
        self.assertEqual(old['data_dir'], new['data_dir'])
        self.assertNotIn('filesystem', new['sources']['host']['collectors'])


class CommandDiagnosticsTests(unittest.TestCase):
    def test_nonzero_exit_is_classified_without_arbitrary_output(self):
        result = subprocess.CompletedProcess([], 13, 'PRIVATE_OUTPUT', 'PRIVATE_SECRET Permission denied')
        with patch.object(c.subprocess, 'run', return_value=result), self.assertRaises(c.CommandFailure) as error:
            c.command(['/usr/sbin/cloudlinux-top', '--json'], timeout=40)
        fields = c.error_diagnostic(error.exception)
        self.assertEqual(fields['exit_code'], 13)
        self.assertEqual(fields['tool'], 'cloudlinux-top')
        self.assertEqual(fields['stderr_category'], 'permission_denied')
        self.assertNotIn('PRIVATE', json.dumps(fields))

    def test_timeout_and_missing_tool_keep_reason_without_command_arguments(self):
        for error, reason in ((subprocess.TimeoutExpired(['PRIVATE_SECRET'], 40), 'timeout'),
                              (FileNotFoundError(2, 'PRIVATE_SECRET'), 'execution_failed')):
            with patch.object(c.subprocess, 'run', side_effect=error), self.assertRaises(c.CommandFailure) as failed:
                c.command(['/usr/sbin/cloudlinux-top'], timeout=40)
            fields = c.error_diagnostic(failed.exception)
            self.assertEqual(fields['reason'], reason)
            self.assertEqual(fields['timeout_seconds'], 40)
            self.assertNotIn('PRIVATE', json.dumps(fields))

    def test_changed_reason_is_not_suppressed_for_fifteen_minutes(self):
        state = {}; out = c.Output(PROFILE, state, now=1000)
        for reason in ('timeout', 'timeout', 'command_failed'):
            out.log('collector_error', component='lve_history', **c.error_diagnostic(c.CommandFailure(reason, 'cloudlinux-statistics', 40)))
        self.assertEqual(len(out.logs), 2)
        self.assertNotIn('PRIVATE_SECRET', json.dumps(c.error_diagnostic(RuntimeError('PRIVATE_SECRET'))))


class HeartbeatTests(unittest.TestCase):
    def check(self, marker):
        with patch.object(identity, 'read', return_value=json.dumps(marker).encode()), patch.object(identity.time, 'time', return_value=1000):
            return identity.observer_health({'resource_sampling': {'lve_seconds': 900}})

    def test_fresh_lve_failure_is_degraded_not_absent_and_zero_count_is_healthy(self):
        value = self.check({'at':999, 'components':{'lve':'failed','processes':0,'access':'disabled'}})
        self.assertEqual(value, {'heartbeat':'fresh','status':'degraded','degraded_components':['lve']})
        self.assertEqual(self.check({'at':999,'components':{'lve':True,'processes':0}})['status'], 'ok')
        self.assertEqual(self.check({'at':999,'components':{'lve':False}})['status'], 'degraded')

    def test_missing_stale_invalid_and_previous_boot_are_distinct(self):
        with patch.object(identity, 'read', side_effect=FileNotFoundError):
            self.assertEqual(identity.observer_health({'resource_sampling':True})['heartbeat'], 'missing')
        for at, expected in ((819,'stale'), (1001,'invalid'), (float('nan'),'invalid'), (True,'invalid'), (None,'invalid')):
            self.assertEqual(self.check({'at':at,'components':{'lve':True}})['heartbeat'], expected)
        for marker in ([], {}, {'at':999,'components':[]}, {'at':999,'components':{}}):
            self.assertEqual(self.check(marker)['heartbeat'], 'invalid')
        with patch.object(identity.Path, 'read_text', return_value='new-boot'):
            self.assertEqual(self.check({'at':999,'boot_id':'old-boot','components':{'lve':True}})['heartbeat'], 'previous_boot')


class FaultCycleTests(unittest.TestCase):
    def setUp(self):
        previous.EssentialCycleTests.setUp(self)
        self.profile['disk_io_observer'] = True
        (c.BASE/'profile.json').write_text(json.dumps(self.profile))

    def test_failed_lve_preserves_healthy_spool_and_recovery_changes_global_status(self):
        started = time.time()
        output = io.StringIO()
        with patch.object(c,'read_diskstats',return_value=DiskTests().parse(DISKS)), \
             patch.object(c,'query_pressure',side_effect=c.CommandFailure('command_failed','cloudlinux-statistics',40,exit_code=1)), \
             patch.object(c,'collect_kernel',return_value=None), contextlib.redirect_stdout(output):
            c.main()
        self.assertEqual(json.loads(output.getvalue())['status'], 'degraded')
        marker=json.loads((c.STATE/'collector-health.json').read_text())
        self.assertEqual(marker['status'], 'degraded')
        self.assertEqual(marker['components']['disk_io'], 'available')
        metrics=[json.loads(x) for x in (c.SPOOL/'metrics.jsonl').read_text().splitlines()]
        self.assertEqual(len([x for x in metrics if 'counter' in x]), 8)
        self.assertTrue(any(x['name']=='duplika_collector_heartbeat' for x in metrics))
        with patch.object(c.time,'time',return_value=started+302), patch.object(c,'query_pressure',return_value=None), \
             patch.object(c,'read_diskstats',return_value=DiskTests().parse(DISKS)), \
             patch.object(c,'collect_kernel',return_value=None), contextlib.redirect_stdout(io.StringIO()):
            c.main()
        self.assertEqual(json.loads((c.STATE/'collector-health.json').read_text())['status'], 'ok')


class Rc27ProofTests(previous.Rc26ProofTests):
    previous_version='4.0.0-rc27'


class EssentialPlanTests(unittest.TestCase):
    previous_version = '4.0.0-rc26'

    def setUp(self):
        previous.Rc26ProofTests.setUp(self)
        self.profile = dict(PROFILE, agent_version=self.previous_version)
        if self.previous_version == '4.0.0-rc27':
            self.profile['monitoring_policy'] = 'essential-v1'
        (i.CLOUD/'profile.json').write_text(json.dumps(self.profile))
        self.cfg = config.make_config(self.profile, 'TEST_SECRET', self.meta['target']['ingesting_host'])
        (i.CLOUD/'vector.json').write_text(json.dumps(self.cfg))
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'],self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        self.stack.enter_context(patch.object(identity,'status'))
        self.stack.enter_context(patch.object(e,'storage_coverage',return_value=2))
        self.stack.enter_context(patch.object(e,'disk_coverage',return_value={'devices':2,'counter_samples':8}))
        self.stack.enter_context(patch.object(c,'query_pressure',return_value=None))
        self.stack.enter_context(patch.object(i.context_cleanup,'validate'))
        self.stack.enter_context(patch.object(i,'run',side_effect=lambda args,**kw: subprocess.CompletedProcess(
            args,3 if args[:2]==['systemctl','is-active'] else 0,'inactive','')))

    def test_actual_plan_accepts_frozen_runtime_but_changes_only_reviewed_graph(self):
        old, profile, proposed, proof = e.plan(i,self.meta['target'],{})
        self.assertEqual(old['agent_version'],self.previous_version)
        self.assertEqual(proposed['sources']['host_slow']['collectors'],['network'])
        self.assertEqual(proposed['sinks'],config.essential_config(self.cfg)['sinks'])
        self.assertEqual(proof['disk_io_counter_samples_validated'],8)
        self.assertEqual(json.loads((i.CLOUD/'profile.json').read_text()),self.profile)

    def test_second_sender_or_unexpected_policy_blocks_before_mutation(self):
        with patch.object(i,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            with self.assertRaisesRegex(RuntimeError,'second_transport_active'):e.plan(i,self.meta['target'],{})
        self.profile['monitoring_policy']='unexpected'
        (i.CLOUD/'profile.json').write_text(json.dumps(self.profile))
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(RuntimeError,'unexpected_existing_policy'):e.plan(i,self.meta['target'],{})

    def test_failed_first_disk_cycle_restores_configuration_and_keeps_advanced_cursor(self):
        files=[i.CLOUD/'profile.json',i.CLOUD/'collector.py',i.CLOUD/'vector.json',i.BASE/'manifest.json',i.BASE/'complete']
        originals={p:p.read_bytes() for p in files}
        cursor=i.CLOUD_DATA/'private/checkpoint';cursor.write_text('before')
        def execute(args,**kw):
            if args==['systemctl','start','duplika-cloudlinux-observer.service']:
                cursor.write_text('advanced')
                (i.CLOUD_DATA/'private/collector-health.json').write_text(json.dumps({
                    'at':time.time(),'started_at':time.time(),'agent_version':i.VERSION,
                    'monitoring_policy':'essential-v1','components':{'lve':'available','processes':'disabled','access':'disabled','disk_io':'failed'}}))
        with patch.object(i,'execute',side_effect=execute), patch.object(i,'observer_quiet_point',side_effect=contextlib.nullcontext), \
             patch.object(i,'wait_observer_inactive'),patch.object(i,'activate') as activate:
            with self.assertRaisesRegex(RuntimeError,'new_observer_cycle_not_verified'):
                e.migrate(i,self.meta['target'],{},apply=True)
        activate.assert_not_called()
        for path,data in originals.items():self.assertEqual(path.read_bytes(),data)
        self.assertEqual(cursor.read_text(),'advanced')


class Rc27EssentialPlanTests(EssentialPlanTests):
    previous_version='4.0.0-rc27'


class NativeProbeTests(unittest.TestCase):
    def test_native_disk_proof_requires_real_counter_samples_and_no_writes(self):
        result = subprocess.CompletedProcess([],0,'{"devices":2,"counter_samples":8}', '')
        with patch.object(i,'run',return_value=result) as run:
            self.assertEqual(e.disk_coverage(i), {'devices':2,'counter_samples':8})
        args=run.call_args.args[0]
        self.assertIn('NoNewPrivileges=true',args)
        self.assertNotIn('ReadWritePaths=', ' '.join(args))
        # Execute the actual supplied driver against the bundled source locally.
        probe_args = args[args.index('-I')+1:]
        response=subprocess.run([i.sys.executable,'-I',*probe_args],capture_output=True,text=True,check=True)
        self.assertGreater(json.loads(response.stdout)['counter_samples'],0)
        for stdout in ('{}','{"devices":0,"counter_samples":0}','{"devices":2,"counter_samples":7}'):
            with patch.object(i,'run',return_value=subprocess.CompletedProcess([],0,stdout,'')), self.assertRaises(RuntimeError):
                e.disk_coverage(i)
