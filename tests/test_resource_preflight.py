"""Bounded resource probe before any installation marker or package mutation."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import installer as i

CAPS = {'lve_status': 'available', 'cloudlinux_top': '/usr/sbin/cloudlinux-top',
        'isolates_supported': True, 'os_id': 'cloudlinux', 'os_version': '8.10'}

class ResourcePreflightTests(unittest.TestCase):
    def test_transient_sandbox_matches_collector_and_never_exposes_users(self):
        response = subprocess.CompletedProcess([], 0, 'duplika_preflight={"status":"ok","accounts_observed":2,"duration_seconds":25}\n', '')
        with patch.object(i, 'trusted_executable', side_effect=lambda x:'/usr/sbin/'+x), patch.object(i, 'run', return_value=response) as run, contextlib.redirect_stdout(io.StringIO()) as output:
            i.resource_preflight(CAPS)
        args = run.call_args.args[0]
        for value in ('--wait','--pipe','--collect','CPUQuota=10%','MemoryMax=256M','ProtectHome=true','ProtectSystem=strict','PrivateTmp=true','NoNewPrivileges=true','IOSchedulingClass=idle','TimeoutStartSec=50'):
            self.assertIn(value, args)
        self.assertEqual(args[-1], 'domains')
        self.assertEqual(run.call_args.kwargs['timeout'], 55)
        self.assertEqual(json.loads(output.getvalue())['preflight_resources']['accounts_observed'], 2)

    def test_unavailable_lve_skips_transient_unit(self):
        for value in ('not_supported','not_applicable'):
            with patch.object(i, 'run') as run:
                i.resource_preflight(dict(CAPS,lve_status=value))
            run.assert_not_called()

    def test_failure_timeout_missing_or_malformed_summary_are_closed(self):
        for response in (subprocess.CompletedProcess([],1,'','private data'),
                         subprocess.CompletedProcess([],0,'not json',''),
                         subprocess.CompletedProcess([],1,'duplika_preflight={"status":"failed","error":"lve_timeout"}\n',''),
                         subprocess.TimeoutExpired('systemd-run',55)):
            kwargs = {'side_effect':response} if isinstance(response,Exception) else {'return_value':response}
            with self.subTest(response=response), patch.object(i,'trusted_executable',side_effect=lambda x:'/usr/sbin/'+x), patch.object(i,'run',**kwargs), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(RuntimeError,'preflight:.*before_install') as error:i.resource_preflight(CAPS)
            self.assertNotIn('private data', str(error.exception)+output.getvalue())

    def test_probe_code_validates_lve_and_suppresses_account_payload(self):
        for data, code, status in [({'result':'success','users':[{'username':'private-account'}]},0,'ok'),
                                   ({'result':'failed','users':[]},0,'failed'),
                                   ({'result':'success','users':{}},0,'failed'),
                                   ({'result':'success','users':[]},1,'failed')]:
            with patch('sys.argv',['probe','/usr/sbin/cloudlinux-top','domains']), patch('subprocess.run',return_value=subprocess.CompletedProcess([],code,json.dumps(data),'')) as run, contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):exec(i.RESOURCE_PROBE, {})
            result=json.loads(output.getvalue().split('=',1)[1]);self.assertEqual(result['status'],status)
            self.assertNotIn('private-account',output.getvalue())
            self.assertEqual(run.call_args.kwargs['timeout'],40)
            self.assertIn('--domains',run.call_args.args[0])

    def test_fresh_probe_failure_precedes_install_marker(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            caps=dict(CAPS)
            target={'source_id':123,'ingesting_host':'s123.example.net'}
            with patch.object(i,'BASE',root/'base'),patch.object(i,'STATE',root/'state'),patch.object(i,'LEGACY',root/'legacy'),patch.object(i,'load',return_value={'source_id':123,'host':'s123.example.net','token':'TEST_SECRET'}),patch.object(i,'resource_preflight',side_effect=RuntimeError('preflight:failed_before_install')),patch.object(i,'write') as write:
                with self.assertRaisesRegex(RuntimeError,'before_install'):i.fresh(target,caps,root/'credential.json')
            write.assert_not_called()

import test_upgrade_rc15

class Rc19UpgradeTests(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc19'
