"""Original rc21 transport verification and explicit date-safe graph migration."""
import base64
import copy
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

import error_aggregation as a
import identity
import installer as i
import legacy_upgrade
from test_release18 import MinimalLegacyTests
from test_upgrade_rc15 import TARGET, UpgradeTests


class Rc21ObserverUpgradeTests(UpgradeTests):
    previous_version = '4.0.0-rc21'


class Rc21AggregationTests(MinimalLegacyTests):
    def setUp(self):
        super().setUp()
        script = (Path(__file__).parents[1] / 'releases/4.0.0-rc21/installer.sh').read_text()
        encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive}
        self.original_module = {}
        exec(compile(files['error_aggregation.py'], 'released-rc21-aggregation.py', 'exec'), self.original_module)
        self.path = Path(self.meta['config_path'])
        self.plain = json.loads(self.path.read_text())
        self.old_config = self.original_module['prepare'](self.plain)
        self.path.write_text(json.dumps(self.old_config))
        self.meta.update(agent_version='4.0.0-rc21', installer_version='4.0.0-rc21',
                         migration_review={'error_aggregation': a.LEGACY_MARKER})
        for path in i.runtime_files():
            path.write_bytes(files[path.name])
        proof = json.loads((i.HERE / 'upgrade_rc21.json').read_text())
        (i.UNITS / (i.NAME + '.service')).write_text(
            proof['identity_service_template'].replace('__PYTHON__', i.sys.executable))
        (i.BASE / 'complete').write_text('4.0.0-rc21\n')
        self.save_baseline()

    def save_baseline(self):
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], str(self.path))
        (i.BASE / 'manifest.json').write_text(json.dumps(self.meta))

    def test_original_release_payload_and_frozen_graph_validate(self):
        self.assertEqual(a.prepare(self.plain, a.LEGACY_MARKER), self.old_config)
        self.assertEqual(i.validate_v4_upgrade(TARGET, {}), self.meta)

    def test_explicit_upgrade_changes_only_reviewed_graph_and_keeps_cursor(self):
        self.path.chmod(0o640)
        self.save_baseline()
        before_stat = self.path.stat()
        preserved = {p: p.read_bytes() for p in self.originals if p != self.path}
        cursor = i.CLOUD_DATA / 'private/checkpoint'
        cursor.write_text('current offset preserved')
        with patch.object(identity, 'status'), patch.object(i, 'execute') as execute, \
                patch.object(i.context_cleanup, 'validate'), patch.object(i, 'backup', return_value={}), \
                patch.object(i, 'activate') as activate:
            legacy_upgrade.upgrade(i, TARGET, {}, aggregate_errors=True)
        after = json.loads(self.path.read_text())
        self.assertEqual(after, a.prepare(self.plain))
        self.assertEqual(after['sources'], self.old_config['sources'])
        self.assertEqual(after['sinks']['duplika_metrics'], self.old_config['sinks']['duplika_metrics'])
        self.assertEqual(after['sinks']['duplika_logs']['auth'], self.old_config['sinks']['duplika_logs']['auth'])
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})
        self.assertEqual(cursor.read_text(), 'current offset preserved')
        self.assertEqual((before_stat.st_uid, before_stat.st_gid, before_stat.st_mode),
                         (self.path.stat().st_uid, self.path.stat().st_gid, self.path.stat().st_mode))
        self.assertEqual(activate.call_args.args[9]['error_aggregation'], a.MARKER)
        self.assertIn(['systemctl', 'restart', 'vector.service'], [c.args[0] for c in execute.call_args_list])

    def test_without_option_keeps_existing_graph_and_review_marker(self):
        before = self.path.read_bytes()
        with patch.object(identity, 'status'), patch.object(i, 'execute') as execute, \
                patch.object(i, 'backup', return_value={}), patch.object(i, 'activate') as activate:
            legacy_upgrade.upgrade(i, TARGET, {})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(activate.call_args.args[9]['error_aggregation'], a.LEGACY_MARKER)
        self.assertNotIn(['systemctl', 'restart', 'vector.service'], [c.args[0] for c in execute.call_args_list])

    def test_changed_rc21_graph_is_rejected_even_with_rewritten_baseline(self):
        cfg = copy.deepcopy(self.old_config)
        cfg['transforms']['repeat_reduce']['group_by'].append('message')
        self.path.write_text(json.dumps(cfg))
        self.save_baseline()
        with self.assertRaisesRegex(RuntimeError, 'differs_from_blueprint'):
            i.validate_v4_upgrade(TARGET, {})
        with self.assertRaisesRegex(RuntimeError, 'differs_from_blueprint'):
            a.replace_v21(cfg)

    def test_new_marker_validates_and_is_idempotent(self):
        self.path.write_text(json.dumps(a.prepare(self.plain)))
        self.meta['migration_review']['error_aggregation'] = a.MARKER
        self.save_baseline()
        self.assertEqual(legacy_upgrade.validate(i, self.meta, TARGET), self.meta)
        with patch.object(i.context_cleanup, 'validate'):
            self.assertEqual(a.validate_plan(i, self.meta), json.loads(self.path.read_text()))

    def test_unknown_marker_rejected(self):
        self.meta['migration_review']['error_aggregation'] = 'unknown'
        with self.assertRaisesRegex(RuntimeError, 'unknown_review_marker'):
            legacy_upgrade.validate(i, self.meta, TARGET)

    def test_transport_failure_invokes_transaction_recovery(self):
        def execute(args):
            if args == ['systemctl', 'restart', 'vector.service']:
                raise RuntimeError('fixture_transport_failure')
        with patch.object(identity, 'status'), patch.object(i, 'execute', side_effect=execute), \
                patch.object(i.context_cleanup, 'validate'), patch.object(i, 'backup', return_value={'fixture': True}), \
                patch.object(i, 'recover_failure') as recover:
            with self.assertRaisesRegex(RuntimeError, 'fixture_transport_failure'):
                legacy_upgrade.upgrade(i, TARGET, {}, aggregate_errors=True)
        recover.assert_called_once()
        self.assertEqual(recover.call_args.args[0], {'fixture': True})
        self.assertEqual(str(recover.call_args.args[1]), 'fixture_transport_failure')

    def test_real_transaction_restores_graph_and_metadata_without_rewinding_cursor(self):
        self.path.chmod(0o640)
        self.save_baseline()
        preserved = {p: p.read_bytes() for p in [self.path, i.BASE / 'manifest.json', i.BASE / 'complete']}
        cursor = i.CLOUD_DATA / 'private/checkpoint'
        cursor.write_text('offset remains current')
        def execute(args):
            if args == ['systemctl', 'restart', 'vector.service']:
                raise RuntimeError('fixture_transport_failure')
        with patch.object(identity, 'status'), patch.object(i, 'execute', side_effect=execute), \
                patch.object(i.context_cleanup, 'validate'):
            with self.assertRaisesRegex(RuntimeError, 'fixture_transport_failure'):
                legacy_upgrade.upgrade(i, TARGET, {}, aggregate_errors=True)
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(cursor.read_text(), 'offset remains current')
