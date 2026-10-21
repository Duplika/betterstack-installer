import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

import collector
import config
import identity
import installer as api
import partial_recovery as recovery

TARGET = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20', 'source_id': 1234568,
          'ingesting_host': 's1234568.us-west-2a.betterstackdata.com'}
CAPS = {'os_id': 'cloudlinux', 'os_version': '8.10', 'edition': 'CloudLinux OS Shared Pro',
        'lve_status': 'available', 'cloudlinux_top': '/usr/sbin/cloudlinux-top',
        'isolates_supported': True, 'mysql_governor': 'enabled'}


def response(text='', code=0):
    return subprocess.CompletedProcess([], code, text, '')


class PartialRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        stage = self.root / 'payload'; stage.mkdir()
        for name in ('collector.py', 'recovery_rc11.json'):
            (stage / name).write_bytes((api.HERE / name).read_bytes())
        self.stack.enter_context(patch.object(api, 'HERE', stage))
        for name in ('BASE', 'STATE', 'CLOUD', 'CLOUD_DATA', 'UNITS'):
            path = self.root / name; path.mkdir()
            self.stack.enter_context(patch.object(api, name, path))
        self.stack.enter_context(patch.object(api, 'LEGACY', self.root / 'absent-legacy'))
        self.domlogs = self.root / 'domlogs'; self.domlogs.mkdir()
        self.profile = dict(host=TARGET['host'], server_ip=TARGET['server_ip'], cloudlinux_version='8.10',
            installation_id='cloudlinux-' + '20261021T060000Z', domlog_roots=[str(self.domlogs)], error_logs=[],
            cloudlinux_top=CAPS['cloudlinux_top'], isolates_supported=True, mysql_governor='enabled',
            capabilities=CAPS, agent_version='4.0.0-rc11', wordpress=[], interval_seconds=60,
            account_count=119, reader_gid=0, vector_binary='/usr/bin/vector')
        self.stack.enter_context(patch.object(recovery.pwd, 'getpwnam',
            return_value=types.SimpleNamespace(pw_gid=0)))
        self.stack.enter_context(patch.object(api, 'trusted_executable',
            side_effect=lambda name: '/usr/bin/vector' if name == 'vector' else None))
        self.run = self.stack.enter_context(patch.object(api, 'run', side_effect=self.run_command))
        self.execute = self.stack.enter_context(patch.object(api, 'execute', side_effect=self.execute_command))
        self.finish = self.stack.enter_context(patch.object(api, 'finish_install', side_effect=self.finish_install))
        self.fail_native = False; self.fail_activation = False
        api.write(api.STATE / 'install-pending.json', identity.canonical({'phase': 'base_installing', 'target': TARGET}))
        api.write(api.STATE / 'last-error.json', identity.canonical({'stage': 'install', 'exit_code': 1,
            'diagnostic': 'No se pudo medir procesos/LVE bajo systemd.'}))
        api.write(api.CLOUD / 'profile.json', identity.canonical(self.profile))
        api.write(api.CLOUD / 'vector.json', identity.canonical(config.make_config(
            self.profile, 'TEST_SECRET', TARGET['ingesting_host'])), 0o640)
        script = (Path(__file__).parents[1] / 'releases/4.0.0-rc11/installer.sh').read_text()
        raw = base64.b64decode(script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0])
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            original = archive.extractfile('collector.py').read()
        api.write(api.CLOUD / 'collector.py', original, 0o700)
        reference = api.load(api.HERE / 'recovery_rc11.json')
        for name, template in reference['units'].items():
            api.write(api.UNITS / name, template.replace('__VECTOR__', '/usr/bin/vector')
                      .replace('__PYTHON__', sys.executable), 0o644)
        spool = api.CLOUD_DATA / 'spool'; spool.mkdir()
        private = api.CLOUD_DATA / 'private'; private.mkdir()
        api.write(private / 'state.json', b'{"access_files":{"example":{"offset":987654}}}')
        self.write_heartbeat(failed=True)

    def write_heartbeat(self, failed):
        rows = [{'dt': '2026-10-21T06:00:00Z', 'event_kind': 'collector_error',
                 'component': 'lve', 'error_class': 'TimeoutExpired'}] if failed else []
        rows.append({'dt': '2026-10-21T06:00:00Z', 'event_kind': 'collector_heartbeat',
            'host': TARGET['host'], 'server_ip': TARGET['server_ip'],
            'installation_id': self.profile['installation_id'], 'components': {
                'processes': 429, 'lve': 'failed' if failed else True, 'access': {'files': 560},
                'errors': None, 'kernel': None, 'wordpress': 'disabled'}})
        api.write(api.CLOUD_DATA / 'spool/events.jsonl', b'\n'.join(identity.canonical(r) for r in rows) + b'\n')

    def execute_command(self, args, **kwargs):
        if args[:2] == ['systemctl', 'show']:
            return response('FragmentPath=' + str(api.UNITS / args[2]) + '\nDropInPaths=\n')
        if args == ['systemctl', 'start', recovery.OBSERVER]:
            self.write_heartbeat(failed=self.fail_native)
        return response()

    def run_command(self, args, **kwargs):
        if args[:2] == ['systemctl', 'is-enabled']:
            return response('static\n' if args[-1] == recovery.OBSERVER else 'disabled\n',
                            0 if args[-1] == recovery.OBSERVER else 1)
        return response(code=3)

    def finish_install(self, target, caps):
        pending = api.load(api.STATE / 'install-pending.json')
        self.assertEqual(pending['phase'], 'base_complete')
        self.assertEqual(pending['baseline'], identity.snapshot(pending['paths'], str(api.CLOUD / 'vector.json')))
        if self.fail_activation:
            raise RuntimeError('identity_delivery_failed')
        api.write(api.STATE / 'install-pending.json', identity.canonical({'phase': 'complete', 'target': target}))

    def test_preflight_reads_only_and_checks_actual_unit_files(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        recovery.validate(api, TARGET, CAPS)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertTrue(all(call.args[0][:2] == ['systemctl', 'show'] for call in self.execute.call_args_list))

    def test_success_preserves_transport_credentials_and_offsets(self):
        paths = [api.CLOUD / 'vector.json', api.CLOUD_DATA / 'private/state.json',
                 api.UNITS / recovery.TRANSPORT, api.UNITS / recovery.TIMER]
        before = {p: p.read_bytes() for p in paths}
        recovery.recover(api, TARGET, CAPS)
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertEqual(api.load(api.CLOUD / 'profile.json')['agent_version'], api.VERSION)
        self.assertIn('TimeoutStartSec=55', (api.UNITS / recovery.OBSERVER).read_text())
        self.assertIn('CPUQuota=10%', (api.UNITS / recovery.OBSERVER).read_text())
        self.assertEqual(api.load(api.STATE / 'install-pending.json')['phase'], 'complete')
        commands = [call.args[0] for call in self.execute.call_args_list]
        self.assertFalse(any(args[0] in ('dnf', 'useradd') or 'vector.service' in args for args in commands))

    def test_changed_collector_or_unknown_transport_is_refused_before_mutation(self):
        for filename in ('collector.py', 'vector.json'):
            path = api.CLOUD / filename; original = path.read_bytes()
            if filename == 'vector.json':
                value = api.load(path); value['sources']['host']['scrape_interval_secs'] = 5
                path.write_bytes(identity.canonical(value))
            else:
                path.write_bytes(original + b'\n')
            with patch.object(api, 'backup') as backup:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    recovery.recover(api, TARGET, CAPS)
                backup.assert_not_called()
            path.write_bytes(original)

    def test_changed_target_or_different_failure_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'pending_target'):
            recovery.validate(api, dict(TARGET, source_id=1), CAPS)
        api.write(api.STATE / 'last-error.json', identity.canonical({'stage': 'install', 'exit_code': 1,
            'diagnostic': 'different error'}))
        with self.assertRaisesRegex(RuntimeError, 'unreviewed_failure'):
            recovery.validate(api, TARGET, CAPS)

    def test_unknown_dropin_or_active_unit_is_refused(self):
        with patch.object(api, 'unit_files', return_value=[api.UNITS / recovery.TRANSPORT, self.root / 'dropin']):
            with self.assertRaisesRegex(RuntimeError, 'unit_changed'):
                recovery.validate(api, TARGET, CAPS)
        self.run.side_effect = None; self.run.return_value = response()
        with self.assertRaisesRegex(RuntimeError, 'stopped_disabled'):
            recovery.validate(api, TARGET, CAPS)

    def test_completed_base_or_changed_mode_is_refused(self):
        (api.CLOUD / 'vector.json').chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'unexpected_permissions'):
            recovery.validate(api, TARGET, CAPS)
        api.write(api.CLOUD / 'complete', 'already complete')
        with self.assertRaisesRegex(RuntimeError, 'incomplete_rc11'):
            recovery.validate(api, TARGET, CAPS)

    def test_native_or_identity_failure_restores_original_files_and_stopped_services(self):
        for phase in ('native', 'identity'):
            self.fail_native = phase == 'native'; self.fail_activation = phase == 'identity'
            before = {p: p.read_bytes() for p in api.observer_base_paths() + [api.STATE / 'install-pending.json']}
            with self.assertRaisesRegex(RuntimeError, 'native_check_failed|identity_delivery_failed'):
                recovery.recover(api, TARGET, CAPS)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            self.assertFalse((api.CLOUD / 'complete').exists())
            commands = [call.args[0] for call in self.execute.call_args_list + self.run.call_args_list]
            self.assertIn(['systemctl', 'stop', recovery.TRANSPORT], commands)
            self.assertIn(['systemctl', 'disable', recovery.TIMER], commands)
            self.finish.reset_mock(); self.write_heartbeat(failed=True)


class LveTimeoutTests(unittest.TestCase):
    def test_lve_wait_is_bounded_at_forty_seconds(self):
        out = collector.Output({'host': TARGET['host'], 'server_ip': TARGET['server_ip']})
        with patch.object(collector, 'command', return_value='{"result":"success","users":[]}') as command:
            self.assertTrue(collector.collect_lve(out, {'cloudlinux_top': CAPS['cloudlinux_top'],
                'capabilities': CAPS, 'isolates_supported': True}))
        self.assertEqual(command.call_args.kwargs['timeout'], 40)
