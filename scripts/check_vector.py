#!/usr/bin/env python3
"""Native regression checks using a synthetic profile and no production data."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import context_cleanup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vector', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('context_reference', root / 'src/legacy/legacy_context_reference.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    profile = {'host': 'cloud.example.net', 'site': 'example.net', 'docroot': '/home/account/public_html',
               'since': '2026-01-01T00:00:00+00:00', 'context_since': '2026-01-01T00:00:00+00:00', 'interval': 60}
    config = context_cleanup.prepare(reference.make_config(profile, 'TEST_SECRET', 's1234568.us-west-2a.betterstackdata.com'))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'vector.json'; path.write_text(json.dumps(config))
        subprocess.run([args.vector, 'test', '--config-json', str(path)], check=True, timeout=120)
        subprocess.run([args.vector, 'validate', '--no-environment', '--config-json', str(path)], check=True, timeout=120)
    print('Synthetic context cleanup: ' + str(len(config['tests'])) + ' native Vector cases passed.')


if __name__ == '__main__':
    main()
