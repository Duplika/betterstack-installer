"""Explicit removal of application/journal readers from reviewed legacy hosts.

No site data, credentials, offsets, buffers or ACL entries are deleted. The old
ACL helper stays on disk for rollback but its timer and service are stopped.
"""
import copy
import json
from pathlib import Path

import legacy_essential
import legacy_upgrade

MARKER = 'host_metrics_only_v35'
POLICY = 'essential-host-v1'
SAMPLING = dict(legacy_essential.SAMPLING, wordpress_errors='disabled',
                application_logs=False, journal_logs=False, acl_timer=False)


def prepare(original, marker=MARKER):
    if marker != MARKER:
        raise RuntimeError('minimal_host:unknown_review_marker')
    cfg = copy.deepcopy(original)
    sources = ('duplika_host', 'duplika_host_slow')
    transforms = ('host_to_json', 'host_for_betterstack', 'legacy_host_essential')
    if (not all(n in cfg['sources'] for n in sources)
            or not all(n in cfg['transforms'] for n in transforms)
            or cfg['transforms']['host_to_json']['inputs'] != list(sources)
            or cfg['sinks']['duplika_metrics']['inputs'] != ['legacy_host_essential']):
        raise RuntimeError('minimal_host:unexpected_graph')
    cfg['sources'] = {k: cfg['sources'][k] for k in sources}
    cfg['transforms'] = {k: cfg['transforms'][k] for k in transforms}
    cfg['sinks'] = {'duplika_metrics': cfg['sinks']['duplika_metrics']}
    # Log-reader tests no longer describe the selected graph.
    cfg['tests'] = [t for t in cfg.get('tests', [])
                    if t.get('name', '').startswith('legacy essential ')]
    return cfg


def retired_units(old):
    services = legacy_upgrade.LAYOUTS[old['pipeline_variant']][-1]
    return services[1], services[1][:-6] + '.service'


def ensure_single_transport(api, old):
    for other in ('vector.service', 'duplika-betterstack.service', 'duplika-betterstack-cloudlinux.service'):
        if other != old['services'][0]:
            result = api.run(['systemctl', 'is-active', '--quiet', other])
            if result.returncode == 0:
                raise RuntimeError('minimal_host:second_transport_active')
            if result.returncode not in (3, 4):
                raise RuntimeError('minimal_host:transport_state_unavailable')


def ensure_retired(api, old):
    timer, service = retired_units(old)
    for unit in (timer, service):
        result = api.run(['systemctl', 'is-active', '--quiet', unit])
        if result.returncode not in (3, 4):
            raise RuntimeError('minimal_host:log_reader_still_active')
    result = api.run(['systemctl', 'is-enabled', timer])
    if result.stdout.strip() != 'disabled':
        raise RuntimeError('minimal_host:acl_timer_not_disabled')


def plan(api, target, caps):
    old = api.validate_v4_upgrade(target, caps)
    if (old.get('pipeline_variant') not in legacy_upgrade.LAYOUTS
            or old.get('agent_version') not in ('4.0.0-rc30', '4.0.0-rc34')
            or old.get('migration_review', {}).get('essential_host_sampling') != legacy_essential.MARKER
            or old.get('migration_review', {}).get('minimal_host')):
        raise RuntimeError('minimal_host:requires_reviewed_essential_legacy')
    api.identity.status(quiet=True)
    ensure_single_transport(api, old)
    original = api.load(old['config_path'])
    proposed = prepare(original)
    # Validate and test as the real transport account before any live mutation.
    api.context_cleanup.validate(proposed, old['vector_binary'], api.execute)
    proof = legacy_essential.native_probe(api, old, proposed)
    return old, proposed, dict(proof, policy=POLICY, application_readers=0,
        journal_readers=0, acl_timer='disabled_on_apply', storage_observer=False,
        wordpress_errors='disabled', remote_acceptance='external_evidence_required')


def migrate(api, target, caps, apply=False):
    current = api.load(api.BASE/'manifest.json')
    if current.get('agent_version') == api.VERSION:
        api.validate_current(target, caps)
        legacy_upgrade.validate(api, current, target)
        if current.get('migration_review', {}).get('minimal_host') != MARKER:
            raise RuntimeError('minimal_host:select_during_version_upgrade')
        ensure_single_transport(api, current)
        ensure_retired(api, current)
        print(json.dumps({'policy': POLICY, 'application_readers': 0,
                          'already_installed': True, 'changes_applied': False}))
        return
    old, proposed, proof = plan(api, target, caps)
    if not apply:
        print(json.dumps(dict(proof, changes_applied=False)))
        return
    path = Path(old['config_path'])
    transport = old['services'][0]
    timer, service = retired_units(old)
    paths = [*api.runtime_files(), path, api.BASE/'manifest.json', api.BASE/'complete',
             api.UNITS/(api.NAME+'.service'), api.UNITS/(api.NAME+'.timer')]
    transaction = api.backup(paths, [api.NAME+'.timer', api.NAME+'.service', transport, timer, service])
    try:
        api.execute(['systemctl', 'stop', api.NAME+'.timer', api.NAME+'.service'])
        api.execute(['systemctl', 'disable', '--now', timer])
        api.execute(['systemctl', 'stop', service])
        if api.identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
            raise RuntimeError('minimal_host:changed_during_upgrade')
        ensure_single_transport(api, old)
        # Stop/drain one emitter before replacing its graph; keep its data_dir.
        api.execute(['systemctl', 'stop', transport])
        api.write(path, api.identity.canonical(proposed), path.stat().st_mode & 0o777)
        if api.trusted_executable('restorecon'):
            api.execute(['restorecon', '-F', str(path)])
        api.execute(['runuser', '-u', 'vector', '--', old['vector_binary'], 'validate',
                     '--no-environment', '--config-json', str(path)])
        api.execute(['systemctl', 'start', transport])
        ensure_retired(api, old)
        original = [Path(p) for p in old['critical_paths'] if p not in set(map(str, paths))]
        review = dict(old.get('migration_review') or {}, minimal_host=MARKER)
        api.activate(target, caps, path, Path(old['profile_path']), [transport], [],
                     original, old['vector_binary'], old['pipeline_variant'], review)
    except BaseException as error:
        api.recover_failure(transaction, error)
        raise
    print(json.dumps(dict(proof, changes_applied=True)))
