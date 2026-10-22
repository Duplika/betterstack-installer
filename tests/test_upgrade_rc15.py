"""Validate actual rc15 payload bytes before the narrowly scoped rc16 update."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import config
import identity
import installer as i

TARGET = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20', 'source_id': 1234568,
          'ingesting_host': 's1234568.us-west-2a.betterstackdata.com'}


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        for name in ('base', 'cloud', 'units', 'state', 'data/private'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for key, folder in [('BASE', 'base'), ('CLOUD', 'cloud'), ('UNITS', 'units'),
                            ('STATE', 'state'), ('CLOUD_DATA', 'data')]:
            self.stack.enter_context(patch.object(i, key, self.root / folder))
        self.stack.enter_context(patch.object(identity, 'BASE', i.BASE))
        script = (Path(__file__).parents[1] / 'releases/4.0.0-rc15/installer.sh').read_text()
        encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive}
        for p in i.runtime_files(): p.write_bytes(files[p.name])
        (i.CLOUD / 'collector.py').write_bytes(files['collector.py'])
        (i.CLOUD / 'storage.py').write_bytes(files['storage.py'])
        (i.CLOUD / 'vector.json').write_text(json.dumps(config.make_config(TARGET, 'TEST_SECRET', TARGET['ingesting_host'])))
        (i.CLOUD / 'profile.json').write_text(json.dumps({'agent_version': '4.0.0-rc15'}))
        proof = json.loads((i.HERE / 'upgrade_rc15.json').read_text())
        (i.UNITS / (i.NAME + '.service')).write_text(proof['identity_service_template'].replace('__PYTHON__', i.sys.executable))
        timer = '[Unit]\nDescription=Duplika v4 hourly maintenance and version identity\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=1h\nRandomizedDelaySec=120\nAccuracySec=30s\n[Install]\nWantedBy=timers.target\n'
        (i.UNITS / (i.NAME + '.timer')).write_text(timer)
        for p in i.observer_base_paths():
            if not p.exists(): p.write_text('reviewed fixture service')
        (i.UNITS / 'duplika-storage-observer.service').write_text(i.STORAGE_SERVICE.replace('__PYTHON__', i.sys.executable))
        (i.UNITS / 'duplika-storage-observer.timer').write_text(i.STORAGE_TIMER)
        here = self.root / 'payload'; here.mkdir()
        for source in i.HERE.iterdir():
            if source.is_file(): (here / source.name).write_bytes(source.read_bytes())
        self.stack.enter_context(patch.object(i, 'HERE', here))
        self.vector = self.root / 'vector'; self.vector.write_text('package binary')
        paths = sorted(set(map(str, i.observer_base_paths() + i.runtime_files() +
            [i.UNITS / (i.NAME + '.service'), i.UNITS / (i.NAME + '.timer')] + i.storage_paths())))
        self.meta = {'agent_version': '4.0.0-rc15', 'installer_version': '4.0.0-rc15',
            'target': TARGET, 'capabilities': {}, 'configuration_schema': i.SCHEMA, 'pipeline_variant': 'observer-v4',
            'config_path': str(i.CLOUD / 'vector.json'), 'profile_path': str(i.CLOUD / 'profile.json'),
            'critical_paths': paths, 'services': ['duplika-betterstack-cloudlinux.service',
                'duplika-cloudlinux-observer.timer', 'duplika-storage-observer.timer', i.NAME + '.timer'],
            'oneshots': ['duplika-cloudlinux-observer.service', 'duplika-storage-observer.service'],
            'storage_observer': True, 'vector_binary': str(self.vector),
            'baseline': identity.snapshot(paths, str(i.CLOUD / 'vector.json'))}
        (i.BASE / 'manifest.json').write_text(json.dumps(self.meta)); (i.BASE / 'complete').write_text('4.0.0-rc15\n')
        self.stack.enter_context(patch.object(i, 'run', return_value=__import__('subprocess').CompletedProcess([], 0, 'inactive', '')))
        self.stack.enter_context(patch.object(i, 'trusted_executable', side_effect=lambda name: str(self.vector) if name == 'vector' else None))

    def test_original_payload_validates_and_storage_hashes_are_verified(self):
        self.assertEqual(i.validate_v4_upgrade(TARGET, {})['agent_version'], '4.0.0-rc15')
        (i.CLOUD / 'storage.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'unreviewed_v4_runtime'): i.validate_v4_upgrade(TARGET, {})

    def test_altered_configuration_and_wrong_target_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'target_or_capabilities_changed'):
            i.validate_v4_upgrade(dict(TARGET, source_id=1234569), {})
        (i.CLOUD / 'profile.json').write_text('{"agent_version":"changed"}')
        with self.assertRaisesRegex(RuntimeError, 'v4_drift'): i.validate_v4_upgrade(TARGET, {})

    def test_upgrade_preserves_transport_storage_and_checkpoint(self):
        checkpoint = i.CLOUD_DATA / 'private/checkpoint'; checkpoint.write_text('latest offset')
        preserved = {p: p.read_bytes() for p in i.storage_paths() + [i.CLOUD / 'vector.json']}
        calls = []
        def execute(args, **kwargs):
            calls.append(args)
            if args == ['systemctl', 'start', 'duplika-cloudlinux-observer.service']:
                (i.CLOUD_DATA / 'private/collector-health.json').write_text(json.dumps({'at': time.time(), 'started_at': time.time(), 'agent_version': i.VERSION, 'components': {'lve': True, 'processes': 1, 'access': {}}}))
        with patch.object(identity, 'status'), patch.object(i, 'execute', side_effect=execute), \
             patch.object(i, 'observer_quiet_point', side_effect=lambda: contextlib.nullcontext()), \
             patch.object(i, 'backup', return_value={}), patch.object(i, 'activate') as activate:
            i.upgrade_v4(TARGET, {})
        self.assertTrue(activate.call_args.kwargs['storage_enabled'])
        self.assertTrue(activate.call_args.kwargs['storage_preserved'])
        self.assertEqual(checkpoint.read_text(), 'latest offset')
        for p, before in preserved.items(): self.assertEqual(p.read_bytes(), before)
        self.assertFalse(any('duplika-betterstack-cloudlinux.service' in args or 'duplika-storage-observer.service' in args for args in calls))
        self.assertEqual((i.CLOUD / 'collector.py').read_bytes(), (i.HERE / 'collector.py').read_bytes())

    def test_failed_first_cycle_rolls_back_instead_of_accepting(self):
        def fail(args, **kwargs):
            if args == ['systemctl', 'start', 'duplika-cloudlinux-observer.service']: raise RuntimeError('probe failed')
        with patch.object(identity, 'status'), patch.object(i, 'execute', side_effect=fail), \
             patch.object(i, 'observer_quiet_point', side_effect=lambda: contextlib.nullcontext()), \
             patch.object(i, 'backup', return_value={'fixture': True}), patch.object(i, 'recover_failure') as recover, \
             patch.object(i, 'activate') as activate:
            with self.assertRaisesRegex(RuntimeError, 'probe failed'): i.upgrade_v4(TARGET, {})
        recover.assert_called_once(); activate.assert_not_called()
