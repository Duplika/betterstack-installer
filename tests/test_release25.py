"""Verify rc24 upgrades preserve its already-correct event-time grouping."""
import base64
import copy
import io
import json
from pathlib import Path
import tarfile

import error_aggregation as a
import installer as i
from test_upgrade_rc15 import TARGET
import test_release22 as previous_tests


class Rc24ObserverUpgradeTests(previous_tests.UpgradeTests):
    previous_version = '4.0.0-rc24'


class Rc24AggregationMigrationTests(previous_tests.Rc21AggregationTests):
    installed_marker = a.MARKER

    def setUp(self):
        super().setUp()
        script = (Path(__file__).parents[1] / 'releases/4.0.0-rc24/installer.sh').read_text()
        encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive}
        self.original_module = {}
        exec(compile(files['error_aggregation.py'], 'released-rc24-aggregation.py', 'exec'), self.original_module)
        self.old_config = self.original_module['prepare'](self.plain)
        self.path.write_text(json.dumps(self.old_config))
        self.meta.update(agent_version='4.0.0-rc24', installer_version='4.0.0-rc24')
        for path in i.runtime_files():
            path.write_bytes(files[path.name])
        proof = json.loads((i.HERE / 'upgrade_rc24.json').read_text())
        (i.UNITS / (i.NAME + '.service')).write_text(
            proof['identity_service_template'].replace('__PYTHON__', i.sys.executable))
        (i.BASE / 'complete').write_text('4.0.0-rc24\n')
        self.save_baseline()

    def test_changed_rc21_graph_is_rejected_even_with_rewritten_baseline(self):
        cfg = copy.deepcopy(self.old_config)
        cfg['transforms']['repeat_reduce']['group_by'].append('message')
        self.path.write_text(json.dumps(cfg))
        self.save_baseline()
        with self.assertRaisesRegex(RuntimeError, 'differs_from_blueprint'):
            i.validate_v4_upgrade(TARGET, {})
        # The current graph is retained, not a legacy replacement input.
        with self.assertRaisesRegex(RuntimeError, 'unknown_review_marker'):
            a.replace_reviewed(cfg, self.installed_marker)
