"""Read-only review of the two inherited AlmaLinux transports; no pipeline rewrite."""
import json
import importlib.util
import error_aggregation
from pathlib import Path


LAYOUTS = {
    'almalinux-wordpress-v2': ('4.0.0-rc7', '/etc/duplika-betterstack/vector.yaml',
        '/etc/duplika-betterstack/profile.json', '/etc/duplika-betterstack/acl.py',
        'legacy_alma_profile.py', 'legacy_alma_acl.py',
        ['duplika-betterstack.service', 'duplika-betterstack-acl.timer']),
    'almalinux-wordpress-minimal-v4': ('4.0.0-rc10', '/etc/vector/vector.yaml',
        '/etc/vector/duplika-profile.json', '/usr/local/sbin/duplika-vector-acl.py',
        'legacy_context_reference.py', 'legacy_context_acl.py',
        ['vector.service', 'duplika-vector-acl.timer'])}

def validate(api, old, target):
    variant = old.get('pipeline_variant')
    known = LAYOUTS
    if variant not in known:
        raise RuntimeError('upgrade:unreviewed_legacy_variant')
    version, config, profile, helper, blueprint, reference, services = known[variant]
    minimal = old.get('migration_review', {}).get('minimal_host')
    if minimal is not None:
        import minimal_host
        if minimal != minimal_host.MARKER:
            raise RuntimeError('minimal_host:unknown_review_marker')
    active_services = services[:1] if minimal else services
    expected_oneshots = [] if minimal else [services[1][:-6] + '.service']
    reviewed_minimal = minimal and old['agent_version'] in ('4.0.0-rc37', '4.0.0-rc50', '4.0.0-rc51', '4.0.0-rc52')
    if ((not reviewed_minimal and old['agent_version'] not in (version, '4.0.0-rc18', '4.0.0-rc19', '4.0.0-rc20', '4.0.0-rc21', '4.0.0-rc22', '4.0.0-rc23', '4.0.0-rc24', '4.0.0-rc29', '4.0.0-rc30', '4.0.0-rc31', '4.0.0-rc32', '4.0.0-rc33', '4.0.0-rc34', '4.0.0-rc35', '4.0.0-rc36', api.VERSION)) or old.get('storage_observer')
            or old.get('config_path') != config or old.get('profile_path') != profile
            or old.get('services') != active_services + [api.NAME + '.timer']
            or old.get('oneshots') != expected_oneshots
            or old.get('configuration_schema') != api.SCHEMA):
        raise RuntimeError('upgrade:unreviewed_legacy_layout')
    helper = Path(helper)
    if api.identity.read(helper) != api.identity.read(api.HERE / reference):
        raise RuntimeError('upgrade:unreviewed_legacy_helper')
    original = [helper]
    for service in services + [services[1][:-6] + '.service']:
        original.extend(api.unit_files(service))
    original = sorted(set(original))
    expected = sorted(set(map(str, [*original, *api.runtime_files(), Path(config), Path(profile),
                        api.UNITS / (api.NAME + '.service'), api.UNITS / (api.NAME + '.timer')])))
    if old.get('critical_paths') != expected:
        raise RuntimeError('upgrade:unreviewed_legacy_paths')
    cfg = api.load(config)
    _, token = api.identity.destination(cfg, target, allow_metrics_only=bool(minimal))
    data = api.load(profile)
    if data.get('host') != target['host']:
        raise RuntimeError('upgrade:legacy_profile_target_mismatch')
    spec = importlib.util.spec_from_file_location('reviewed_blueprint', api.HERE / blueprint)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    generated = module.make_config(data, token, target['ingesting_host'])
    if variant == 'almalinux-wordpress-minimal-v4':
        generated = api.context_cleanup.prepare(generated)
    marker = old.get('migration_review', {}).get('error_aggregation')
    if marker is not None:
        generated = error_aggregation.prepare(generated, marker)
    essential = old.get('migration_review', {}).get('essential_host_sampling')
    if essential is not None:
        import legacy_essential
        generated = legacy_essential.prepare(generated, essential)
    if minimal:
        generated = minimal_host.prepare(generated, minimal)
    base = old.get('migration_review', {}).get('base_monitoring')
    if base is not None:
        import base_monitoring
        if base != base_monitoring.MARKER or not minimal:
            raise RuntimeError('base:unknown_review_marker')
        generated = base_monitoring.cpu_graph(generated, 'legacy_host_essential', 'duplika_metrics')
    if 'filesystem_aliases' in old.get('migration_review', {}):
        import filesystem_aliases
        generated = filesystem_aliases.prepare(generated, old['migration_review']['filesystem_aliases'], target['host'], old['migration_review']['live_mount_flags'])
    # Embedded tests are inactive; all active sources/transforms/sinks must match.
    cfg.pop('tests', None); generated.pop('tests', None)
    if cfg != generated:
        raise RuntimeError('upgrade:legacy_config_differs_from_blueprint')
    if api.identity.snapshot(old['critical_paths'], config) != old['baseline']:
        raise RuntimeError('upgrade:v4_drift')
    vector = api.trusted_executable('vector')
    if not vector or Path(old['vector_binary']).resolve() != Path(vector).resolve():
        raise RuntimeError('upgrade:vector_binary_mismatch')
    return old


def filesystem_plan(api, old):
    import filesystem_aliases
    if not old.get('migration_review', {}).get('minimal_host') or not old['migration_review'].get('base_monitoring'):
        raise RuntimeError('filesystem:requires_minimal_common_base')
    pid_args = ['systemctl', 'show', old['services'][0], '-p', 'MainPID', '--value']
    pid = int(api.execute(pid_args).stdout)
    if pid <= 1:
        raise RuntimeError('filesystem:transport_pid_missing')
    aliases = filesystem_aliases.discover(pid)
    if int(api.execute(pid_args).stdout) != pid:
        raise RuntimeError('filesystem:transport_restarted')
    from capabilities import mount_flags_source
    flags = api.run(['runuser', '-u', 'vector', '--', *mount_flags_source()['command']], timeout=8)
    live_flags = flags.returncode == 0 and any(row in ('/ 0', '/ 1') for row in flags.stdout.splitlines())
    print(json.dumps({'root_capacity_aliases': aliases, 'live_mount_flags': live_flags}))
    proposed = filesystem_aliases.prepare(api.load(old['config_path']), aliases, old['target']['host'], live_flags)
    api.context_cleanup.validate(proposed, old['vector_binary'], api.execute)
    return proposed, aliases, live_flags


def upgrade(api, target, caps, aggregate_errors=False, deduplicate=False):
    old = api.validate_v4_upgrade(target, caps)
    api.identity.status(quiet=True)
    paths = [*api.runtime_files(), api.BASE / 'manifest.json', api.BASE / 'complete',
             api.UNITS / (api.NAME + '.service'), api.UNITS / (api.NAME + '.timer')]
    config = error_aggregation.validate_plan(api, old) if aggregate_errors else None
    aliases = None
    if deduplicate:
        config, aliases, live_flags = filesystem_plan(api, old)
    affected = [api.NAME + '.timer', api.NAME + '.service']
    if config is not None:
        paths += [Path(old['config_path'])]
        affected += [old['services'][0]]
    transaction = api.backup(paths, affected)
    original = [Path(p) for p in old['critical_paths'] if p not in set(map(str, paths))]
    try:
        api.execute(['systemctl', 'stop', api.NAME + '.timer', api.NAME + '.service'])
        review = dict(old.get('migration_review') or {})
        if config is not None:
            if api.identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
                raise RuntimeError('aggregation:changed_during_upgrade')
            path = Path(old['config_path'])
            api.write(path, api.identity.canonical(config), path.stat().st_mode & 0o777)
            if api.trusted_executable('restorecon'):
                api.execute(['restorecon', str(path)])
            api.execute(['systemctl', 'restart', old['services'][0]])
            if aggregate_errors:
                review['error_aggregation'] = error_aggregation.MARKER
            if aliases is not None:
                review['filesystem_aliases'] = aliases
                review['live_mount_flags'] = live_flags
        api.activate(target, caps, Path(old['config_path']), Path(old['profile_path']),
            [s for s in old['services'] if s != api.NAME + '.timer'], old['oneshots'],
            original, old['vector_binary'], old['pipeline_variant'], review)
    except BaseException as error:
        api.recover_failure(transaction, error)
        raise
