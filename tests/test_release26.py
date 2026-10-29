"""Verify actual packaged cycle markers, rather than fabricating version fields."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import installer as i

ROOT = Path(__file__).parents[1]


def builder_module():
    spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'scripts/build.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def unpack(script):
    encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
        return {m.name: archive.extractfile(m).read() for m in archive}


class PackagedCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('base', 'data/private', 'spool', 'domlogs'):
            (self.root / name).mkdir(parents=True)
        profile = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20',
                   'installation_id': 'synthetic', 'reader_gid': os.getgid(),
                   'domlog_roots': [str(self.root / 'domlogs')]}
        (self.root / 'base/profile.json').write_text(json.dumps(profile))

    def run_cycle(self, code):
        collector = types.ModuleType('packaged_collector')
        exec(compile(code, 'packaged-collector.py', 'exec'), collector.__dict__)
        started = time.time()
        with contextlib.ExitStack() as stack:
            for key, value in {'BASE': self.root / 'base', 'ROOT': self.root / 'data',
                               'STATE': self.root / 'data/private', 'SPOOL': self.root / 'spool'}.items():
                stack.enter_context(patch.object(collector, key, value))
            stack.enter_context(patch.object(collector, 'inventory', return_value=({}, set(), {})))
            for name, result in [('collect_processes', 0), ('collect_lve', True),
                                 ('collect_errors', {}), ('collect_kernel', 'empty'),
                                 ('collect_wordpress', 'disabled')]:
                stack.enter_context(patch.object(collector, name, return_value=result))
            # Execute real main, sampling, access reader, spool writes and atomic
            # health marker. Only external inventory/probes use synthetic data.
            with contextlib.redirect_stdout(io.StringIO()):
                collector.main()
        marker = json.loads((self.root / 'data/private/collector-health.json').read_text())
        self.assertGreaterEqual(marker['started_at'], started)
        self.assertEqual(marker['components']['access'].get('bytes_read', 0), 0)
        self.assertTrue((self.root / 'spool/metrics.jsonl').exists())
        return started, marker

    def test_current_distributed_collector_passes_actual_upgrade_verification(self):
        output = self.root / 'release'
        builder_module().build(output)
        files = unpack((output / 'installer.sh').read_text())
        started, marker = self.run_cycle(files['collector.py'])
        self.assertEqual(marker['agent_version'], i.VERSION)
        with patch.object(i, 'CLOUD_DATA', self.root / 'data'):
            i.verify_new_observer_cycle(started)

    def test_frozen_rc25_reproduces_wrong_cycle_version_rejection(self):
        files = unpack((ROOT / 'releases/4.0.0-rc25/installer.sh').read_text())
        started, marker = self.run_cycle(files['collector.py'])
        self.assertEqual(marker['agent_version'], '4.0.0-rc21')
        with patch.object(i, 'CLOUD_DATA', self.root / 'data'), patch.object(i, 'VERSION', '4.0.0-rc25'):
            with self.assertRaisesRegex(RuntimeError, 'new_observer_cycle_not_verified'):
                i.verify_new_observer_cycle(started)

    def test_builder_refuses_a_mismatched_or_missing_collector_version(self):
        root = self.root / 'sources'; (root / 'src/agent').mkdir(parents=True)
        (root / 'src/agent/capabilities.py').write_text("VERSION = '4.0.0-rc26'\n")
        builder = builder_module()
        for program in ("COLLECTOR_RELEASE = '4.0.0-rc21'\n", '# missing version\n'):
            (root / 'src/agent/collector.py').write_text(program)
            with patch.object(builder, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'Collector release must match installer version'):
                    builder.build(self.root / 'refused')
