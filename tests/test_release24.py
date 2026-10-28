"""Verify the released rc23 runtime/graph before replacing its event-time grouping."""
import base64
import io
import json
from pathlib import Path
import tarfile

import error_aggregation as a
import installer as i
import test_release22 as previous_tests


class Rc23ObserverUpgradeTests(previous_tests.UpgradeTests):
    previous_version = '4.0.0-rc23'


class Rc23AggregationMigrationTests(previous_tests.Rc21AggregationTests):
    installed_marker = a.RC23_MARKER

    def setUp(self):
        super().setUp()
        script = (Path(__file__).parents[1] / 'releases/4.0.0-rc23/installer.sh').read_text()
        encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive}
        self.original_module = {}
        exec(compile(files['error_aggregation.py'], 'released-rc23-aggregation.py', 'exec'), self.original_module)
        self.old_config = self.original_module['prepare'](self.plain)
        self.path.write_text(json.dumps(self.old_config))
        self.meta.update(agent_version='4.0.0-rc23', installer_version='4.0.0-rc23')
        for path in i.runtime_files():
            path.write_bytes(files[path.name])
        proof = json.loads((i.HERE / 'upgrade_rc23.json').read_text())
        (i.UNITS / (i.NAME + '.service')).write_text(
            proof['identity_service_template'].replace('__PYTHON__', i.sys.executable))
        (i.BASE / 'complete').write_text('4.0.0-rc23\n')
        self.save_baseline()
