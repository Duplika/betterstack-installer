"""Explicit common base, preserving transport identity and optional capabilities."""
import copy
import json
import time
from pathlib import Path

MARKER = 'essential_base_v36'
TEMPLATE = 'essential-monitoring'
POLICY = 'essential-base-v1'


def cpu_graph(original, host_input, sink):
    """Sum absolute per-core counters within exactly one scrape and mode.

    Use a separate metric name and a core-count label: old per-core series never
    collide with new totals, and a core-count change starts a fresh rate series.
    No rolling sums, rounding timestamps, or averaging counters across scrapes.
    """
    cfg = copy.deepcopy(original)
    if 'base_cpu_route' in cfg['transforms'] or host_input not in cfg['sinks'][sink]['inputs']:
        raise RuntimeError('base:unexpected_cpu_graph')
    cfg['transforms'].update({
        'base_cpu_route': {'type': 'route', 'inputs': [host_input],
            'route': {'cpu': '.name == "cpu_seconds_total"'}},
        'base_cpu_prepare': {'type': 'remap', 'inputs': ['base_cpu_route.cpu'],
            'source': '.cpu_mode = string!(.tags.mode)\n.cpu_value = float!(.counter.value)\n.cpu_count = 1\ndel(.counter)\ndel(.tags.cpu)'},
        'base_cpu_reduce': {'type': 'reduce', 'inputs': ['base_cpu_prepare'],
            'group_by': ['dt', 'tags.host', 'cpu_mode'],
            'expire_after_ms': 2000, 'flush_period_ms': 500,
            'merge_strategies': {'dt': 'discard', 'tags': 'discard',
                'cpu_value': 'sum', 'cpu_count': 'sum', 'cpu_mode': 'discard'}},
        'base_cpu_finish': {'type': 'remap', 'inputs': ['base_cpu_reduce'],
            'source': '.name = "duplika_cpu_seconds_total"\n.counter.value = del(.cpu_value)\n.tags.logical_cpus = to_string!(del(.cpu_count))\n.tags.mode = del(.cpu_mode)\n.tags.aggregation = "host_sum"'},
    })
    inputs = cfg['sinks'][sink]['inputs']
    cfg['sinks'][sink]['inputs'] = [x for x in inputs if x != host_input] + [
        'base_cpu_route._unmatched', 'base_cpu_finish']
    return cfg


def contract(observer, caps):
    return {
        'host_metrics': True, 'cpu_aggregation': 'host_sum_by_mode',
        'filesystem': 'root_observer' if observer else 'vector_host_metrics',
        'lve_history': bool(observer and caps.get('lve_status') == 'available'),
        'storage_observer': observer,
        'raid': 'when_present' if observer else 'not_collected',
        'smart': 'when_supported' if observer else 'not_collected',
        'account_diagnostics': observer,
        'application_logs': 'selected_global_errors' if observer else 'disabled',
    }


def fresh_preflight(api, target, caps):
    import collector
    import essential_upgrade
    api.base_preflight(target, essential_base=True)
    disk = essential_upgrade.disk_coverage(api)
    volumes = essential_upgrade.storage_coverage(api, require_healthy=False)
    pressure = collector.query_pressure(dict(capabilities=caps), time.time())
    print(json.dumps({'policy': POLICY, 'volumes_validated': volumes,
        'disk_io_devices_validated': disk['devices'],
        'pressure_status': 'not_supported' if pressure is None else 'available',
        'changes_applied': False}))


def plan(api, target, caps):
    import config
    import minimal_host
    import essential_upgrade
    old = api.validate_v4_upgrade(target, caps)
    api.identity.status(quiet=True)
    observer = old['pipeline_variant'] == 'observer-v4'
    if observer:
        if old['agent_version'] != '4.0.0-rc34' or not old.get('storage_observer'):
            raise RuntimeError('base:requires_rc34_essential_observer')
        profile = api.load(old['profile_path'])
        if profile.get('monitoring_policy') != 'essential-v1' or not profile.get('disk_io_observer'):
            raise RuntimeError('base:requires_essential_profile')
        original = api.load(old['config_path'])
        _, token = api.identity.destination(original, target)
        expected = config.make_config(profile, token, target['ingesting_host'])
        for graph in (original, expected):
            graph.pop('tests', None)
        if original != expected:
            raise RuntimeError('base:transport_differs_from_reviewed_graph')
        minimal_host.ensure_single_transport(api, old)
        essential_upgrade.storage_coverage(api)
        essential_upgrade.disk_coverage(api)
        proposed = cpu_graph(api.load(old['config_path']), 'host_essential', 'metrics')
    else:
        if old['agent_version'] != '4.0.0-rc35' or old.get('migration_review', {}).get('minimal_host') != minimal_host.MARKER:
            raise RuntimeError('base:requires_rc35_minimal_legacy')
        minimal_host.ensure_single_transport(api, old)
        minimal_host.ensure_retired(api, old)
        original = api.load(old['config_path'])
        proposed = cpu_graph(original, 'legacy_host_essential', 'duplika_metrics')
    api.context_cleanup.validate(proposed, old['vector_binary'], api.execute)
    return old, proposed


def migrate(api, target, caps, apply=False):
    current = api.load(api.BASE/'manifest.json')
    if current.get('agent_version') == api.VERSION:
        api.validate_current(target, caps)
        if current.get('migration_review', {}).get('base_monitoring') != MARKER:
            raise RuntimeError('base:policy_not_selected')
        import minimal_host
        minimal_host.ensure_single_transport(api, current)
        if current['pipeline_variant'] != 'observer-v4':
            minimal_host.ensure_retired(api, current)
        print(json.dumps({'policy': POLICY, 'already_installed': True, 'changes_applied': False}))
        return
    old, proposed = plan(api, target, caps)
    proof = {'policy': POLICY, 'cpu_aggregation': 'host_sum_by_mode',
             'monitoring_capabilities': contract(old['pipeline_variant']=='observer-v4', caps)}
    if not apply:
        print(json.dumps(dict(proof, changes_applied=False)))
        return
    observer = old['pipeline_variant'] == 'observer-v4'
    config_path = Path(old['config_path'])
    paths = [*api.runtime_files(), config_path, api.BASE/'manifest.json', api.BASE/'complete',
        api.UNITS/(api.NAME+'.service'), api.UNITS/(api.NAME+'.timer')]
    units = [api.NAME+'.timer', api.NAME+'.service', old['services'][0]]
    if observer:
        paths += [api.CLOUD/'collector.py', api.CLOUD/'profile.json']
        units += ['duplika-cloudlinux-observer.timer']
    transaction = api.backup(paths, units)
    try:
        api.execute(['systemctl', 'stop', *units])
        if observer:
            api.wait_observer_inactive()
        with api.observer_quiet_point() if observer else api.contextlib.nullcontext():
            if api.identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
                raise RuntimeError('base:changed_during_upgrade')
            api.write(config_path, api.identity.canonical(proposed), config_path.stat().st_mode & 0o777)
            if observer:
                profile = api.load(old['profile_path'])
                profile.update(agent_version=api.VERSION, base_monitoring=True)
                api.write(api.CLOUD/'profile.json', api.identity.canonical(profile))
                api.write(api.CLOUD/'collector.py', (api.HERE/'collector.py').read_bytes(), 0o700)
        if api.trusted_executable('restorecon'):
            api.execute(['restorecon', '-F', *map(str, paths)])
        reader = 'duplikaobs' if observer else 'vector'
        api.execute(['runuser','-u',reader,'--',old['vector_binary'],'validate',
            '--no-environment','--config-json',str(config_path)])
        if observer:
            first_cycle = time.time()
            api.execute(['systemctl','start','duplika-cloudlinux-observer.service'], timeout=60)
            api.verify_new_observer_cycle(first_cycle)
        api.execute(['systemctl','start',old['services'][0]])
        if observer:
            api.execute(['systemctl','start','duplika-cloudlinux-observer.timer'])
        review = dict(old.get('migration_review') or {}, base_monitoring=MARKER)
        original = [Path(p) for p in old['critical_paths'] if p not in set(map(str, paths))]
        api.activate(target, caps, config_path, Path(old['profile_path']),
            [s for s in old['services'] if s not in (api.NAME+'.timer','duplika-storage-observer.timer')],
            [s for s in old['oneshots'] if s != 'duplika-storage-observer.service'],
            original + ([api.CLOUD/'collector.py',api.CLOUD/'profile.json'] if observer else []),
            old['vector_binary'],old['pipeline_variant'],review,
            storage_enabled=observer,storage_preserved=observer)
    except BaseException as error:
        api.recover_failure(transaction, error)
        raise
    print(json.dumps(dict(proof, changes_applied=True)))
