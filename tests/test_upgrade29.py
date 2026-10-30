"""Exercise a queued frozen collector with a real flock, not a mocked lock."""
import json
import subprocess
import time
from unittest.mock import patch

import installer as i
import essential_upgrade as e
import identity
import config
from test_observer28 import EssentialPlanTests


class Rc28EssentialPlanTests(EssentialPlanTests):
    previous_version = '4.0.0-rc28'

    def setUp(self):
        super().setUp()
        self.profile.update(monitoring_policy='essential-v1', disk_io_observer=True,
                            disk_io_generation=1)
        (i.CLOUD/'profile.json').write_text(json.dumps(self.profile))
        self.cfg = config.make_config(self.profile, 'TEST_SECRET', self.meta['target']['ingesting_host'])
        (i.CLOUD/'vector.json').write_text(json.dumps(self.cfg))
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))


class QueuedCollectorTests(EssentialPlanTests):
    def setUp(self):
        super().setUp()
        (i.CLOUD_DATA/'private/collector.lock').touch()
        self.frozen = self.root/'frozen-collector.py'
        self.frozen.write_bytes((i.CLOUD/'collector.py').read_bytes())

    def old_cycle(self):
        # Stop immediately after acquiring the lock, before inventory or spool
        # work. Import and execute main() from the immutable old release.
        probe = '''import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('old_collector',sys.argv[1])
c=importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
c.BASE=pathlib.Path(sys.argv[2]); c.STATE=pathlib.Path(sys.argv[3])
original=c.read_json
class Acquired(Exception): pass
def read(path, fallback):
    if path.name == 'state.json': raise Acquired()
    return original(path, fallback)
c.read_json=read
try: c.main()
except Acquired: sys.exit(0)
except BlockingIOError: sys.exit(11)
sys.exit(2)
'''
        return subprocess.run([i.sys.executable, '-I', '-c', probe, str(self.frozen),
                               str(i.CLOUD), str(i.CLOUD_DATA/'private')],
                              capture_output=True, text=True, timeout=10).returncode

    def test_old_order_reproduces_eagain_in_the_frozen_collector(self):
        with i.observer_quiet_point():
            self.assertEqual(self.old_cycle(), 11)
        self.assertEqual(self.old_cycle(), 0)

    def test_queued_old_cycle_finishes_before_configuration_or_lock_changes(self):
        queued = False
        events = []
        original = (i.CLOUD/'profile.json').read_bytes()
        def run(args, **kwargs):
            nonlocal queued
            if args[:3] == ['systemctl', 'show', 'duplika-cloudlinux-observer.service']:
                if queued:
                    queued = False
                    self.assertEqual((i.CLOUD/'profile.json').read_bytes(), original)
                    code = self.old_cycle()
                    events.append(('old cycle', code))
                    return subprocess.CompletedProcess(args, 0, 'inactive' if code == 0 else 'failed', '')
            return subprocess.CompletedProcess(args, 3 if args[:2] == ['systemctl', 'is-active'] else 0, 'inactive', '')
        def execute(args, **kwargs):
            nonlocal queued
            if args[:2] == ['systemctl', 'stop'] and 'duplika-cloudlinux-observer.timer' in args:
                queued = True
            if args == ['systemctl', 'start', 'duplika-cloudlinux-observer.service']:
                events.append(('new cycle', 0))
                (i.CLOUD_DATA/'private/collector-health.json').write_text(json.dumps({
                    'at': time.time(), 'started_at': time.time(), 'agent_version': i.VERSION,
                    'monitoring_policy': 'essential-v1', 'components': {
                        'lve': 'available', 'processes': 'disabled', 'access': 'disabled', 'disk_io': 'available'}}))
        with patch.object(i, 'run', side_effect=run), patch.object(i, 'execute', side_effect=execute), \
             patch.object(i, 'activate') as activate:
            e.migrate(i, self.meta['target'], {}, apply=True)
        self.assertEqual(events, [('old cycle', 0), ('new cycle', 0)])
        activate.assert_called_once()

    def test_failed_old_job_does_not_replace_collector_or_profile(self):
        originals = {p: p.read_bytes() for p in (i.CLOUD/'collector.py', i.CLOUD/'profile.json', i.CLOUD/'vector.json')}
        with patch.object(e, 'plan', return_value=(self.meta, self.profile, self.cfg, {})), \
             patch.object(i, 'execute'), patch.object(i, 'wait_observer_inactive', side_effect=RuntimeError('upgrade:previous_observer_job_failed')), \
             patch.object(i, 'recover_failure') as recover:
            with self.assertRaisesRegex(RuntimeError, 'previous_observer_job_failed'):
                e.migrate(i, self.meta['target'], {}, apply=True)
        recover.assert_called_once()
        for path, before in originals.items():
            self.assertEqual(path.read_bytes(), before)
