"""Explicit host sampling change that preserves the reviewed WordPress readers."""
import copy
import json
import math
import os
from pathlib import Path
import pwd
import tempfile


MARKER = 'legacy_host_sampling_v30'
POLICY = 'essential-host-v1'
MEMORY = ['memory_total_bytes', 'memory_available_bytes', 'memory_swap_total_bytes',
          'memory_swap_used_bytes', 'memory_oom_kill_events_total']
DISK = {'disk_read_bytes_total', 'disk_reads_completed_total',
        'disk_written_bytes_total', 'disk_writes_completed_total'}
SAMPLING = {'host_seconds': 60, 'filesystem_seconds': 60, 'disk_io_seconds': 300,
            'network_seconds': 300, 'wordpress_errors': 'incremental',
            'wordpress_active_probes': False, 'storage_observer': False}


def prepare(original, marker=MARKER):
    if marker != MARKER:
        raise RuntimeError('legacy_essential:unknown_review_marker')
    cfg = copy.deepcopy(original)
    if ('duplika_host_slow' in cfg['sources'] or 'legacy_host_essential' in cfg['transforms']
            or cfg['transforms']['host_to_json']['inputs'] != ['duplika_host']
            or cfg['sinks']['duplika_metrics']['inputs'] != ['host_for_betterstack']):
        raise RuntimeError('legacy_essential:unexpected_graph')
    # Keep the filesystem collector: these layouts have no root storage probe.
    cfg['sources']['duplika_host']['scrape_interval_secs'] = 60
    cfg['sources']['duplika_host']['collectors'] = ['cpu', 'filesystem', 'load', 'host', 'memory']
    cfg['sources']['duplika_host_slow'] = {'type': 'host_metrics',
        'scrape_interval_secs': 300, 'collectors': ['disk', 'network']}
    cfg['transforms']['host_to_json']['inputs'].append('duplika_host_slow')
    cfg['transforms']['legacy_host_essential'] = {'type': 'filter',
        'inputs': ['host_for_betterstack'], 'condition':
        '!starts_with(string!(.name), "memory_") || includes(' + json.dumps(MEMORY) + ', .name)'}
    cfg['sinks']['duplika_metrics']['inputs'] = ['legacy_host_essential']
    cfg.setdefault('tests', []).extend([
        {'name': 'legacy essential retains useful memory and disk counters', 'inputs': [{
            'insert_at': 'legacy_host_essential', 'type': 'log', 'log_fields': {
                'name': 'disk_read_bytes_total', 'counter': {'value': 1024}}}],
         'outputs': [{'extract_from': 'legacy_host_essential', 'conditions': [{
             'type': 'vrl', 'source': 'assert_eq!(.name, "disk_read_bytes_total")\nassert_eq!(.counter.value, 1024)'}]}]},
        {'name': 'legacy essential removes unused memory series', 'inputs': [{
            'insert_at': 'legacy_host_essential', 'type': 'log', 'log_fields': {
                'name': 'memory_cached_bytes', 'gauge': {'value': 1}}}],
         'no_outputs_from': ['legacy_host_essential']},
    ])
    return cfg


def coverage(rows):
    devices = {}
    mounts = set()
    network = set()
    for line in rows.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            raise RuntimeError('legacy_essential:probe_invalid_json') from None
        if not isinstance(row, dict) or not isinstance(row.get('tags') or {}, dict):
            raise RuntimeError('legacy_essential:probe_invalid_schema')
        name, tags = row.get('name'), row.get('tags') or {}
        counter = row.get('counter') or {}
        gauge = row.get('gauge') or {}
        if not isinstance(counter, dict) or not isinstance(gauge, dict):
            raise RuntimeError('legacy_essential:probe_invalid_schema')
        value = counter.get('value')
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
        if name in DISK and isinstance(tags.get('device'), str) and tags['device'] and valid:
            devices.setdefault(tags['device'], set()).add(name)
        total = gauge.get('value')
        if (name == 'filesystem_total_bytes' and isinstance(tags.get('mountpoint'), str)
                and tags['mountpoint'] and not isinstance(total, bool) and isinstance(total, (int, float))
                and math.isfinite(total) and total > 0):
            mounts.add(tags['mountpoint'])
        if name in ('network_receive_bytes_total', 'network_transmit_bytes_total') and valid:
            network.add(name)
    if not devices or any(names != DISK for names in devices.values()) or not mounts or len(network) != 2:
        raise RuntimeError('legacy_essential:probe_incomplete')
    return {'disk_io_devices_validated': len(devices), 'disk_io_counter_samples_validated': 4 * len(devices),
            'filesystem_mountpoints_validated': len(mounts), 'network_counters_validated': len(network)}


def probe_config(original, data_dir):
    source = {'type': 'host_metrics', 'scrape_interval_secs': 60,
              'collectors': ['disk', 'filesystem', 'network'],
              'filesystem': copy.deepcopy(original['sources']['duplika_host']['filesystem'])}
    return {'data_dir': str(data_dir), 'sources': {'probe': source},
            'transforms': {'as_json': {'type': 'metric_to_log', 'inputs': ['probe']}},
            'sinks': {'local': {'type': 'console', 'inputs': ['as_json'], 'target': 'stdout',
                                'encoding': {'codec': 'json'}}}}


def native_probe(api, old, original):
    # No source credential, site reader or HTTP sink is present in this probe.
    reader = pwd.getpwnam('vector')
    props = api.execute(['systemctl', 'show', old['services'][0], '-p', 'User', '-p', 'Group']).stdout
    values = dict(line.split('=', 1) for line in props.splitlines() if '=' in line)
    if values.get('User') != 'vector' or values.get('Group') not in ('', 'vector'):
        raise RuntimeError('legacy_essential:transport_user_requires_review')
    timeout = api.trusted_executable('timeout')
    if not timeout:
        raise RuntimeError('legacy_essential:timeout_tool_missing')
    with tempfile.TemporaryDirectory(prefix='duplika-host-probe-', dir='/var/tmp') as folder:
        root = Path(folder)
        os.chown(root, 0, reader.pw_gid); root.chmod(0o750)
        data = root/'data'; data.mkdir(); os.chown(data, reader.pw_uid, reader.pw_gid)
        cfg = probe_config(original, data)
        path = root/'probe.json'; path.write_text(json.dumps(cfg))
        os.chown(path, 0, reader.pw_gid); path.chmod(0o640)
        response = api.run(['runuser', '-u', 'vector', '--', timeout, '--kill-after=2s',
                            '--signal=INT', '5s', old['vector_binary'], '--config-json', str(path)], timeout=12)
        if response.returncode not in (0, 124):
            raise RuntimeError('legacy_essential:probe_failed:exit=' + str(response.returncode))
        return coverage(response.stdout)


def plan(api, target, caps):
    import legacy_upgrade
    old = api.validate_v4_upgrade(target, caps)
    if caps.get('os_id') != 'almalinux' or old.get('pipeline_variant') not in legacy_upgrade.LAYOUTS:
        raise RuntimeError('legacy_essential:requires_reviewed_almalinux_layout')
    if old.get('migration_review', {}).get('essential_host_sampling'):
        raise RuntimeError('legacy_essential:already_selected_requires_review')
    api.identity.status(quiet=True)
    transport = old['services'][0]
    for other in ('vector.service', 'duplika-betterstack.service', 'duplika-betterstack-cloudlinux.service'):
        if other != transport and api.run(['systemctl', 'is-active', '--quiet', other]).returncode == 0:
            raise RuntimeError('legacy_essential:second_transport_active')
    original = api.load(old['config_path'])
    proposed = prepare(original)
    api.context_cleanup.validate(proposed, old['vector_binary'], api.execute)
    proof = native_probe(api, old, original)
    return old, proposed, dict(proof, policy=POLICY, wordpress_errors='preserved',
                               probe_context='transport_user', remote_acceptance='external_evidence_required')


def migrate(api, target, caps, apply=False):
    import legacy_upgrade
    current = api.load(api.BASE/'manifest.json')
    if current.get('agent_version') == api.VERSION:
        api.validate_current(target, caps)
        legacy_upgrade.validate(api, current, target)
        if current.get('migration_review', {}).get('essential_host_sampling') != MARKER:
            raise RuntimeError('legacy_essential:policy_not_selected')
        print(json.dumps({'policy': POLICY, 'already_installed': True, 'changes_applied': False}))
        return
    old, proposed, proof = plan(api, target, caps)
    if not apply:
        print(json.dumps(dict(proof, changes_applied=False)))
        return
    path = Path(old['config_path'])
    paths = [*api.runtime_files(), path, api.BASE/'manifest.json', api.BASE/'complete',
             api.UNITS/(api.NAME+'.service'), api.UNITS/(api.NAME+'.timer')]
    transport = old['services'][0]
    transaction = api.backup(paths, [api.NAME+'.timer', api.NAME+'.service', transport])
    try:
        api.execute(['systemctl', 'stop', api.NAME+'.timer', api.NAME+'.service'])
        if api.identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
            raise RuntimeError('legacy_essential:changed_during_upgrade')
        # Drain the existing emitter before the same unit reads its new graph.
        api.execute(['systemctl', 'stop', transport])
        api.write(path, api.identity.canonical(proposed), path.stat().st_mode & 0o777)
        if api.trusted_executable('restorecon'):
            api.execute(['restorecon', '-F', str(path)])
        api.execute(['runuser', '-u', 'vector', '--', old['vector_binary'], 'validate',
                     '--no-environment', '--config-json', str(path)])
        api.execute(['systemctl', 'start', transport])
        original = [Path(p) for p in old['critical_paths'] if p not in set(map(str, paths))]
        review = dict(old.get('migration_review') or {}, essential_host_sampling=MARKER)
        api.activate(target, caps, path, Path(old['profile_path']),
                     [s for s in old['services'] if s != api.NAME+'.timer'], old['oneshots'],
                     original, old['vector_binary'], old['pipeline_variant'], review)
    except BaseException as error:
        api.recover_failure(transaction, error)
        raise
