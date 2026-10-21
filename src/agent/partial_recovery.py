"""Explicit recovery of the reviewed rc11 LVE-timeout partial installation only."""
import hashlib
import json
from pathlib import Path
import pwd
import re
import sys

from config import make_config

TRANSPORT = 'duplika-betterstack-cloudlinux.service'
OBSERVER = 'duplika-cloudlinux-observer.service'
TIMER = 'duplika-cloudlinux-observer.timer'


def heartbeat(api):
    records = [json.loads(line) for line in api.identity.read(
        api.CLOUD_DATA / 'spool/events.jsonl').splitlines() if line.strip()]
    beats = [row for row in records if row.get('event_kind') == 'collector_heartbeat']
    if not beats:
        raise RuntimeError('recovery:heartbeat_missing')
    return beats[-1], records


def validate(api, target, caps):
    """No writes, starts, baseline changes, credential collection or package installs."""
    for path in (api.BASE / 'complete', api.BASE / 'manifest.json', api.CLOUD / 'complete', api.LEGACY):
        if path.exists() or path.is_symlink():
            raise RuntimeError('recovery:requires_incomplete_rc11_base')
    pending = api.load(api.STATE / 'install-pending.json')
    if pending != {'phase': 'base_installing', 'target': target}:
        raise RuntimeError('recovery:pending_target_or_phase')
    failure = api.load(api.STATE / 'last-error.json')
    if (failure.get('stage') != 'install' or failure.get('exit_code') != 1
            or 'No se pudo medir procesos/LVE bajo systemd.' not in failure.get('diagnostic', '')):
        raise RuntimeError('recovery:unreviewed_failure')
    profile = api.load(api.CLOUD / 'profile.json')
    expected_fields = {'host', 'server_ip', 'cloudlinux_version', 'installation_id', 'domlog_roots',
        'error_logs', 'cloudlinux_top', 'isolates_supported', 'mysql_governor', 'capabilities',
        'agent_version', 'wordpress', 'interval_seconds', 'account_count', 'reader_gid', 'vector_binary'}
    reader = pwd.getpwnam('duplikaobs')
    vector = api.trusted_executable('vector')
    if (set(profile) != expected_fields or profile['agent_version'] != '4.0.0-rc11'
            or profile['host'] != target['host'] or profile['server_ip'] != target['server_ip']
            or profile['capabilities'] != caps or caps.get('lve_status') != 'available'
            or profile['cloudlinux_version'] != caps['os_version']
            or profile['cloudlinux_top'] != caps['cloudlinux_top']
            or profile['isolates_supported'] != caps['isolates_supported']
            or profile['mysql_governor'] != caps['mysql_governor']
            or profile['wordpress'] or target.get('wordpress')
            or profile['interval_seconds'] != 60 or type(profile['account_count']) is not int
            or profile['account_count'] <= 0 or profile['reader_gid'] != reader.pw_gid
            or not vector or profile['vector_binary'] != str(Path(vector).resolve())
            or not re.fullmatch(r'cloudlinux-[0-9]{8}T[0-9]{6}Z', profile['installation_id'])):
        raise RuntimeError('recovery:profile_not_reviewed')
    if not profile['domlog_roots']:
        raise RuntimeError('recovery:domlogs_missing')
    for value in profile['domlog_roots']:
        path = Path(value)
        if (not path.is_absolute() or path.resolve() != path or not path.is_dir()
                or path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
            raise RuntimeError('recovery:unsafe_domlogs')
    config = api.load(api.CLOUD / 'vector.json')
    _, token = api.identity.destination(config, target)
    if config != make_config(profile, token, target['ingesting_host']):
        raise RuntimeError('recovery:transport_changed')
    reference = api.load(api.HERE / 'recovery_rc11.json')
    if hashlib.sha256(api.identity.read(api.CLOUD / 'collector.py')).hexdigest() != reference['collector_sha256']:
        raise RuntimeError('recovery:collector_changed')
    for name, template in reference['units'].items():
        unit = api.UNITS / name
        expected = template.replace('__VECTOR__', profile['vector_binary']).replace('__PYTHON__', sys.executable)
        if api.identity.read(unit) != expected.encode() or api.unit_files(name) != [unit]:
            raise RuntimeError('recovery:unit_changed:' + name)
        enablement = api.run(['systemctl', 'is-enabled', name]).stdout.strip()
        allowed = ('static',) if name == OBSERVER else ('disabled',)
        if (api.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0
                or enablement not in allowed):
            raise RuntimeError('recovery:units_must_be_stopped_disabled')
    for path, mode, gid in ((api.CLOUD / 'vector.json', 0o640, reader.pw_gid),
                            (api.CLOUD / 'profile.json', 0o600, 0),
                            (api.CLOUD / 'collector.py', 0o700, 0)):
        info = path.stat()
        if info.st_mode & 0o777 != mode or info.st_gid != gid:
            raise RuntimeError('recovery:unexpected_permissions')
    beat, records = heartbeat(api)
    components = beat.get('components', {})
    if (beat.get('installation_id') != profile['installation_id']
            or beat.get('host') != target['host'] or beat.get('server_ip') != target['server_ip']
            or components.get('lve') != 'failed' or type(components.get('processes')) is not int
            or any(value == 'failed' for key, value in components.items() if key != 'lve')
            or not isinstance(components.get('access'), dict) or not components['access'].get('files')
            or not any(row.get('event_kind') == 'collector_error' and row.get('component') == 'lve'
                and row.get('error_class') == 'TimeoutExpired' and row.get('dt') == beat.get('dt')
                for row in records)):
        raise RuntimeError('recovery:not_reviewed_lve_timeout')
    return profile, reference


def recover(api, target, caps):
    profile, reference = validate(api, target, caps)
    paths = [*api.observer_base_paths(), api.CLOUD / 'complete', api.STATE / 'install-pending.json',
        *api.runtime_files(), api.BASE / 'manifest.json', api.BASE / 'complete',
        api.UNITS / (api.NAME + '.service'), api.UNITS / (api.NAME + '.timer')]
    record = api.backup(paths, [TRANSPORT, OBSERVER, TIMER, api.NAME + '.service', api.NAME + '.timer'])
    try:
        # Transport, credential, cursors, spool and private state remain untouched.
        api.execute([profile['vector_binary'], 'test', '--config-json', str(api.CLOUD / 'vector.json')])
        api.execute(['runuser', '-u', 'duplikaobs', '--', profile['vector_binary'],
                     'validate', '--config-json', str(api.CLOUD / 'vector.json')])
        api.write(api.CLOUD / 'collector.py', (api.HERE / 'collector.py').read_bytes(), 0o700)
        profile['agent_version'] = api.VERSION
        api.write(api.CLOUD / 'profile.json', api.identity.canonical(profile))
        unit = reference['units'][OBSERVER].replace('__PYTHON__', sys.executable)
        unit = unit.replace('TimeoutStartSec=50', 'TimeoutStartSec=55')
        api.write(api.UNITS / OBSERVER, unit, 0o644)
        if api.trusted_executable('restorecon'):
            api.execute(['restorecon', '-F', str(api.CLOUD / 'collector.py'),
                         str(api.CLOUD / 'profile.json'), str(api.UNITS / OBSERVER)])
        api.execute(['systemd-analyze', 'verify', str(api.UNITS / OBSERVER)])
        api.execute(['systemctl', 'daemon-reload'])
        api.execute(['systemctl', 'start', OBSERVER], timeout=65)
        beat, _ = heartbeat(api)
        components = beat.get('components', {})
        if (beat.get('installation_id') != profile['installation_id']
                or components.get('lve') is not True or type(components.get('processes')) is not int
                or 'failed' in components.values()
                or not isinstance(components.get('access'), dict) or not components['access'].get('files')):
            raise RuntimeError('recovery:collector_native_check_failed')
        api.execute(['systemctl', 'enable', '--now', TRANSPORT, TIMER])
        for name in (TRANSPORT, TIMER):
            api.execute(['systemctl', 'is-active', '--quiet', name])
        api.write(api.CLOUD / 'complete', profile['installation_id'] + '\n')
        base_paths = list(map(str, api.observer_base_paths()))
        api.write(api.STATE / 'install-pending.json', api.identity.canonical({
            'phase': 'base_complete', 'target': target, 'paths': base_paths,
            'baseline': api.identity.snapshot(base_paths, str(api.CLOUD / 'vector.json'))}))
        api.finish_install(target, caps)
    except BaseException as error:
        api.recover_failure(record, error)
        raise
