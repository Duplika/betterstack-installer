#!/usr/bin/env python3
"""Unified explicit install/upgrade CLI. Only reviewed layouts can migrate."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

from capabilities import VERSION, SCHEMA, detect, parse_json, run, trusted_executable, validate_target
import identity

HERE = Path(__file__).resolve().parent
BASE = identity.BASE
STATE = identity.STATE
UNITS = Path('/etc/systemd/system')
NAME = 'duplika-betterstack-v4'
CLOUD = Path('/etc/duplika-betterstack-cloudlinux')
CLOUD_DATA = Path('/var/lib/duplika-betterstack-cloudlinux')
LEGACY = Path('/etc/duplika-betterstack-identity')


def load(path):
    return json.loads(identity.read(path))


def write(path, data, mode=0o600):
    path = Path(path)
    if path.exists() or path.is_symlink():
        identity.read(path)
    if path.parent.resolve() != path.parent:
        raise RuntimeError('redirected_parent')
    import secrets
    temp = path.with_name('.' + path.name + '.' + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data if isinstance(data, bytes) else data.encode()); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def mkdir(path):
    path = Path(path)
    if path.exists() and (path.is_symlink() or path.resolve() != path or path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
        raise RuntimeError('unsafe_directory')
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def execute(args, timeout=120):
    p = run(args, timeout=timeout)
    if p.returncode:
        raise RuntimeError('step_failed:' + Path(args[0]).name + ':exit=' + str(p.returncode))
    return p


def secure_target(target):
    validate_target(target)
    if target['host'] not in (socket.gethostname(), socket.getfqdn()):
        raise RuntimeError('target:hostname_mismatch')
    ip = trusted_executable('ip')
    if not ip:
        raise RuntimeError('target:iproute_missing')
    addresses = parse_json(run([ip, '-j', 'address', 'show']), 'ip_addresses')
    if target['server_ip'] not in {a['local'] for row in addresses for a in row.get('addr_info', [])}:
        raise RuntimeError('target:ip_not_assigned')
    if not Path('/etc/userdomains').is_file() or not Path('/var/cpanel/users').is_dir():
        raise RuntimeError('cpanel:not_detected')
    for site in target.get('wordpress', []):
        root = Path(site['docroot'])
        account = Path('/var/cpanel/users') / site['user']
        metadata = Path('/var/cpanel/userdata') / site['user'] / site['domain']
        if not account.is_file() or not metadata.is_file() or root.resolve() != root or not root.is_dir():
            raise RuntimeError('wordpress:layout_not_verified')
        match = re.search(r'^documentroot:\s*["\']?(/[^\n"\']+)', metadata.read_text(), re.M)
        if not match or match.group(1).strip() != str(root):
            raise RuntimeError('wordpress:cpanel_docroot_mismatch')
    return detect()


def validate_migration(target):
    """Full old identity check before changing a byte; old pipeline stays intact."""
    if not (LEGACY / 'complete').exists():
        raise RuntimeError('upgrade:requires_complete_v3_identity')
    old = load(LEGACY / 'manifest.json')
    spec = old['spec']
    if any(spec['target'].get(k) != target[k] for k in ('host', 'server_ip', 'source_id', 'ingesting_host')):
        raise RuntimeError('upgrade:target_source_mismatch')
    if spec['target'].get('template_version') != '3':
        raise RuntimeError('upgrade:unknown_previous_release')
    variant = spec['base_variant']
    known = {
        'cloudlinux-v2': ('/etc/duplika-betterstack-cloudlinux/vector.json', '/etc/duplika-betterstack-cloudlinux/profile.json', 'legacy_cloud_config.py'),
        'almalinux-wordpress-v2': ('/etc/duplika-betterstack/vector.yaml', '/etc/duplika-betterstack/profile.json', 'legacy_alma_profile.py'),
        'almalinux-wordpress-context-r6': ('/etc/vector/vector.yaml', '/etc/vector/duplika-profile.json', 'legacy_context_reference.py')}
    if variant not in known or tuple((spec.get('config'), spec.get('profile'))) != known[variant][:2]:
        raise RuntimeError('upgrade:unknown_layout')
    # Compare against bundled reviewed code, not merely a mutable local baseline.
    expected_files = {
        'cloudlinux-v2': {str(CLOUD / 'collector.py'): 'legacy_cloud_collector.py'},
        'almalinux-wordpress-v2': {'/etc/duplika-betterstack/acl.py': 'legacy_alma_acl.py'},
        'almalinux-wordpress-context-r6': {'/usr/local/sbin/duplika-vector-acl.py': 'legacy_context_acl.py',
                                         '/usr/local/sbin/duplika-vector-context.py': 'legacy_context_reader.py'}}
    for installed, bundled in expected_files[variant].items():
        if identity.read(installed) != identity.read(HERE / bundled):
            raise RuntimeError('upgrade:unreviewed_helper_change')
    if identity.read(LEGACY / 'identity.py') != identity.read(HERE / 'legacy_identity.py'):
        raise RuntimeError('upgrade:unreviewed_identity_change')
    module_spec = importlib.util.spec_from_file_location('legacy_identity', HERE / 'legacy_identity.py')
    legacy = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(legacy)
    with tempfile.TemporaryDirectory(prefix='duplika-v3-review-', dir='/root') as folder:
        stage = Path(folder)
        shutil.copyfile(HERE / known[variant][2], stage / 'blueprint.py')
        legacy.HERE = stage
        legacy.expected_config(spec)
        snapshot, health, _, _ = legacy.observe(spec)
    if snapshot != old['baseline'] or not all(health.values()):
        raise RuntimeError('upgrade:v3_drift_or_unhealthy_services')
    return old


def backup(paths, services):
    directory = STATE / ('backup-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    mkdir(directory)
    files = {}
    for i, path in enumerate(sorted(set(map(str, paths)))):
        p = Path(path)
        if p.exists() or p.is_symlink():
            data = identity.read(p)
            dest = directory / str(i)
            write(dest, data, p.stat().st_mode & 0o777)
            files[path] = {'file': str(dest), 'mode': p.stat().st_mode & 0o777}
        else:
            files[path] = None
    statuses = {}
    for service in services:
        active = run(['systemctl', 'is-active', '--quiet', service]).returncode == 0
        enabled = run(['systemctl', 'is-enabled', '--quiet', service]).returncode == 0
        statuses[service] = {'active': active, 'enabled': enabled}
    record = {'files': files, 'services': statuses}
    write(directory / 'transaction.json', identity.canonical(record))
    write(STATE / 'last-transaction.json', identity.canonical({'path': str(directory / 'transaction.json')}))
    return record


def restore(record):
    for service in record['services']:
        run(['systemctl', 'stop', service])
    for path, item in record['files'].items():
        if item:
            write(path, identity.read(item['file']), item['mode'])
        else:
            Path(path).unlink(missing_ok=True)
    execute(['systemctl', 'daemon-reload'])
    for service, status in record['services'].items():
        execute(['systemctl', 'enable' if status['enabled'] else 'disable', service])
        if status['active']:
            execute(['systemctl', 'start', service])


def runtime_files():
    return [BASE / name for name in ('identity.py', 'health.py', 'capabilities.py')]


def activate(target, caps, config, profile_path, services, oneshots, original_paths, vector, pipeline_variant="observer-v4"):
    for name in ('identity.py', 'health.py', 'capabilities.py'):
        write(BASE / name, (HERE / name).read_bytes())
    service = '''[Unit]
Description=Duplika v4 hourly identity and maintenance observations
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /etc/duplika-betterstack-v4/identity.py --publish
UMask=0077
Nice=15
IOSchedulingClass=idle
CPUQuota=10%
MemoryMax=128M
TimeoutStartSec=120
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/duplika-betterstack-v4
'''
    timer = '''[Unit]
Description=Duplika v4 hourly maintenance and version identity
[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
RandomizedDelaySec=120
AccuracySec=30s
[Install]
WantedBy=timers.target
'''
    write(UNITS / (NAME + '.service'), service, 0o644)
    write(UNITS / (NAME + '.timer'), timer, 0o644)
    config_value = load(config)
    identity.destination(config_value, target)
    critical = list(map(str, [*original_paths, *runtime_files(), config, profile_path,
                UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]))
    critical = sorted(set(critical))
    meta = {'installer_version': VERSION, 'agent_version': VERSION, 'configuration_schema': SCHEMA,
        'target': target, 'capabilities': caps, 'pipeline_variant': pipeline_variant, 'config_path': str(config), 'profile_path': str(profile_path),
        'services': services + [NAME + '.timer'], 'oneshots': oneshots,
        'vector_binary': vector, 'critical_paths': critical, 'baseline': identity.snapshot(critical, str(config))}
    write(BASE / 'manifest.json', identity.canonical(meta))
    if trusted_executable('restorecon'):
        execute(['restorecon', '-RF', str(BASE), str(STATE)])
    execute(['systemd-analyze', 'verify', str(UNITS / (NAME + '.service')), str(UNITS / (NAME + '.timer'))])
    execute(['systemctl', 'daemon-reload'])
    execute(['systemctl', 'enable', '--now', NAME + '.timer'])
    identity.status()  # Local verification; remote delivery has a separate acceptance.
    write(BASE / 'complete', VERSION + '\n')
    print('v4 instalada localmente. Recepción remota pendiente; el timer publica identidad cada hora.')


def fresh(target, caps, credential):
    if (BASE / 'manifest.json').exists() or (STATE / 'install-pending.json').exists():
        raise RuntimeError('install:existing_state_use_status_or_resume')
    creds = load(credential)
    if set(creds) != {'source_id', 'host', 'token'} or creds['source_id'] != target['source_id'] or creds['host'] != target['ingesting_host'] or not re.fullmatch(r'[A-Za-z0-9_.-]+', creds.get('token', '')):
        raise RuntimeError('install:invalid_source_credential')
    if (LEGACY / 'manifest.json').exists() or Path('/etc/duplika-betterstack').exists() or Path('/etc/vector/duplika-profile.json').exists():
        raise RuntimeError('install:legacy_layout_detected_use_upgrade')
    write(STATE / 'install-pending.json', identity.canonical({'phase': 'base_installing', 'target': target}))
    with tempfile.TemporaryDirectory(prefix='duplika-install4-', dir='/root') as folder:
        stage = Path(folder)
        for name in ('install_base.py', 'config.py', 'collector.py', 'capabilities.py'):
            shutil.copyfile(HERE / name, stage / name)
        write(stage / 'deployment.json', identity.canonical(target))
        write(stage / 'credentials.json', identity.canonical(creds))
        # Native tests and validation are part of the reviewed base installer.
        for operation in ('preflight', 'install'):
            command = [sys.executable, str(stage / 'install_base.py')]
            if operation == 'preflight':
                command.append('--preflight')
            response = run(command, timeout=600)
            if response.returncode:
                diagnostic = (response.stdout + '\n' + response.stderr).replace(creds['token'], '[TOKEN]')[-6000:]
                write(STATE / 'last-error.json', identity.canonical({'stage': operation, 'exit_code': response.returncode, 'diagnostic': diagnostic}))
                print(diagnostic, file=sys.stderr)
                raise RuntimeError('install_base:' + operation + ':failed; ver /var/lib/duplika-betterstack-v4/last-error.json')
    config = CLOUD / 'vector.json'; profile = CLOUD / 'profile.json'
    paths = [str(p) for p in (config, profile, CLOUD / 'collector.py',
        UNITS / 'duplika-betterstack-cloudlinux.service', UNITS / 'duplika-cloudlinux-observer.service',
        UNITS / 'duplika-cloudlinux-observer.timer')]
    write(STATE / 'install-pending.json', identity.canonical({'phase': 'base_complete', 'target': target,
        'paths': paths, 'baseline': identity.snapshot(paths, str(config))}))
    finish_install(target, caps)


def finish_install(target, caps):
    pending = load(STATE / 'install-pending.json')
    if pending['phase'] != 'base_complete' or pending['target'] != target:
        raise RuntimeError('resume:base_not_complete_or_target_mismatch')
    config, profile = CLOUD / 'vector.json', CLOUD / 'profile.json'
    if identity.snapshot(pending['paths'], str(config)) != pending['baseline']:
        raise RuntimeError('resume:base_changed_review_required')
    activate(target, caps, config, profile,
        ['duplika-betterstack-cloudlinux.service', 'duplika-cloudlinux-observer.timer'],
        ['duplika-cloudlinux-observer.service'], [Path(p) for p in pending['paths']], load(profile)['vector_binary'])
    write(STATE / 'install-pending.json', identity.canonical({'phase': 'complete', 'target': target}))


def observer_base_paths():
    return [CLOUD / 'vector.json', CLOUD / 'profile.json', CLOUD / 'collector.py',
            *[UNITS / name for name in ('duplika-betterstack-cloudlinux.service',
                'duplika-cloudlinux-observer.service', 'duplika-cloudlinux-observer.timer')]]


def validate_v4_upgrade(target, caps):
    """Only reviewed, complete rc3/rc4/rc5 observer layouts can receive this fix."""
    old = load(BASE / 'manifest.json')
    previous = old.get('agent_version')
    reviewed_versions = {'4.0.0-rc3': 'upgrade_rc3.json', '4.0.0-rc4': 'upgrade_rc4.json',
                         '4.0.0-rc5': 'upgrade_rc5.json'}
    if (previous not in reviewed_versions or old.get('installer_version') != previous
            or identity.read(BASE / 'complete') != (previous + '\n').encode()):
        raise RuntimeError('upgrade:unreviewed_v4_version')
    if old.get('target') != target or old.get('capabilities') != caps:
        raise RuntimeError('upgrade:target_or_capabilities_changed')
    expected = observer_base_paths() + runtime_files() + [UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    if (old.get('pipeline_variant') != 'observer-v4' or old.get('configuration_schema') != SCHEMA
            or old.get('config_path') != str(CLOUD / 'vector.json')
            or old.get('profile_path') != str(CLOUD / 'profile.json')
            or old.get('critical_paths') != sorted(map(str, expected))
            or old.get('services') != ['duplika-betterstack-cloudlinux.service',
                'duplika-cloudlinux-observer.timer', NAME + '.timer']
            or old.get('oneshots') != ['duplika-cloudlinux-observer.service']):
        raise RuntimeError('upgrade:unreviewed_v4_layout')
    reviewed = load(HERE / reviewed_versions[previous])
    for path in runtime_files() + [CLOUD / 'collector.py', UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]:
        if hashlib.sha256(identity.read(path)).hexdigest() != reviewed[path.name]:
            raise RuntimeError('upgrade:unreviewed_v4_runtime')
    if identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
        raise RuntimeError('upgrade:v4_drift')
    vector = trusted_executable('vector')
    if not vector or Path(old['vector_binary']).resolve() != Path(vector).resolve():
        raise RuntimeError('upgrade:vector_binary_mismatch')
    identity.destination(load(old['config_path']), target)
    return old


def upgrade_v4(target, caps):
    old = validate_v4_upgrade(target, caps)
    identity.status()  # Must be healthy before any write or service change.
    paths = [*runtime_files(), BASE / 'manifest.json', BASE / 'complete',
             UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    transaction = backup(paths, [NAME + '.timer', NAME + '.service'])
    try:
        execute(['systemctl', 'stop', NAME + '.timer', NAME + '.service'])
        activate(target, caps, Path(old['config_path']), Path(old['profile_path']),
            [name for name in old['services'] if name != NAME + '.timer'],
            old['oneshots'], observer_base_paths(), old['vector_binary'], old['pipeline_variant'])
    except BaseException:
        restore(transaction)
        raise


def upgrade(target, caps):
    if (BASE / 'complete').exists():
        existing = load(BASE / 'manifest.json')
        if existing['agent_version'] == VERSION and existing['target'] == target:
            identity.status()
            print('Versión ya instalada; sin cambios.')
            return
        upgrade_v4(target, caps)
        return
    old = validate_migration(target)
    if target.get('wordpress'):
        raise RuntimeError('upgrade:wordpress_selection_change_requires_separate_review')
    spec = old['spec']
    config, profile_path = Path(spec['config']), Path(spec['profile'])
    profile = load(profile_path)
    paths = [*runtime_files(), BASE / 'manifest.json', BASE / 'complete',
             UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    services = ['duplika-betterstack-identity.timer', 'duplika-betterstack-identity.service',
                NAME + '.timer', NAME + '.service']
    cloud = spec['base_variant'] == 'cloudlinux-v2'
    if cloud:
        if target.get('wordpress'):
            raise RuntimeError('upgrade:wordpress_requires_separate_reviewed_sandbox_change')
        paths += [CLOUD / 'collector.py', profile_path]
        services += ['duplika-cloudlinux-observer.timer', 'duplika-cloudlinux-observer.service']
    transaction = backup(paths, services)
    try:
        execute(['systemctl', 'disable', '--now', 'duplika-betterstack-identity.timer'])
        execute(['systemctl', 'stop', 'duplika-betterstack-identity.service'])
        if cloud:
            execute(['systemctl', 'stop', 'duplika-cloudlinux-observer.timer', 'duplika-cloudlinux-observer.service'])
            profile.update(capabilities=caps, agent_version=VERSION, wordpress=[])
            write(profile_path, identity.canonical(profile))
            write(CLOUD / 'collector.py', (HERE / 'collector.py').read_bytes(), 0o700)
            execute(['systemctl', 'start', 'duplika-cloudlinux-observer.service'], timeout=60)
            execute(['systemctl', 'start', 'duplika-cloudlinux-observer.timer'])
        original = [Path(p) for p in spec['runtime_files'] if not str(p).startswith(str(LEGACY))
                    and not str(p).endswith('duplika-betterstack-identity.service')
                    and not str(p).endswith('duplika-betterstack-identity.timer')]
        for name in spec['services']:
            original.append(UNITS / name)
            if name.endswith('.timer'):
                original.append(UNITS / (name[:-6] + '.service'))
        vector = trusted_executable('vector')
        if not vector:
            raise RuntimeError('upgrade:vector_binary_missing')
        activate(target, caps, config, profile_path, spec['services'],
                 [name[:-6] + '.service' for name in spec['services'] if name.endswith('.timer')], original, vector, 'observer-v4' if cloud else spec['base_variant'])
    except BaseException:
        restore(transaction)
        raise



def create_configuration(args):
    if not args.source_id or not args.ingesting_host or not args.server_ip:
        raise RuntimeError('configure:required_source_id_ingesting_host_server_ip')
    target = {'host': args.host or socket.getfqdn(), 'server_ip': args.server_ip,
              'source_id': args.source_id, 'ingesting_host': args.ingesting_host}
    secure_target(target)
    if args.target.exists():
        if load(args.target) != target:
            raise RuntimeError('configure:existing_profile_differs_not_overwritten')
        print('Perfil ya configurado; sin cambios.')
        return
    parent = args.target.parent
    if parent.resolve() != parent or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
        raise RuntimeError('configure:unsafe_parent_directory')
    write(args.target, identity.canonical(target))
    print('Perfil creado sin credenciales: ' + str(args.target))


def create_credential(target, path):
    import getpass
    if not sys.stdin.isatty():
        raise RuntimeError('credential:interactive_terminal_required')
    if path.exists() or path.is_symlink():
        raise RuntimeError('credential:existing_file_not_overwritten')
    parent = path.parent
    if parent.resolve() != parent or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
        raise RuntimeError('credential:unsafe_parent_directory')
    token = getpass.getpass('Token de ingesta de esta fuente (entrada oculta): ')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', token):
        raise RuntimeError('credential:invalid_token_format')
    write(path, identity.canonical({'source_id':target['source_id'], 'host':target['ingesting_host'], 'token':token}))
    print('Credencial guardada localmente con permisos 600; no se comprobó la recepción remota.')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['preflight', 'install', 'upgrade', 'verify', 'status', 'rollback', 'resume', 'configure', 'credential'])
    p.add_argument('--target', type=Path, default=Path('/root/perfil.json'))
    p.add_argument('--host')
    p.add_argument('--server-ip')
    p.add_argument('--source-id', type=int)
    p.add_argument('--ingesting-host')
    p.add_argument('--credential', type=Path)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error('Ejecutar como root.')
    os.umask(0o077)
    lock = os.open('/run/duplika-betterstack-v4.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.operation == 'configure':
        create_configuration(args)
        return
    if args.operation in ('verify', 'status'):
        identity.status()
        return
    if args.operation == 'rollback':
        record = load(load(STATE / 'last-transaction.json')['path'])
        restore(record)
        print('Transacción anterior restaurada; verificar recepción remota.')
        return
    if not args.target:
        p.error('--target es obligatorio')
    target = load(args.target)
    caps = secure_target(target)
    if args.operation == 'credential':
        create_credential(target, args.credential or Path('/root/fuente.json'))
        return
    if args.operation == 'preflight':
        previous = None
        if (BASE / 'complete').exists():
            if load(BASE / 'manifest.json')['target'] != target:
                raise RuntimeError('preflight:target_mismatch')
            previous = load(BASE / 'manifest.json')['agent_version']
            if previous != VERSION:
                validate_v4_upgrade(target, caps)
            identity.status()
        if LEGACY.exists() and not (BASE / 'complete').exists():
            previous = validate_migration(target)['spec']['base_variant']
        elif not (BASE / 'complete').exists():
            for path in (CLOUD, CLOUD_DATA):
                if path.exists() or path.is_symlink():
                    raise RuntimeError('preflight:partial_or_unknown_installation')
        if previous is None and not (BASE / 'complete').exists():
            with tempfile.TemporaryDirectory(prefix='duplika-preflight4-', dir='/root') as folder:
                stage = Path(folder)
                for name in ('install_base.py', 'config.py', 'collector.py', 'capabilities.py'):
                    shutil.copyfile(HERE / name, stage / name)
                write(stage / 'deployment.json', identity.canonical(target))
                response = run([sys.executable, str(stage / 'install_base.py'), '--preflight'])
                if response.returncode:
                    print((response.stdout + '\n' + response.stderr)[-6000:], file=sys.stderr)
                    raise RuntimeError('preflight:base_checks_failed')
        print(json.dumps({'host': target['host'], 'source_id': target['source_id'],
            'capabilities': caps, 'migration_from': previous, 'installer_version': VERSION,
            'changes_applied': False}, indent=2))
        return
    mkdir(BASE); mkdir(STATE)
    if args.operation in ('install', 'resume') and (BASE / 'complete').exists():
        if load(BASE / 'manifest.json')['target'] != target:
            raise RuntimeError('install:target_mismatch')
        identity.status()
        print('Ya instalado; sin cambios.')
        return
    if args.operation == 'resume':
        finish_install(target, caps)
    elif args.operation == 'install':
        if not args.credential:
            p.error('--credential es obligatorio para instalación nueva')
        fresh(target, caps, args.credential)
    else:
        upgrade(target, caps)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
