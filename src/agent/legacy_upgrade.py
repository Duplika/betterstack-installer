"""Read-only review of the two inherited AlmaLinux transports; no pipeline rewrite."""
import importlib.util
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
    if (old['agent_version'] != version or old.get('storage_observer')
            or old.get('config_path') != config or old.get('profile_path') != profile
            or old.get('services') != services + [api.NAME + '.timer']
            or old.get('oneshots') != [services[1][:-6] + '.service']
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
    _, token = api.identity.destination(cfg, target)
    data = api.load(profile)
    if data.get('host') != target['host']:
        raise RuntimeError('upgrade:legacy_profile_target_mismatch')
    spec = importlib.util.spec_from_file_location('reviewed_blueprint', api.HERE / blueprint)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    generated = module.make_config(data, token, target['ingesting_host'])
    if variant == 'almalinux-wordpress-minimal-v4':
        generated = api.context_cleanup.prepare(generated)
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


def upgrade(api, target, caps):
    old = api.validate_v4_upgrade(target, caps)
    api.identity.status(quiet=True)
    paths = [*api.runtime_files(), api.BASE / 'manifest.json', api.BASE / 'complete',
             api.UNITS / (api.NAME + '.service'), api.UNITS / (api.NAME + '.timer')]
    transaction = api.backup(paths, [api.NAME + '.timer', api.NAME + '.service'])
    original = [Path(p) for p in old['critical_paths'] if p not in set(map(str, paths))]
    try:
        api.execute(['systemctl', 'stop', api.NAME + '.timer', api.NAME + '.service'])
        api.activate(target, caps, Path(old['config_path']), Path(old['profile_path']),
            [s for s in old['services'] if s != api.NAME + '.timer'], old['oneshots'],
            original, old['vector_binary'], old['pipeline_variant'], old.get('migration_review'))
    except BaseException as error:
        api.recover_failure(transaction, error)
        raise
