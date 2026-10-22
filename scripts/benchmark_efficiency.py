#!/usr/bin/env python3
"""Synthetic policy comparison; not production usage or billed-byte prediction."""
import base64
import io
import json
from pathlib import Path
import tarfile
import types
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/agent'))
import collector


def reference():
    script = (ROOT / 'releases/4.0.0-rc15/installer.sh').read_text()
    encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
        source = archive.extractfile('collector.py').read()
    module = types.ModuleType('reviewed_rc15')
    exec(compile(source, 'reviewed_rc15', 'exec'), module.__dict__)
    return module


def simulate(module, sparse):
    state = {}; events = []; snapshots = 0
    profile = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20'}
    for minute in range(60):
        now = 100000 + minute * 60
        out = module.Output(profile, state, now=now)
        if not sparse or minute % 5 == 0:
            snapshots += 1
            for account in range(200):
                user = 'account' + str(account)
                out.log('account_resources', user=user, blocked_processes=0,
                        running_processes=minute % 3, rss_bytes=8000000, cpu_percent=3)
                out.log('lve_usage', user=user, usage={'cpu_percent': 85 if account == 0 and 10 <= minute < 30 else 1},
                        limits={'cpu_percent': 100}, mysql={'cpu_percent': 1}, mysql_governor='enabled')
        for site in range(400):
            out.log('site_traffic', site='s' + str(site) + '.example.net', user='account' + str(site // 2),
                    requests=10, errors_5xx=int(site == 0 and 10 <= minute < 20),
                    response_bytes=1000, interval_seconds=60, coverage_complete=True,
                    breakdown={'route:home': 10, 'status:200': 10})
        out.log('collector_heartbeat', accounts=200, domains=400,
                components={'lve': True, 'processes': 400 + minute, 'access': {'bytes_read': minute * 300}})
        out.log('web_error_summary', error_type='timeout', count=1, site='s0.example.net', level='warning')
        events += out.logs
        state = json.loads(json.dumps(state))
    return {'events': len(events), 'json_bytes': sum(len(json.dumps(row, separators=(',', ':'))) for row in events),
            'resource_snapshots': snapshots,
            'web_errors_preserved': sum(row['event_kind'] == 'web_error_summary' for row in events)}


if __name__ == '__main__':
    before, after = simulate(reference(), False), simulate(collector, True)
    assert before['web_errors_preserved'] == after['web_errors_preserved'] == 60
    assert after['resource_snapshots'] == before['resource_snapshots'] // 5
    print(json.dumps({'synthetic_minutes': 60, 'accounts': 200, 'sites': 400, 'rc15': before,
        'candidate': after, 'log_json_reduction_ratio': 1 - after['json_bytes'] / before['json_bytes'],
        'scope': 'Synthetic routine traffic with two incidents; no claim of production savings.'}, indent=2))
