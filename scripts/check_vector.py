#!/usr/bin/env python3
"""Native regression checks using a synthetic profile and no production data."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import context_cleanup
import error_aggregation
import threading
import copy


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
    aggregation = error_aggregation.prepare(config)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'aggregate.json'; path.write_text(json.dumps(aggregation))
        subprocess.run([args.vector, 'validate', '--no-environment', '--config-json', str(path)], check=True, timeout=120)
    check_aggregation(args.vector, aggregation)
    check_aggregation(args.vector, aggregation, native_dates=True)
    check_reader_aggregation(args.vector, aggregation)
    print('Synthetic context cleanup: ' + str(len(config['tests'])) + ' native Vector cases passed.')


def check_aggregation(vector, config, native_dates=False):
    # Run the actual route/remap/reduce graph. Only timers are shortened;
    # production five-minute settings are validated above with the full graph.
    transforms = {k:copy.deepcopy(v) for k,v in config['transforms'].items() if k.startswith('repeat_')}
    transforms['repeat_route']['inputs'] = ['input']
    if native_dates:
        transforms['native_dates'] = {'type': 'remap', 'inputs': ['input'],
                                      'source': '.dt = parse_timestamp(to_string(.dt) ?? "", format: "%+") ?? .dt'}
        transforms['repeat_route']['inputs'] = ['native_dates']
    transforms['repeat_reduce'].update(expire_after_ms=100, end_every_period_ms=100, flush_period_ms=20)
    graph = {'sources': {'input': {'type':'stdin','decoding':{'codec':'json'}}},
             'transforms':transforms,
             'sinks':{'output':{'type':'console','inputs':['repeat_dates','repeat_route._unmatched'],
                                'encoding':{'codec':'json'},'target':'stdout'}}}
    def row(kind, level='warning', site='example.net', at='2026-01-01T00:00:00Z'):
        return {'dt':at,'host':'cloud.example.net','service':'application',
                'site':site,'event_kind':kind,'level':level,'message':'PRIVATE_SQL_DIAGNOSTIC'}
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'config.json';path.write_text(json.dumps(graph))
        proc=subprocess.Popen([vector,'--config-json',str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        for event in [row('lsapi_abort', at='2026-01-01T00:00:02.123456Z'),
                      row('lsapi_abort', at='2025-12-31T21:00:01.123456-03:00'),
                      row('lsapi_abort', at='2026-01-01T00:00:03.123456Z')]+[row('sql_commands_out_of_sync')]*2+[row('lsapi_abort',site='other.example.net'),row('native_crash','critical'),row('lsapi_abort','critical'),row('lsapi_abort',at='invalid timestamp'),row('sql_commands_out_of_sync',at=None)]:
            proc.stdin.write(json.dumps(event)+'\n')
        proc.stdin.flush()
        rows=[]; expired=threading.Event()
        def read_output():
            for line in proc.stdout:
                rows.append(json.loads(line))
                if len(rows) >= 7:expired.set()
        reader=threading.Thread(target=read_output,daemon=True);reader.start()
        try:
            assert expired.wait(10), 'Native timer did not flush while input remained open'
            proc.stdin.write(json.dumps(row('lsapi_abort'))+'\n');proc.stdin.flush()
            proc.stdin.close();proc.wait(timeout=15);reader.join(timeout=2)
            stderr=proc.stderr.read()
            if proc.returncode:raise RuntimeError('native aggregation failed: '+stderr[-2000:])
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
        summaries=[r for r in rows if 'occurrences' in r]
        assert sorted(r['occurrences'] for r in summaries)==[1,1,2,3], rows
        assert sum(r['occurrences'] for r in summaries)==7
        assert len([r for r in rows if r['level']=='critical'])==2
        assert all('PRIVATE_SQL_DIAGNOSTIC' not in json.dumps(r) for r in summaries)
        assert all(r['aggregation_seconds']==300 for r in summaries)
        for expected in (row('lsapi_abort', at='invalid timestamp'), row('sql_commands_out_of_sync', at=None)):
            assert any(all(actual.get(k) == v for k, v in expected.items())
                       and 'occurrences' not in actual for actual in rows), rows
        for row_ in summaries:
            assert row_['dt'] == row_['first_seen'], row_
            assert row_['first_seen'] <= row_['last_seen'], row_
            assert 'first_seen_us' not in row_ and 'last_seen_us' not in row_, row_
        combined = next(r for r in summaries if r['occurrences'] == 3)
        assert combined['first_seen'] == '2026-01-01T00:00:01.123456Z', combined
        assert combined['last_seen'] == '2026-01-01T00:00:03.123456Z', combined
        assert 'Failed to create value merger' not in stderr, stderr
    print('Native aggregation: exact event dates, out-of-order/offset input, counts, grouping, timer flush, critical bypass and payload minimization passed.')


def check_reader_aggregation(vector, config):
    # Exercise the real released reader normalization and filtering, which
    # produce VRL timestamps rather than JSON strings. No production logs.
    names = {'normalize_errors', 'useful_errors'}
    transforms = {k:copy.deepcopy(v) for k,v in config['transforms'].items()
                  if k.startswith('repeat_') or k in names}
    transforms['normalize_errors']['inputs'] = ['input']
    transforms['repeat_reduce'].update(expire_after_ms=100, end_every_period_ms=100, flush_period_ms=20)
    graph = {'sources': {'input': {'type': 'stdin', 'decoding': {'codec': 'json'}}},
             'transforms': transforms,
             'sinks': {'output': {'type': 'console', 'inputs': ['repeat_dates', 'repeat_route._unmatched'],
                                  'encoding': {'codec': 'json'}, 'target': 'stdout'}}}
    def ls(at, suffix=''):
        return {'host': 'cloud.example.net', 'service': 'litespeed',
                'message': at + ' [WARN] [APVH_example.invalid] Abort request processing by PID: 12345, kill: 1 ' + suffix}
    def sql(at):
        return {'host': 'cloud.example.net', 'service': 'wordpress', 'site': 'example.net',
                'message': '[' + at + ' UTC] WordPress database error Commands out of sync; PRIVATE_SQL_DIAGNOSTIC'}
    critical = ls('2026-01-01T00:00:05Z', 'SIGKILL')
    unknown = ls('unparsed time')
    events = [ls('2026-01-01T00:00:03.123456Z'), ls('2026-01-01T00:00:01.123456Z'),
              ls('2025-12-31T21:00:02.123456-03:00'), sql('01-Jan-2026 00:00:04'),
              sql('01-Jan-2026 00:00:02'), critical, unknown]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'reader.json'; path.write_text(json.dumps(graph))
        result = subprocess.run([vector, '--config-json', str(path)],
                                input=''.join(json.dumps(r) + '\n' for r in events),
                                capture_output=True, text=True, timeout=20, check=True)
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    summaries = {r['event_kind']: r for r in rows if 'occurrences' in r}
    assert set(summaries) == {'lsapi_abort', 'sql_commands_out_of_sync'}, rows
    assert summaries['lsapi_abort']['occurrences'] == 3, rows
    assert summaries['lsapi_abort']['first_seen'] == '2026-01-01T00:00:01.123456Z', rows
    assert summaries['lsapi_abort']['last_seen'] == '2026-01-01T00:00:03.123456Z', rows
    assert summaries['sql_commands_out_of_sync']['occurrences'] == 2, rows
    assert summaries['sql_commands_out_of_sync']['first_seen'] == '2026-01-01T00:00:02Z', rows
    assert summaries['sql_commands_out_of_sync']['last_seen'] == '2026-01-01T00:00:04Z', rows
    assert all(r['dt'] == r['first_seen'] for r in summaries.values()), rows
    assert all('PRIVATE_SQL_DIAGNOSTIC' not in json.dumps(r) for r in summaries.values()), rows
    assert any(r.get('level') == 'critical' and r['message'] == critical['message']
               and 'occurrences' not in r for r in rows), rows
    assert any(r.get('timestamp_unparsed') is True and r['message'] == unknown['message']
               and 'occurrences' not in r for r in rows), rows
    for warning in ('Mapping failed', 'Failed to create value merger', 'expected string', 'error[E'):
        assert warning not in result.stderr, result.stderr
    print('Native reader-to-summary: actual parser/filter, typed dates, exact counts/dates and unparsed/critical bypass passed.')


if __name__ == '__main__':
    main()
