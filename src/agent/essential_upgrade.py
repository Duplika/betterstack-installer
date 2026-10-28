"""Explicit rc26 observer migration. No legacy transport adoption or rebaselining."""
import copy
import datetime
import json
import time
import resource
from pathlib import Path

import collector
import config


def storage_coverage(api):
    marker = api.load(api.CLOUD_DATA / 'private/storage-health.json')
    if not 0 <= time.time() - marker.get('at', 0) < 180 or marker.get('probe_failures') != 0:
        raise RuntimeError('essential:storage_not_healthy')
    args = ['systemd-run', '--wait', '--pipe', '--collect']
    for line in api.STORAGE_SERVICE.splitlines():
        if '=' in line and line.split('=', 1)[0] not in ('Description', 'Type', 'ExecStart', 'ReadWritePaths'):
            args += ['-p', line]
    args += [api.sys.executable, '-E', '-s', '-B', str(api.HERE / 'storage.py'), '--probe']
    response = api.run(args, timeout=60)
    try:
        result = json.loads(response.stdout)
    except ValueError:
        raise RuntimeError('essential:storage_native_schema') from None
    if response.returncode or not isinstance(result.get('volumes'), list) or not result['volumes']:
        raise RuntimeError('essential:storage_native_coverage_failed')
    if any(not isinstance(row, dict) or 'total_bytes' not in row or 'available_bytes' not in row or 'readonly' not in row for row in result['volumes']):
        raise RuntimeError('essential:storage_native_coverage_incomplete')
    return len(result['volumes'])


def plan(api, target, caps):
    old = api.validate_v4_upgrade(target, caps)
    if old.get('agent_version') != '4.0.0-rc26' or old.get('pipeline_variant') != 'observer-v4' or not old.get('storage_observer'):
        raise RuntimeError('essential:requires_complete_rc26_observer_with_storage')
    api.identity.status(quiet=True)
    profile = api.load(api.CLOUD / 'profile.json')
    if profile.get('monitoring_policy') is not None:
        raise RuntimeError('essential:unexpected_existing_policy')
    original = api.load(old['config_path'])
    uri, token = api.identity.destination(original, target)
    expected = config.make_config(profile, token, target['ingesting_host'])
    active, blueprint = copy.deepcopy(original), copy.deepcopy(expected)
    active.pop('tests', None); blueprint.pop('tests', None)
    if active != blueprint:
        raise RuntimeError('essential:transport_differs_from_reviewed_graph')
    volumes = storage_coverage(api)
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    historical = collector.query_pressure(profile, time.time())
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    query_seconds = time.monotonic() - started
    proposed = config.essential_config(original)
    api.context_cleanup.validate(proposed, old['vector_binary'], api.execute)
    return old, profile, proposed, {'volumes_validated': volumes,
        'pressure_status': 'not_supported' if historical is None else 'available',
        'pressure_accounts_selected': 0 if historical is None else len(historical[2]),
        'pressure_query_wall_seconds': round(query_seconds, 3),
        'pressure_query_child_cpu_seconds': round(after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime, 3)}


def migrate(api, target, caps, apply=False):
    current = api.load(api.BASE / 'manifest.json')
    if current.get('agent_version') == api.VERSION:
        api.validate_current(target, caps)
        if api.load(api.CLOUD / 'profile.json').get('monitoring_policy') != collector.ESSENTIAL_POLICY:
            raise RuntimeError('essential:policy_must_be_selected_during_rc26_upgrade')
        print(json.dumps({'policy': collector.ESSENTIAL_POLICY, 'already_installed': True, 'changes_applied': False}))
        return
    old, profile, proposed, proof = plan(api, target, caps)
    if not apply:
        print(json.dumps(dict(proof, policy=collector.ESSENTIAL_POLICY, changes_applied=False)))
        return
    paths = [*api.runtime_files(), api.CLOUD / 'collector.py', api.CLOUD / 'profile.json',
             Path(old['config_path']), api.BASE / 'manifest.json', api.BASE / 'complete',
             api.UNITS / (api.NAME + '.service'), api.UNITS / (api.NAME + '.timer')]
    services = [api.NAME + '.timer', api.NAME + '.service', 'duplika-cloudlinux-observer.timer',
                'duplika-betterstack-cloudlinux.service']
    transaction = api.backup(paths, services)
    try:
        api.execute(['systemctl', 'stop', api.NAME + '.timer', api.NAME + '.service', 'duplika-cloudlinux-observer.timer'])
        with api.observer_quiet_point():
            if api.identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
                raise RuntimeError('essential:changed_during_upgrade')
            profile.update(agent_version=api.VERSION, monitoring_policy=collector.ESSENTIAL_POLICY)
            first_cycle_after = time.time()
            api.write(api.CLOUD / 'collector.py', (api.HERE / 'collector.py').read_bytes(), 0o700)
            api.write(api.CLOUD / 'profile.json', api.identity.canonical(profile))
            api.execute(['systemctl', 'stop', 'duplika-betterstack-cloudlinux.service'])
            api.write(old['config_path'], api.identity.canonical(proposed), Path(old['config_path']).stat().st_mode & 0o777)
            if api.trusted_executable('restorecon'):
                api.execute(['restorecon', '-F', str(api.CLOUD / 'collector.py'), str(api.CLOUD / 'profile.json'), old['config_path']])
            api.execute(['runuser', '-u', 'duplikaobs', '--', old['vector_binary'], 'validate', '--no-environment', '--config-json', old['config_path']])
            api.execute(['systemctl', 'start', 'duplika-betterstack-cloudlinux.service'])
        api.wait_observer_inactive()
        api.execute(['systemctl', 'start', 'duplika-cloudlinux-observer.service'], timeout=60)
        api.verify_new_observer_cycle(first_cycle_after)
        marker = api.load(api.CLOUD_DATA / 'private/collector-health.json')
        if marker.get('monitoring_policy') != collector.ESSENTIAL_POLICY:
            raise RuntimeError('essential:new_cycle_policy_not_verified')
        api.execute(['systemctl', 'start', 'duplika-cloudlinux-observer.timer'])
        review = dict(old.get('migration_review') or {}, essential_monitoring='rc26_to_essential_v1')
        api.activate(target, caps, Path(old['config_path']), Path(old['profile_path']),
            [name for name in old['services'] if name not in (api.NAME + '.timer', 'duplika-storage-observer.timer')],
            [name for name in old['oneshots'] if name != 'duplika-storage-observer.service'],
            api.observer_base_paths(), old['vector_binary'], 'observer-v4', review,
            storage_enabled=True, storage_preserved=True)
    except BaseException as error:
        api.recover_failure(transaction, error)
        raise


def diagnostic(api, args):
    manifest = api.load(api.BASE / 'manifest.json')
    api.validate_current(manifest['target'], api.secure_target(manifest['target']))
    profile = api.load(api.CLOUD / 'profile.json')
    if profile.get('monitoring_policy') != collector.ESSENTIAL_POLICY:
        raise RuntimeError('diagnostic:requires_essential_policy')
    path = api.CLOUD_DATA / 'private/diagnostic.json'
    if args.operation == 'diagnostic-disable':
        if path.exists():
            api.load(path)
            path.unlink()
        print(json.dumps({'diagnostic': 'disabled'}))
        return
    if args.operation == 'diagnostic-status':
        selected = api.load(path) if path.exists() else {}
        print(json.dumps(dict(selected, active=selected.get('expires_at', 0) > time.time())))
        return
    if not args.user or not collector.USER.fullmatch(args.user) or not args.readers or not 1 <= args.minutes <= 60:
        raise RuntimeError('diagnostic:require_exact_user_readers_and_1_to_60_minutes')
    domains, accounts, users = collector.inventory()
    if args.user not in accounts:
        raise RuntimeError('diagnostic:account_not_found')
    if 'lve' in args.readers and profile.get('capabilities', {}).get('lve_status') != 'available':
        raise RuntimeError('diagnostic:lve_not_available')
    now = time.time()
    if 'wordpress' in args.readers:
        approved = [site for site in profile.get('wordpress', []) if site['user'] == args.user
                    and domains.get(site['domain']) == args.user
                    and datetime.datetime.fromisoformat(site['expires_at']).timestamp() > now]
        if not approved:
            raise RuntimeError('diagnostic:wordpress_requires_preapproved_unexpired_sandbox_paths')
    selection = {'user': args.user, 'readers': sorted(set(args.readers)), 'created_at': now,
                 'expires_at': now + args.minutes * 60}
    api.write(path, api.identity.canonical(selection), 0o600)
    print(json.dumps(dict(selection, activation='next_collector_cycle')))
