#!/usr/bin/env python3
"""Unified explicit install/upgrade CLI. Only reviewed layouts can migrate."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import socket
import pwd
import subprocess
import sys
import tempfile
import time

from capabilities import VERSION, SCHEMA, detect, parse_json, run, trusted_executable, validate_target
import identity
import context_cleanup

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
            if path.exists():
                previous = path.stat()
                os.fchown(f.fileno(), previous.st_uid, previous.st_gid)
            # os.open's mode is masked by the installer's mandatory umask 077.
            # Set the requested final mode explicitly, after ownership changes.
            os.fchmod(f.fileno(), mode)
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
        step = Path(args[0]).name
        if step == 'systemctl':
            # Only fixed operations/unit names, never command output or secrets.
            operation = args[1] if args[1] in ('start', 'stop', 'enable', 'disable', 'daemon-reload') else 'command'
            units = [arg for arg in args[2:] if re.fullmatch(r'[A-Za-z0-9_.@-]+\.(?:service|timer)', arg)]
            step += ':' + operation + (':' + ','.join(units) if units else '')
        raise RuntimeError('step_failed:' + step + ':exit=' + str(p.returncode))
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


def verify_packaged_vector(spec, observed_sha):
    """One-time migration check against the local RPM database, no downloads."""
    binary = trusted_executable('vector')
    rpm = trusted_executable('rpm')
    if not binary or not rpm or not re.fullmatch(r'[0-9a-f]{64}', observed_sha):
        raise RuntimeError('upgrade:vector_package_verification_unavailable')
    binary = Path(binary).resolve()
    pid = execute(['systemctl', 'show', spec['vector_service'], '-p', 'MainPID', '--value']).stdout.strip()
    if not re.fullmatch(r'[1-9][0-9]*', pid):
        raise RuntimeError('upgrade:vector_process_missing')
    installed = binary.stat()
    running = (Path('/proc') / pid / 'exe').stat()
    if (installed.st_dev, installed.st_ino) != (running.st_dev, running.st_ino):
        raise RuntimeError('upgrade:vector_running_file_differs_from_installed')
    metadata = execute([rpm, '-qf', '--qf', '%{NAME}\n%{VERSION}-%{RELEASE}.%{ARCH}\n%{FILEDIGESTALGO}\n', str(binary)]).stdout.splitlines()
    if (len(metadata) != 3 or metadata[0] != 'vector' or metadata[2] != '8'
            or not re.fullmatch(r'[A-Za-z0-9_.+~:-]{1,200}', metadata[1])):
        raise RuntimeError('upgrade:unexpected_vector_package_or_digest_algorithm')
    listing = execute([rpm, '-qf', '--qf', '[%{FILENAMES}\t%{FILEDIGESTS}\n]', str(binary)]).stdout
    matches = [line.split('\t', 1)[1] for line in listing.splitlines()
               if '\t' in line and line.split('\t', 1)[0] == str(binary)]
    if matches != [observed_sha]:
        raise RuntimeError('upgrade:vector_binary_does_not_match_rpm_digest')
    return {'kind': 'rpm_verified_vector_update', 'package': 'vector-' + metadata[1],
            'binary_path': str(binary), 'accepted_sha256': observed_sha}


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
    changed = sorted(key for key in set(snapshot) | set(old['baseline'])
                     if snapshot.get(key) != old['baseline'].get(key))
    if not all(health.values()) or (changed and changed != ['vector_running_binary_sha256']):
        raise RuntimeError('upgrade:v3_drift_or_unhealthy_services')
    if changed:
        review = verify_packaged_vector(spec, snapshot['vector_running_binary_sha256'])
        review['previous_sha256'] = old['baseline']['vector_running_binary_sha256']
        # Preserve the original v3 baseline, including in its installed manifest.
        old = dict(old, migration_review=review)
        print('Cambio del binario Vector validado contra RPM; el resto de v3 coincide.')
    return old


def backup(paths, services, set_latest=True):
    directory = STATE / ('backup-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    mkdir(directory)
    files = {}
    for i, path in enumerate(sorted(set(map(str, paths)))):
        p = Path(path)
        if p.exists() or p.is_symlink():
            data = identity.read(p)
            dest = directory / str(i)
            write(dest, data, p.stat().st_mode & 0o777)
            files[path] = {'file': str(dest), 'mode': p.stat().st_mode & 0o777,
                           'uid': p.stat().st_uid, 'gid': p.stat().st_gid}
        else:
            files[path] = None
    statuses = {}
    for service in services:
        active = run(['systemctl', 'is-active', '--quiet', service]).returncode == 0
        enabled = run(['systemctl', 'is-enabled', '--quiet', service]).returncode == 0
        statuses[service] = {'active': active, 'enabled': enabled}
    record = {'files': files, 'services': statuses}
    write(directory / 'transaction.json', identity.canonical(record))
    if set_latest:
        write(STATE / 'last-transaction.json', identity.canonical({'path': str(directory / 'transaction.json')}))
    return record


def restore(record):
    errors = []
    def attempt(args):
        try:
            execute(args)
        except Exception as error:
            errors.append(str(error))
    for service in record['services']:
        try:
            run(['systemctl', 'stop', service])
        except Exception:
            errors.append('restore_stop_failed:' + service)
    for path, item in record['files'].items():
        try:
            if item:
                write(path, identity.read(item['file']), item['mode'])
                os.chown(path, item.get('uid', 0), item.get('gid', 0))
                Path(path).chmod(item['mode'])
            else:
                Path(path).unlink(missing_ok=True)
        except Exception:
            errors.append('restore_file_failed:' + Path(path).name)
    restored = [path for path, item in record['files'].items() if item and Path(path).exists()]
    if restored and trusted_executable('restorecon'):
        attempt(['restorecon', '-F', *restored])
    attempt(['systemctl', 'daemon-reload'])
    # Recover previously running services before touching enablement. A missing
    # new timer or another error must never prevent restarting the transport.
    for service, status in record['services'].items():
        if status['active']:
            attempt(['systemctl', 'start', service])
    for service, status in record['services'].items():
        try:
            loaded = run(['systemctl', 'show', service, '-p', 'LoadState', '--value'])
            if loaded.stdout.strip() == 'not-found':
                if status['active'] or status['enabled']:
                    errors.append('restore_unit_missing:' + service)
                continue
            attempt(['systemctl', 'enable' if status['enabled'] else 'disable', service])
        except Exception:
            errors.append('restore_unit_state_failed:' + service)
    for service, status in record['services'].items():
        if status['active']:
            try:
                if run(['systemctl', 'is-active', '--quiet', service]).returncode:
                    errors.append('restore_service_inactive:' + service)
            except Exception:
                errors.append('restore_service_check_failed:' + service)
    if errors:
        raise RuntimeError('rollback_incomplete:' + ';'.join(errors))


def recover_failure(record, original):
    try:
        restore(record)
    except Exception as rollback_error:
        raise RuntimeError(str(original) + '; ' + str(rollback_error)) from None


def runtime_files():
    return [BASE / name for name in ('identity.py', 'health.py', 'capabilities.py')]


def unit_files(name):
    properties = execute(['systemctl', 'show', name, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
    values = dict(line.split('=', 1) for line in properties.splitlines() if '=' in line)
    fragment = values.get('FragmentPath', '')
    if not fragment or not Path(fragment).is_absolute():
        raise RuntimeError('upgrade:unit_fragment_missing:' + name)
    paths = [Path(fragment), *map(Path, shlex.split(values.get('DropInPaths', '')))]
    resolved = []
    for path in paths:
        if not path.is_absolute():
            raise RuntimeError('upgrade:unit_path_invalid:' + name)
        path = path.resolve()
        identity.read(path)
        resolved.append(path)
    return resolved


def migration_files(spec, simplify_context=False):
    removed = context_cleanup.paths(UNITS) if simplify_context else []
    original = [Path(p) for p in spec['runtime_files'] if not str(p).startswith(str(LEGACY))
                and not str(p).endswith('duplika-betterstack-identity.service')
                and not str(p).endswith('duplika-betterstack-identity.timer')]
    active_services = [name for name in spec['services']
                       if not (simplify_context and name == 'duplika-vector-context.timer')]
    for name in active_services:
        original.extend(unit_files(name))
        if name.endswith('.timer'):
            original.extend(unit_files(name[:-6] + '.service'))
    original = sorted(set(path for path in original if path not in removed))
    for path in original:
        identity.read(path)
    return original, active_services


def storage_paths():
    return [CLOUD / 'storage.py', UNITS / 'duplika-storage-observer.service',
            UNITS / 'duplika-storage-observer.timer']


def enable_storage():
    # Own bounded spool streams match the existing Vector glob; no token/config changes.
    paths = storage_paths()
    if any(path.exists() or path.is_symlink() for path in paths):
        raise RuntimeError('storage:existing_layout_requires_review')
    if (UNITS / 'duplika-storage-observer.service.d').exists() or (UNITS / 'duplika-storage-observer.timer.d').exists():
        raise RuntimeError('storage:unreviewed_dropins')
    transaction = backup(paths, ['duplika-storage-observer.timer', 'duplika-storage-observer.service'], set_latest=False)
    try:
        write(paths[0], (HERE / 'storage.py').read_bytes(), 0o700)
        service = STORAGE_SERVICE.replace('__PYTHON__', sys.executable)
        write(paths[1], service, 0o644)
        write(paths[2], STORAGE_TIMER, 0o644)
        if trusted_executable('restorecon'):
            execute(['restorecon', '-F', *map(str, paths)])
        execute(['systemd-analyze', 'verify', str(paths[1]), str(paths[2])])
        execute(['systemctl', 'daemon-reload'])
        execute(['systemctl', 'start', 'duplika-storage-observer.service'])
        health = load(CLOUD_DATA / 'private/storage-health.json')
        if not 0 <= time.time() - health.get('at', 0) < 180 or health.get('probe_failures') != 0:
            raise RuntimeError('storage:first_cycle_not_healthy')
        execute(['systemctl', 'enable', '--now', 'duplika-storage-observer.timer'])
    except BaseException as error:
        recover_failure(transaction, error)
        raise
    return transaction


STORAGE_SERVICE = """[Unit]
Description=Duplika local volume, MD RAID and bounded SMART metadata
[Service]
Type=oneshot
ExecStart=__PYTHON__ -E -s -B /etc/duplika-betterstack-cloudlinux/storage.py
UMask=0077
Nice=15
IOSchedulingClass=idle
CPUAccounting=true
MemoryAccounting=true
CPUQuota=5%
MemoryMax=96M
TimeoutStartSec=45
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX
IPAddressDeny=any
ReadWritePaths=/var/lib/duplika-betterstack-cloudlinux/private /var/lib/duplika-betterstack-cloudlinux/spool
"""
STORAGE_TIMER = """[Unit]
Description=Duplika local storage metadata every minute
[Timer]
OnBootSec=45s
OnUnitActiveSec=60s
AccuracySec=2s
RandomizedDelaySec=5s
[Install]
WantedBy=timers.target
"""


def activate(target, caps, config, profile_path, services, oneshots, original_paths, vector, pipeline_variant="observer-v4", migration_review=None, storage_enabled=False, storage_preserved=False):
    python = trusted_executable(sys.executable)
    if not python or not re.fullmatch(r'/[A-Za-z0-9_./-]+', python):
        raise RuntimeError('unsafe_python_interpreter')
    for name in ('identity.py', 'health.py', 'capabilities.py'):
        write(BASE / name, (HERE / name).read_bytes())
    service = '''[Unit]
Description=Duplika v4 hourly identity and maintenance observations
After=network-online.target
[Service]
Type=oneshot
ExecStart=__PYTHON__ -E -s -B /etc/duplika-betterstack-v4/identity.py --publish
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
'''.replace('__PYTHON__', python)
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
    storage_transaction = None
    if storage_enabled:
        if not storage_preserved:
            storage_transaction = enable_storage()
        original_paths = [*original_paths, *storage_paths()]
        services = [*services, 'duplika-storage-observer.timer']
        oneshots = [*oneshots, 'duplika-storage-observer.service']
    critical = list(map(str, [*original_paths, *runtime_files(), config, profile_path,
                UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]))
    critical = sorted(set(critical))
    meta = {'installer_version': VERSION, 'agent_version': VERSION, 'configuration_schema': SCHEMA,
        'target': target, 'capabilities': caps, 'pipeline_variant': pipeline_variant, 'config_path': str(config), 'profile_path': str(profile_path),
        'services': services + [NAME + '.timer'], 'oneshots': oneshots,
        'vector_binary': vector, 'critical_paths': critical, 'baseline': identity.snapshot(critical, str(config))}
    if pipeline_variant == 'observer-v4' and (CLOUD / 'collector.py').read_bytes() == (HERE / 'collector.py').read_bytes():
        meta['resource_sampling'] = ({'processes_seconds': None, 'lve_seconds': 900, 'history_only': True, 'access_default': False, 'inventory_seconds': 43200}
            if load(profile_path).get('monitoring_policy') == 'essential-v1' else {'processes_seconds': 300, 'lve_seconds': 300})
        meta['monitoring_policy'] = load(profile_path).get('monitoring_policy', 'observer-v4')
    if storage_enabled:
        meta['storage_observer'] = True
    try:
        if migration_review:
            meta['migration_review'] = migration_review
        write(BASE / 'manifest.json', identity.canonical(meta))
        if trusted_executable('restorecon'):
            execute(['restorecon', '-RF', str(BASE), str(STATE)])
        execute(['systemd-analyze', 'verify', str(UNITS / (NAME + '.service')), str(UNITS / (NAME + '.timer'))])
        execute(['systemctl', 'daemon-reload'])
        execute(['systemctl', 'enable', '--now', NAME + '.timer'])
        identity.status(quiet=True)  # Local verification; remote delivery has a separate acceptance.
        write(BASE / 'complete', VERSION + '\n')
    except BaseException as error:
        if storage_transaction is not None:
            recover_failure(storage_transaction, error)
        raise


def validate_bootstrap_recovery(target):
    """Only an early DNF failure before any observer/account/config/spool exists."""
    for path in (CLOUD, CLOUD_DATA, BASE / 'manifest.json', BASE / 'complete', LEGACY,
                 Path('/etc/duplika-betterstack'), Path('/etc/vector/duplika-profile.json')):
        if path.exists() or path.is_symlink():
            raise RuntimeError('bootstrap_recovery:existing_base_or_legacy')
    if load(STATE / 'install-pending.json') != {'phase': 'base_installing', 'target': target}:
        raise RuntimeError('bootstrap_recovery:target_or_phase')
    failure = load(STATE / 'last-error.json')
    if (failure.get('stage') != 'install' or failure.get('exit_code') != 1
            or not re.search(r'(?:^|\n)ERROR: command_failed:dnf:exit=1(?:\n|$)', failure.get('diagnostic', ''))):
        raise RuntimeError('bootstrap_recovery:unreviewed_failure')
    try:
        pwd.getpwnam('duplikaobs')
    except KeyError:
        pass
    else:
        raise RuntimeError('bootstrap_recovery:existing_reader')
    if trusted_executable('vector'):
        raise RuntimeError('bootstrap_recovery:existing_vector_requires_review')
    # Reject unit fragments/drop-ins in all standard administrator/vendor/runtime roots.
    for name in ('duplika-betterstack-cloudlinux', 'duplika-cloudlinux-observer',
                 'duplika-storage-observer', NAME):
        for suffix in ('.service', '.timer', '.service.d', '.timer.d'):
            for folder in (UNITS, Path('/usr/lib/systemd/system'), Path('/run/systemd/system')):
                path = folder / (name + suffix)
                if path.exists() or path.is_symlink():
                    raise RuntimeError('bootstrap_recovery:existing_unit')
    return failure


# Passed as code rather than a file: ProtectHome hides the temporary /root stage.
RESOURCE_PROBE = r"""
import json, subprocess, sys, time
from pathlib import Path
started = time.monotonic()
result = {'status': 'failed'}
try:
    Path('/proc/stat').read_text()
    Path('/proc/self/stat').read_text()
    args = [sys.argv[1], '--json', '--order-by=cpu', '--max=10000']
    if sys.argv[2] == 'domains':
        args.append('--domains')
    response = subprocess.run(args, capture_output=True, text=True, timeout=40,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    data = json.loads(response.stdout)
    if response.returncode or data.get('result') != 'success' or not isinstance(data.get('users'), list):
        raise ValueError('invalid_lve_response')
    result = {'status': 'ok', 'accounts_observed': len(data['users'])}
except subprocess.TimeoutExpired:
    result['error'] = 'lve_timeout'
except Exception:
    result['error'] = 'lve_probe_failed'
result['duration_seconds'] = round(time.monotonic() - started, 2)
print('duplika_preflight=' + json.dumps(result), flush=True)
sys.exit(0 if result['status'] == 'ok' else 1)
"""


def resource_preflight(caps):
    """One transient, bounded probe. Never installed as a periodic service."""
    if caps['lve_status'] != 'available':
        return
    systemd = trusted_executable('systemd-run')
    cloudtop = trusted_executable('cloudlinux-top')
    if not systemd or not cloudtop or cloudtop != caps['cloudlinux_top']:
        raise RuntimeError('preflight:resource_probe_executable_unavailable')
    args = [systemd, '--quiet', '--wait', '--pipe', '--collect',
        '-p', 'ProtectSystem=strict', '-p', 'ProtectHome=true',
        '-p', 'PrivateTmp=true', '-p', 'NoNewPrivileges=true',
        '-p', 'CPUAccounting=true', '-p', 'MemoryAccounting=true',
        '-p', 'CPUQuota=10%', '-p', 'MemoryMax=256M', '-p', 'Nice=15',
        '-p', 'IOSchedulingClass=idle', '-p', 'TimeoutStartSec=50',
        sys.executable, '-I', '-c', RESOURCE_PROBE, cloudtop,
        'domains' if caps['isolates_supported'] else 'users']
    try:
        response = run(args, timeout=55)
        summaries = [line.split('=', 1)[1] for line in response.stdout.splitlines()
                     if line.startswith('duplika_preflight=')]
        result = json.loads(summaries[-1]) if len(summaries) == 1 else {}
    except Exception:
        raise RuntimeError('preflight:resource_probe_failed_before_install') from None
    if response.returncode or result.get('status') != 'ok':
        reason = 'lve_timeout' if result.get('error') == 'lve_timeout' else 'resource_probe_failed'
        raise RuntimeError('preflight:' + reason + '_before_install')
    print(json.dumps({'preflight_resources': result, 'cpu_quota': '10%',
        'lve_timeout_seconds': 40, 'persistent_changes_applied': False}))


def base_preflight(target):
    with tempfile.TemporaryDirectory(prefix='duplika-preflight4-', dir='/root') as folder:
        stage = Path(folder)
        for name in ('install_base.py', 'config.py', 'collector.py', 'capabilities.py'):
            shutil.copyfile(HERE / name, stage / name)
        write(stage / 'deployment.json', identity.canonical(target))
        response = run([sys.executable, str(stage / 'install_base.py'), '--preflight'])
        if response.returncode:
            print((response.stdout + '\n' + response.stderr)[-6000:], file=sys.stderr)
            raise RuntimeError('preflight:base_checks_failed')
        resource_preflight(detect())


def fresh(target, caps, credential, recover_bootstrap=False):
    if caps['os_id'] == 'almalinux' and caps['os_version'].split('.')[0] == '8':
        raise RuntimeError('install:almalinux8_reviewed_migration_only')
    if recover_bootstrap:
        validate_bootstrap_recovery(target)
    elif (BASE / 'manifest.json').exists() or (STATE / 'install-pending.json').exists():
        raise RuntimeError('install:existing_state_use_status_or_resume')
    creds = load(credential)
    if set(creds) != {'source_id', 'host', 'token'} or creds['source_id'] != target['source_id'] or creds['host'] != target['ingesting_host'] or not re.fullmatch(r'[A-Za-z0-9_.-]+', creds.get('token', '')):
        raise RuntimeError('install:invalid_source_credential')
    if (LEGACY / 'manifest.json').exists() or Path('/etc/duplika-betterstack').exists() or Path('/etc/vector/duplika-profile.json').exists():
        raise RuntimeError('install:legacy_layout_detected_use_upgrade')
    resource_preflight(caps)
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
            response = run(command, timeout=1200 if operation == 'install' else 120)
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
        ['duplika-cloudlinux-observer.service'], [Path(p) for p in pending['paths']], load(profile)['vector_binary'], storage_enabled=True)
    write(STATE / 'install-pending.json', identity.canonical({'phase': 'complete', 'target': target}))


def observer_base_paths():
    return [CLOUD / 'vector.json', CLOUD / 'profile.json', CLOUD / 'collector.py',
            *[UNITS / name for name in ('duplika-betterstack-cloudlinux.service',
                'duplika-cloudlinux-observer.service', 'duplika-cloudlinux-observer.timer')]]


def validate_observer_profile(meta, profile):
    previous = meta['agent_version']
    if previous in ('4.0.0-rc3', '4.0.0-rc4', '4.0.0-rc5'):
        return  # Historical identity-only routes; profile is not rewritten.
    allowed = {previous}
    if previous in ('4.0.0-rc6', '4.0.0-rc7'):
        # rc4..rc7 upgraded identity only and kept the original profile bytes.
        # Caller first checks official collector/runtime hashes and full baseline.
        allowed = {'4.0.0-rc' + str(n) for n in range(3, int(previous.split('rc')[1]) + 1)}
    if profile.get('agent_version') not in allowed:
        raise RuntimeError('upgrade:observer_profile_version_mismatch')


def validate_v4_upgrade(target, caps):
    """Only reviewed, complete candidate observer layouts can migrate."""
    old = load(BASE / 'manifest.json')
    previous = old.get('agent_version')
    reviewed_versions = {'4.0.0-rc3': 'upgrade_rc3.json', '4.0.0-rc4': 'upgrade_rc4.json',
                         '4.0.0-rc5': 'upgrade_rc5.json', '4.0.0-rc6': 'upgrade_rc6.json',
                         '4.0.0-rc7': 'upgrade_rc7.json', '4.0.0-rc12': 'upgrade_rc12.json',
                         '4.0.0-rc13': 'upgrade_rc13.json', '4.0.0-rc15': 'upgrade_rc15.json',
                         '4.0.0-rc10': 'upgrade_rc10.json', '4.0.0-rc17': 'upgrade_rc17.json',
                         '4.0.0-rc18': 'upgrade_rc18.json', '4.0.0-rc19': 'upgrade_rc19.json', '4.0.0-rc20': 'upgrade_rc20.json', '4.0.0-rc21': 'upgrade_rc21.json', '4.0.0-rc22': 'upgrade_rc22.json', '4.0.0-rc23': 'upgrade_rc23.json', '4.0.0-rc24': 'upgrade_rc24.json', '4.0.0-rc26': 'upgrade_rc26.json'}
    if (previous not in reviewed_versions or old.get('installer_version') != previous
            or identity.read(BASE / 'complete') != (previous + '\n').encode()):
        raise RuntimeError('upgrade:unreviewed_v4_version')
    if old.get('target') != target or old.get('capabilities') != caps:
        raise RuntimeError('upgrade:target_or_capabilities_changed')
    reviewed = load(HERE / reviewed_versions[previous])
    if 'identity_service_template' in reviewed:
        reviewed[NAME + '.service'] = hashlib.sha256(
            reviewed['identity_service_template'].replace('__PYTHON__', sys.executable).encode()).hexdigest()
    for path in runtime_files() + [UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]:
        if hashlib.sha256(identity.read(path)).hexdigest() != reviewed[path.name]:
            raise RuntimeError('upgrade:unreviewed_v4_runtime')
    if old.get('pipeline_variant') != 'observer-v4':
        import legacy_upgrade
        return legacy_upgrade.validate(sys.modules[__name__], old, target)
    expected = observer_base_paths() + runtime_files() + [UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    had_storage = previous in ('4.0.0-rc15', '4.0.0-rc17', '4.0.0-rc18', '4.0.0-rc19', '4.0.0-rc20', '4.0.0-rc21', '4.0.0-rc22', '4.0.0-rc23', '4.0.0-rc24', '4.0.0-rc26')
    if had_storage:
        expected += storage_paths()
    if (old.get('pipeline_variant') != 'observer-v4' or old.get('configuration_schema') != SCHEMA
            or old.get('config_path') != str(CLOUD / 'vector.json')
            or old.get('profile_path') != str(CLOUD / 'profile.json')
            or old.get('critical_paths') != sorted(map(str, expected))
            or old.get('services') != ['duplika-betterstack-cloudlinux.service',
                'duplika-cloudlinux-observer.timer', *(['duplika-storage-observer.timer'] if had_storage else []), NAME + '.timer']
            or old.get('oneshots') != ['duplika-cloudlinux-observer.service', *(['duplika-storage-observer.service'] if had_storage else [])]
            or bool(old.get('storage_observer')) != had_storage):
        raise RuntimeError('upgrade:unreviewed_v4_layout')
    if had_storage:
        reviewed['duplika-storage-observer.service'] = hashlib.sha256(STORAGE_SERVICE.replace('__PYTHON__', sys.executable).encode()).hexdigest()
        reviewed['duplika-storage-observer.timer'] = hashlib.sha256(STORAGE_TIMER.encode()).hexdigest()
    for path in [CLOUD / 'collector.py'] + (storage_paths() if had_storage else []):
        if hashlib.sha256(identity.read(path)).hexdigest() != reviewed[path.name]:
            raise RuntimeError('upgrade:unreviewed_v4_runtime')
    if identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
        raise RuntimeError('upgrade:v4_drift')
    validate_observer_profile(old, load(CLOUD / 'profile.json'))
    vector = trusted_executable('vector')
    if not vector or Path(old['vector_binary']).resolve() != Path(vector).resolve():
        raise RuntimeError('upgrade:vector_binary_mismatch')
    identity.destination(load(old['config_path']), target)
    return old


@contextlib.contextmanager
def observer_quiet_point(timeout=60):
    """Wait for the current cycle to finish writing; never kill it midway."""
    path = CLOUD_DATA / 'private/collector.lock'
    identity.read(path)  # Existing root-owned regular file and safe parents.
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('upgrade:observer_busy_retry_later')
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


def wait_observer_inactive(timeout=60):
    """A released process lock does not mean systemd finished the old oneshot job."""
    deadline = time.monotonic() + timeout
    while True:
        result = run(['systemctl', 'show', 'duplika-cloudlinux-observer.service',
                      '-p', 'ActiveState', '--value'])
        state = result.stdout.strip() if result.returncode == 0 else ''
        if state == 'inactive':
            return
        if state == 'failed':
            raise RuntimeError('upgrade:previous_observer_job_failed')
        if state not in ('active', 'activating', 'deactivating', 'reloading'):
            raise RuntimeError('upgrade:observer_job_state_unavailable')
        if time.monotonic() >= deadline:
            raise RuntimeError('upgrade:observer_job_busy_retry_later')
        time.sleep(0.1)


def verify_new_observer_cycle(started_after):
    try:
        health = load(CLOUD_DATA / 'private/collector-health.json')
    except FileNotFoundError:
        raise RuntimeError('upgrade:new_observer_cycle_marker_missing') from None
    if (health.get('agent_version') != VERSION or health.get('started_at', 0) < started_after
            or not 0 <= time.time() - health.get('at', 0) < 180
            or not {'processes', 'lve', 'access'}.issubset(health.get('components', {}))
            or 'failed' in health.get('components', {}).values()):
        raise RuntimeError('upgrade:new_observer_cycle_not_verified')


def upgrade_v4(target, caps):
    old = validate_v4_upgrade(target, caps)
    identity.status(quiet=True)  # Must be healthy before any write or service change.
    paths = [*runtime_files(), BASE / 'manifest.json', BASE / 'complete',
             UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    refresh_observer = old['agent_version'] in ('4.0.0-rc6', '4.0.0-rc7', '4.0.0-rc10', '4.0.0-rc12', '4.0.0-rc13', '4.0.0-rc15', '4.0.0-rc17', '4.0.0-rc18', '4.0.0-rc19', '4.0.0-rc20', '4.0.0-rc21', '4.0.0-rc22', '4.0.0-rc23', '4.0.0-rc24', '4.0.0-rc26')
    observer_timer = 'duplika-cloudlinux-observer.timer'
    services = [NAME + '.timer', NAME + '.service']
    if refresh_observer:
        paths += [CLOUD / 'collector.py', CLOUD / 'profile.json']
        services.append(observer_timer)
    add_storage = refresh_observer and not old.get('storage_observer')
    had_storage = bool(old.get('storage_observer'))
    if add_storage:
        paths += storage_paths()
    transaction = backup(paths, services + (['duplika-storage-observer.timer', 'duplika-storage-observer.service'] if add_storage else []))
    try:
        execute(['systemctl', 'stop', *services])
        if refresh_observer:
            with observer_quiet_point():
                if identity.snapshot(old['critical_paths'], old['config_path']) != old['baseline']:
                    raise RuntimeError('upgrade:v4_changed_while_waiting')
                profile = load(CLOUD / 'profile.json')
                validate_observer_profile(old, profile)
                first_cycle_after = time.time()
                write(CLOUD / 'collector.py', (HERE / 'collector.py').read_bytes(), 0o700)
                profile['agent_version'] = VERSION
                write(CLOUD / 'profile.json', identity.canonical(profile))
                if trusted_executable('restorecon'):
                    execute(['restorecon', '-F', str(CLOUD / 'collector.py'), str(CLOUD / 'profile.json')])
            # Do not join an old job that has released its lock but is still exiting.
            wait_observer_inactive()
            execute(['systemctl', 'start', 'duplika-cloudlinux-observer.service'], timeout=60)
            verify_new_observer_cycle(first_cycle_after)
            execute(['systemctl', 'start', observer_timer])
        activate(target, caps, Path(old['config_path']), Path(old['profile_path']),
            [name for name in old['services'] if name not in (NAME + '.timer', 'duplika-storage-observer.timer')],
            [name for name in old['oneshots'] if name != 'duplika-storage-observer.service'], observer_base_paths(), old['vector_binary'], old['pipeline_variant'], old.get('migration_review'), storage_enabled=add_storage or had_storage, storage_preserved=had_storage)
    except BaseException as error:
        recover_failure(transaction, error)
        raise


def upgrade(target, caps, simplify_context=False, aggregate_errors=False):
    if (BASE / 'complete').exists():
        existing = load(BASE / 'manifest.json')
        if existing['agent_version'] == VERSION and existing['target'] == target:
            if simplify_context and existing['pipeline_variant'] != 'almalinux-wordpress-minimal-v4':
                raise RuntimeError('cleanup:requires_reviewed_context_v3')
            validate_current(target, caps)
            print('Versión ya instalada y verificada; sin cambios.')
            return
        if simplify_context:
            raise RuntimeError('cleanup:requires_reviewed_context_v3')
        if existing.get('pipeline_variant') == 'observer-v4':
            upgrade_v4(target, caps)
        else:
            import legacy_upgrade
            legacy_upgrade.upgrade(sys.modules[__name__], target, caps, aggregate_errors)
        return
    old = validate_migration(target)
    if target.get('wordpress'):
        raise RuntimeError('upgrade:wordpress_selection_change_requires_separate_review')
    spec = old['spec']
    if simplify_context and spec['base_variant'] != 'almalinux-wordpress-context-r6':
        raise RuntimeError('cleanup:requires_reviewed_context_v3')
    config, profile_path = Path(spec['config']), Path(spec['profile'])
    profile = load(profile_path)
    paths = [*runtime_files(), BASE / 'manifest.json', BASE / 'complete',
             UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    services = ['duplika-betterstack-identity.timer', 'duplika-betterstack-identity.service',
                NAME + '.timer', NAME + '.service']
    cloud = spec['base_variant'] == 'cloudlinux-v2'
    cleanup_paths = context_cleanup.paths(UNITS) if simplify_context else []
    vector = trusted_executable('vector')
    if not vector:
        raise RuntimeError('upgrade:vector_binary_missing')
    candidate = context_cleanup.prepare(load(config)) if simplify_context else None
    if simplify_context:
        context_cleanup.validate(candidate, vector, execute)
        paths += [config, *cleanup_paths]
        services += ['duplika-vector-context.timer', 'duplika-vector-context.service', spec['vector_service']]
    if cloud:
        if target.get('wordpress'):
            raise RuntimeError('upgrade:wordpress_requires_separate_reviewed_sandbox_change')
        paths += [CLOUD / 'collector.py', profile_path]
        services += ['duplika-cloudlinux-observer.timer', 'duplika-cloudlinux-observer.service']
    # Resolve effective vendor/local units and drop-ins while the reviewed old
    # pipeline is still running. Never discover missing paths after stopping it.
    original, active_services = migration_files(spec, simplify_context)
    transaction = backup(paths, services)
    try:
        execute(['systemctl', 'disable', '--now', 'duplika-betterstack-identity.timer'])
        execute(['systemctl', 'stop', 'duplika-betterstack-identity.service'])
        if simplify_context:
            execute(['systemctl', 'disable', '--now', 'duplika-vector-context.timer'])
            execute(['systemctl', 'stop', 'duplika-vector-context.service'])
            execute(['systemctl', 'stop', spec['vector_service']])
            write(config, identity.canonical(candidate), config.stat().st_mode & 0o777)
            if trusted_executable('restorecon'):
                execute(['restorecon', '-F', str(config)])
            execute(['runuser', '-u', 'vector', '--', 'test', '-r', str(config)])
            execute(['runuser', '-u', 'vector', '--', vector, 'validate', '--no-environment', '--config-json', str(config)])
            for path in cleanup_paths:
                path.unlink()
            execute(['systemctl', 'daemon-reload'])
            execute(['systemctl', 'start', spec['vector_service']])
        if cloud:
            execute(['systemctl', 'stop', 'duplika-cloudlinux-observer.timer', 'duplika-cloudlinux-observer.service'])
            profile.update(capabilities=caps, agent_version=VERSION, wordpress=[])
            write(profile_path, identity.canonical(profile))
            write(CLOUD / 'collector.py', (HERE / 'collector.py').read_bytes(), 0o700)
            execute(['systemctl', 'start', 'duplika-cloudlinux-observer.service'], timeout=60)
            execute(['systemctl', 'start', 'duplika-cloudlinux-observer.timer'])
        variant = 'observer-v4' if cloud else spec['base_variant']
        review = old.get('migration_review')
        if simplify_context:
            variant = 'almalinux-wordpress-minimal-v4'
            review = dict(review or {}, context_cleanup={'cpanel_reader': 'removed', 'routine_cron': 'removed',
                          'relay_and_checkpoints': 'preserved'})
        activate(target, caps, config, profile_path, active_services,
                 [name[:-6] + '.service' for name in active_services if name.endswith('.timer')], original, vector, variant, review)
    except BaseException as error:
        recover_failure(transaction, error)
        raise



def validate_current(target, caps):
    """A repeated upgrade verifies the installed release, never rebaselines drift."""
    meta = load(BASE / 'manifest.json')
    if (meta.get('target') != target or meta.get('capabilities') != caps
            or meta.get('agent_version') != VERSION or meta.get('installer_version') != VERSION
            or identity.read(BASE / 'complete') != (VERSION + '\n').encode()):
        raise RuntimeError('upgrade:current_release_mismatch')
    for path in runtime_files():
        if identity.read(path) != (HERE / path.name).read_bytes():
            raise RuntimeError('upgrade:current_runtime_differs_from_release')
    if meta.get('resource_sampling'):
        if identity.read(CLOUD / 'collector.py') != (HERE / 'collector.py').read_bytes():
            raise RuntimeError('upgrade:current_collector_differs_from_release')
    return identity.status(quiet=True)


def request_publication(target):
    """Run the existing sandboxed publisher. Network failure does not undo a healthy install."""
    try:
        response = run(['systemctl', 'start', NAME + '.service'], timeout=130)
        requested = response.returncode == 0
    except Exception:
        requested = False
    print(json.dumps({'host': target['host'], 'source_id': target['source_id'],
        'installed_version': VERSION, 'identity_publication': 'completed_or_unchanged' if requested else 'pending_hourly_retry',
        'remote_acceptance': 'external_evidence_required',
        'metrics_delivery_verified': False}, indent=2))


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
    p.add_argument('operation', choices=['preflight', 'install', 'upgrade', 'verify', 'status', 'rollback', 'resume', 'configure', 'credential', 'storage-preflight', 'diagnostic-enable', 'diagnostic-disable', 'diagnostic-status'])
    p.add_argument('--target', type=Path, help='Perfil; en upgrade/preflight se usa el destino instalado si se omite')
    p.add_argument('--host')
    p.add_argument('--server-ip')
    p.add_argument('--source-id', type=int)
    p.add_argument('--ingesting-host')
    p.add_argument('--credential', type=Path)
    p.add_argument('--recover-bootstrap', action='store_true',
                   help='Reanudar fallo DNF temprano sin base/usuario/servicios del agente')
    p.add_argument('--recover-partial', action='store_true',
                   help='Recuperar sólo la base rc11 detenida por timeout LVE revisado')
    p.add_argument('--simplify-context', action='store_true',
                   help='Retirar contexto cPanel y cron rutinario sólo de la base context-r6 revisada')
    p.add_argument('--aggregate-errors', action='store_true', help='Agrupar errores repetidos sólo en la base minimal-v4 revisada')
    p.add_argument('--essential-monitoring', action='store_true', help='Migración explícita observer-v4 rc26 a monitoreo esencial')
    p.add_argument('--user', help='Cuenta exacta del diagnóstico temporal')
    p.add_argument('--minutes', type=int, default=30)
    p.add_argument('--readers', nargs='+', choices=['access', 'lve', 'processes', 'wordpress'])
    args = p.parse_args()
    if (args.user or args.readers or args.minutes != 30) and not args.operation.startswith('diagnostic-'):
        p.error('--user/--readers/--minutes sólo se usan con diagnóstico explícito')
    if args.essential_monitoring and (args.operation not in ('preflight', 'upgrade') or args.aggregate_errors or args.simplify_context or args.recover_partial or args.recover_bootstrap):
        p.error('--essential-monitoring sólo admite preflight/upgrade sin otras migraciones')
    if args.aggregate_errors and (args.operation not in ('preflight', 'upgrade') or args.simplify_context or args.recover_partial or args.recover_bootstrap):
        p.error('--aggregate-errors sólo admite preflight/upgrade sin otras migraciones')
    if args.recover_bootstrap and (args.operation not in ('preflight', 'resume') or args.recover_partial or args.simplify_context):
        p.error('--recover-bootstrap sólo admite preflight/resume sin otras migraciones')
    if args.recover_partial and args.operation not in ('preflight', 'resume'):
        p.error('--recover-partial sólo admite preflight/resume')
    if args.recover_partial and args.simplify_context:
        p.error('--recover-partial no se combina con --simplify-context')
    if args.simplify_context and args.operation not in ('preflight', 'upgrade'):
        p.error('--simplify-context sólo admite preflight/upgrade')
    if os.geteuid() != 0:
        p.error('Ejecutar como root.')
    os.umask(0o077)
    lock = os.open('/run/duplika-betterstack-v4.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.operation.startswith('diagnostic-'):
        import essential_upgrade
        essential_upgrade.diagnostic(sys.modules[__name__], args)
        return
    if args.operation == 'storage-preflight':
        if not args.host or args.host not in (socket.gethostname(), socket.getfqdn()):
            raise RuntimeError('storage:explicit_hostname_mismatch')
        args_ = ['systemd-run', '--wait', '--pipe', '--collect']
        for line in STORAGE_SERVICE.splitlines():
            if '=' in line and line.split('=', 1)[0] not in ('Description', 'Type', 'ExecStart', 'ReadWritePaths'):
                args_ += ['-p', line]
        args_ += [sys.executable, '-E', '-s', '-B', str(HERE / 'storage.py'), '--probe']
        response = run(args_, timeout=60)
        print(response.stdout)
        if response.returncode:
            print(response.stderr[-3000:], file=sys.stderr)
            raise RuntimeError('storage:native_preflight_failed')
        return
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
    if args.target is None and args.operation in ('preflight', 'upgrade') and (BASE / 'complete').exists():
        target = load(BASE / 'manifest.json')['target']
    else:
        args.target = args.target or Path('/root/perfil.json')
        target = load(args.target)
    caps = secure_target(target)
    if args.essential_monitoring:
        import essential_upgrade
        essential_upgrade.migrate(sys.modules[__name__], target, caps, apply=args.operation == 'upgrade')
        if args.operation == 'upgrade':
            request_publication(target)
        return
    if args.aggregate_errors:
        import error_aggregation
        if not (BASE / 'complete').exists():
            raise RuntimeError('aggregation:requires_reviewed_minimal_v4')
        old = load(BASE / 'manifest.json')
        if old['agent_version'] == VERSION:
            validate_current(target, caps)
            if old.get('migration_review', {}).get('error_aggregation') != error_aggregation.MARKER:
                raise RuntimeError('aggregation:select_during_version_upgrade')
        else:
            validate_v4_upgrade(target, caps)
        error_aggregation.validate_plan(sys.modules[__name__], old)
    if args.recover_bootstrap:
        validate_bootstrap_recovery(target)
        if args.operation == 'preflight':
            base_preflight(target)
            print(json.dumps({'host': target['host'], 'source_id': target['source_id'],
                'installer_version': VERSION, 'recovery': 'early_dnf_bootstrap',
                'capabilities': caps, 'changes_applied': False}, indent=2))
        else:
            if not args.credential:
                p.error('--credential es obligatorio para reanudar bootstrap')
            # Preserve original failure/marker in a root-only backup; do not erase state.
            backup([STATE / 'install-pending.json', STATE / 'last-error.json'], [])
            fresh(target, caps, args.credential, recover_bootstrap=True)
            request_publication(target)
        return
    if args.recover_partial:
        import partial_recovery
        if args.operation == 'preflight':
            partial_recovery.validate(sys.modules[__name__], target, caps)
            print(json.dumps({'host': target['host'], 'source_id': target['source_id'],
                'installer_version': VERSION, 'recovery_from': '4.0.0-rc11',
                'changes_applied': False, 'lve_timeout_seconds': 40, 'cpu_quota': '10%'}, indent=2))
        else:
            partial_recovery.recover(sys.modules[__name__], target, caps)
            request_publication(target)
        return
    if args.operation == 'credential':
        create_credential(target, args.credential or Path('/root/fuente.json'))
        return
    if args.operation == 'preflight':
        previous = None
        if (BASE / 'complete').exists():
            if load(BASE / 'manifest.json')['target'] != target:
                raise RuntimeError('preflight:target_mismatch')
            if args.simplify_context and load(BASE / 'manifest.json')['pipeline_variant'] != 'almalinux-wordpress-minimal-v4':
                raise RuntimeError('cleanup:requires_reviewed_context_v3')
            previous = load(BASE / 'manifest.json')['agent_version']
            if previous != VERSION:
                validate_v4_upgrade(target, caps)
                identity.status(quiet=True)
            else:
                validate_current(target, caps)
        if LEGACY.exists() and not (BASE / 'complete').exists():
            reviewed = validate_migration(target)
            previous = reviewed['spec']['base_variant']
            migration_files(reviewed['spec'], args.simplify_context)
            if args.simplify_context:
                if previous != 'almalinux-wordpress-context-r6':
                    raise RuntimeError('cleanup:requires_reviewed_context_v3')
                context_cleanup.validate(context_cleanup.prepare(load('/etc/vector/vector.yaml')),
                                         trusted_executable('vector'), execute)
        elif not (BASE / 'complete').exists():
            for path in (CLOUD, CLOUD_DATA):
                if path.exists() or path.is_symlink():
                    raise RuntimeError('preflight:partial_or_unknown_installation')
        if previous is None and not (BASE / 'complete').exists():
            if args.simplify_context:
                raise RuntimeError('cleanup:requires_reviewed_context_v3')
            if caps['os_id'] == 'almalinux' and caps['os_version'].split('.')[0] == '8':
                raise RuntimeError('install:almalinux8_reviewed_migration_only')
            base_preflight(target)
        print(json.dumps({'host': target['host'], 'source_id': target['source_id'],
            'capabilities': caps, 'migration_from': previous, 'installer_version': VERSION,
            'changes_applied': False}, indent=2))
        if args.simplify_context:
            print('Plan revisado: retirar lector cPanel y cron rutinario; conservar errores, métricas, relay y checkpoints.')
        return
    mkdir(BASE); mkdir(STATE)
    if args.operation in ('install', 'resume') and (BASE / 'complete').exists():
        if load(BASE / 'manifest.json')['target'] != target:
            raise RuntimeError('install:target_mismatch')
        if load(BASE / 'manifest.json').get('agent_version') != VERSION:
            raise RuntimeError('install:older_version_use_upgrade')
        validate_current(target, caps)
        print('Ya instalado y verificado; sin cambios.')
        return
    if args.operation == 'resume':
        finish_install(target, caps)
    elif args.operation == 'install':
        if not args.credential:
            p.error('--credential es obligatorio para instalación nueva')
        fresh(target, caps, args.credential)
    else:
        upgrade(target, caps, args.simplify_context, args.aggregate_errors)
    request_publication(target)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
