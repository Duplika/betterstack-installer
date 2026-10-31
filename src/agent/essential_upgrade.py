"""Explicit rc26/rc27 observer migration. No legacy adoption or rebaselining."""
import copy
import datetime
import json
import time
import resource
from pathlib import Path

import collector
import config


def disk_coverage(api):
    """Run the distributed parser, not a file-exists check, as a constrained root probe."""
    probe = ('import json,sys; ns={"__name__":"disk_preflight"}; '
             'exec(compile(sys.argv[1],"disk-preflight.py","exec"),ns); '
             'rows=ns["read_diskstats"](); '
             'print(json.dumps({"devices":len(rows),"counter_samples":sum(len(v) for v in rows.values())}))')
    args = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect']
    for setting in ('ProtectSystem=strict', 'ProtectHome=true', 'PrivateTmp=true',
                    'NoNewPrivileges=true', 'CPUQuota=10%', 'MemoryMax=256M',
                    'Nice=15', 'IOSchedulingClass=idle', 'TimeoutStartSec=50'):
        args += ['-p', setting]
    args += [api.sys.executable, '-I', '-c', probe, (api.HERE / 'collector.py').read_text()]
    response = api.run(args, timeout=60)
    try:
        proof = json.loads(response.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('essential:disk_native_schema') from None
    if (response.returncode or not isinstance(proof, dict) or isinstance(proof.get('devices'), bool)
            or not isinstance(proof.get('devices'), int) or not 0 < proof['devices'] <= 4096
            or proof.get('counter_samples') != 4 * proof['devices']):
        raise RuntimeError('essential:disk_native_coverage_failed')
    return proof


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
    if old.get('agent_version') not in ('4.0.0-rc26', '4.0.0-rc27', '4.0.0-rc28', '4.0.0-rc29', '4.0.0-rc30', '4.0.0-rc31', '4.0.0-rc32', '4.0.0-rc33') or old.get('pipeline_variant') != 'observer-v4' or not old.get('storage_observer'):
        raise RuntimeError('essential:requires_complete_reviewed_observer_with_storage')
    api.identity.status(quiet=True)
    profile = api.load(api.CLOUD / 'profile.json')
    expected_policy = None if old['agent_version'] == '4.0.0-rc26' else collector.ESSENTIAL_POLICY
    expected_disk = old['agent_version'] in ('4.0.0-rc28', '4.0.0-rc29', '4.0.0-rc30', '4.0.0-rc31', '4.0.0-rc32', '4.0.0-rc33')
    disk_matches = profile.get('disk_io_observer') is True if expected_disk else not profile.get('disk_io_observer')
    if profile.get('monitoring_policy') != expected_policy or not disk_matches:
        raise RuntimeError('essential:unexpected_existing_policy')
    if api.run(['systemctl', 'is-active', '--quiet', 'vector.service']).returncode == 0:
        raise RuntimeError('essential:second_transport_active')
    original = api.load(old['config_path'])
    uri, token = api.identity.destination(original, target)
    expected = config.make_config(profile, token, target['ingesting_host'])
    active, blueprint = copy.deepcopy(original), copy.deepcopy(expected)
    active.pop('tests', None); blueprint.pop('tests', None)
    if active != blueprint:
        raise RuntimeError('essential:transport_differs_from_reviewed_graph')
    volumes = storage_coverage(api)
    disk = disk_coverage(api)
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    historical = collector.query_pressure(profile, time.time())
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    query_seconds = time.monotonic() - started
    proposed = config.make_config(dict(profile, monitoring_policy=collector.ESSENTIAL_POLICY,
                                       disk_io_observer=True), token, target['ingesting_host'])
    api.context_cleanup.validate(proposed, old['vector_binary'], api.execute)
    return old, profile, proposed, {'volumes_validated': volumes,
        'disk_io_devices_validated': disk['devices'], 'disk_io_counter_samples_validated': disk['counter_samples'],
        'pressure_status': 'not_supported' if historical is None else 'available',
        'pressure_accounts_selected': 0 if historical is None else len(historical[2]),
        'pressure_query_wall_seconds': round(query_seconds, 3),
        'pressure_query_child_cpu_seconds': round(after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime, 3)}


def migrate(api, target, caps, apply=False):
    current = api.load(api.BASE / 'manifest.json')
    if current.get('pipeline_variant') != 'observer-v4':
        import legacy_essential
        legacy_essential.migrate(api, target, caps, apply=apply)
        return
    if current.get('agent_version') == api.VERSION:
        api.validate_current(target, caps)
        selected = api.load(api.CLOUD / 'profile.json')
        if selected.get('monitoring_policy') != collector.ESSENTIAL_POLICY or selected.get('disk_io_observer') is not True:
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
        # A queued old cycle may not have acquired collector.lock yet. Waiting
        # after taking the lock would make that healthy cycle fail with EAGAIN.
        api.wait_observer_inactive()
        with api.observer_quiet_point():
            if api.identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
                raise RuntimeError('essential:changed_during_upgrade')
            profile.update(agent_version=api.VERSION, monitoring_policy=collector.ESSENTIAL_POLICY, disk_io_observer=True)
            first_cycle_after = time.time()
            profile['disk_io_generation'] = first_cycle_after
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
        if marker.get('monitoring_policy') != collector.ESSENTIAL_POLICY or marker.get('components', {}).get('disk_io') != 'available':
            raise RuntimeError('essential:new_cycle_policy_not_verified')
        api.execute(['systemctl', 'start', 'duplika-cloudlinux-observer.timer'])
        review = dict(old.get('migration_review') or {}, essential_monitoring='rc26_or_rc27_to_essential_io_v28')
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
