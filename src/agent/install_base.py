#!/usr/bin/env python3
"""Install a dedicated CloudLinux observer; never change web/PHP/MySQL settings."""
import argparse
import datetime
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import time
from config import make_config
from capabilities import detect, parse_json, VERSION, run as probe_run

HERE = Path(__file__).resolve().parent
BASE = Path('/etc/duplika-betterstack-cloudlinux')
ROOT = Path('/var/lib/duplika-betterstack-cloudlinux')
UNITS = Path('/etc/systemd/system')
READER = 'duplikaobs'
SERVICE = 'duplika-betterstack-cloudlinux'
COLLECTOR = 'duplika-cloudlinux-observer'
DEPLOYMENT = json.loads((HERE / 'deployment.json').read_text())
TARGET = DEPLOYMENT['server_ip']

def run(args, check=True, timeout=120):
    p = probe_run(args, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError('command_failed:' + Path(args[0]).name + ':exit=' + str(p.returncode))
    return p


def write(path, text, mode=0o600, gid=0):
    if path.is_symlink():
        raise RuntimeError('Ruta redirigida: ' + str(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
        os.fchown(f.fileno(), 0, gid)
        os.fchmod(f.fileno(), mode)

def directory(path, uid=0, gid=0, mode=0o750):
    if path.is_symlink() or path.resolve() != path:
        raise RuntimeError('Directorio redirigido: ' + str(path))
    path.mkdir(parents=False, exist_ok=True)
    os.chown(path, uid, gid)
    path.chmod(mode)

def trusted_executable(name):
    value = shutil.which(name)
    if not value:
        return None
    path = Path(value).resolve()
    s = path.stat()
    if s.st_uid != 0 or s.st_mode & 0o022:
        raise RuntimeError('Ejecutable sin propiedad segura: ' + name)
    return str(path)

def preflight():
    if os.geteuid() != 0:
        raise RuntimeError('Ejecutar como root.')
    osinfo = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    distro = osinfo.get('ID', '').strip('"\'').lower()
    version = osinfo.get('VERSION_ID', '').strip('"\'')
    capabilities = detect()
    if DEPLOYMENT['host'] not in (socket.gethostname(), socket.getfqdn()):
        raise RuntimeError('Hostname diferente al paquete.')
    if not Path('/var/cpanel/users').is_dir() or not Path('/etc/userdomains').is_file():
        raise RuntimeError('No se detectó cPanel y su mapa /etc/userdomains.')
    ip = trusted_executable('ip')
    if not ip:
        raise RuntimeError('No se encontró iproute.')
    addresses = parse_json(run([ip, '-j', 'address', 'show']), 'ip_addresses')
    locals_ = {a['local'] for interface in addresses for a in interface.get('addr_info', [])}
    if TARGET not in locals_:
        raise RuntimeError('La IP del paquete no está asignada a este servidor. No se hicieron cambios.')
    for path in (BASE, ROOT, UNITS / (SERVICE + '.service'), UNITS / (COLLECTOR + '.service'),
                 UNITS / (COLLECTOR + '.timer')):
        if path.exists() or path.is_symlink():
            raise RuntimeError('Ya existe ' + str(path) + '. No se reemplaza automáticamente.')
    try:
        pwd.getpwnam(READER)
    except KeyError:
        pass
    else:
        raise RuntimeError('Ya existe el usuario reservado ' + READER + '. Revisar antes de instalar.')
    roots = set()
    for candidate in ('/var/log/apache2/domlogs', '/usr/local/apache/domlogs', '/etc/apache2/logs/domlogs'):
        p = Path(candidate)
        if p.is_dir():
            p = p.resolve()
            if p.stat().st_uid != 0:
                raise RuntimeError('Directorio domlogs no administrado por root: ' + str(p))
            roots.add(str(p))
    if not roots:
        raise RuntimeError('No se encontraron domlogs de cPanel; revisar las rutas antes de instalar.')
    web_logs = sorted({str(Path(p).resolve()) for p in (
        '/var/log/apache2/error_log', '/var/log/apache2/stderr.log',
        '/usr/local/apache/logs/error_log', '/usr/local/apache/logs/stderr.log') if Path(p).is_file()})
    cloudtop = capabilities['cloudlinux_top']
    Path('/proc/stat').read_text()
    Path('/proc/self/stat').read_text()
    account_count = len([p for p in Path('/var/cpanel/users').iterdir() if p.is_file()])
    profile = {'host': socket.getfqdn(), 'server_ip': TARGET, 'cloudlinux_version': version,
        'installation_id': 'cloudlinux-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
        'domlog_roots': sorted(roots), 'error_logs': web_logs, 'cloudlinux_top': cloudtop,
        'isolates_supported': capabilities['isolates_supported'], 'mysql_governor': capabilities['mysql_governor'],
        'capabilities': capabilities, 'agent_version': VERSION, 'wordpress': DEPLOYMENT.get('wordpress', []),
        'interval_seconds': 60, 'account_count': account_count}
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    print('Preflight correcto. No se leyó ningún wp-config.php ni log de una aplicación.')
    return profile

def verify():
    for name in (SERVICE + '.service', COLLECTOR + '.timer'):
        p = run(['systemctl', 'is-active', name], check=False)
        print(name + ': ' + p.stdout.strip())
        if p.returncode:
            raise RuntimeError('Servicio de monitoreo inactivo: ' + name)
    result = run(['systemctl', 'show', COLLECTOR + '.service', '-p', 'Result', '-p', 'ExecMainStatus']).stdout
    print(result.strip())
    if 'Result=success' not in result or 'ExecMainStatus=0' not in result:
        raise RuntimeError('El último ciclo del colector no terminó correctamente.')
    profile = json.loads((BASE / 'profile.json').read_text())
    print('Source ID: ' + str(DEPLOYMENT['source_id']) + '; marcador: ' + profile['installation_id'])
    print('Recepción remota: confirmar collector_heartbeat y métricas en Better Stack.')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if args.verify:
        verify()
        return
    profile = preflight()
    if args.preflight:
        return
    creds = json.loads((HERE / 'credentials.json').read_text())
    if creds['source_id'] != DEPLOYMENT['source_id'] or creds['host'] != DEPLOYMENT['ingesting_host']:
        raise RuntimeError('Destino de ingesta inesperado.')
    vector = trusted_executable('vector')
    if not vector:
        # Official signed RPM repository, without a system-wide upgrade.
        run(['dnf', '-y', 'install', 'ca-certificates'])
        if not shutil.which('curl'):
            run(['dnf', '-y', 'install', 'curl'])
        repo = HERE / 'vector-repo.sh'
        run(['curl', '--proto', '=https', '--tlsv1.2', '-fsSL', '--connect-timeout', '15',
             '--max-time', '120', 'https://setup.vector.dev', '-o', str(repo)])
        run(['bash', str(repo)], timeout=180)
        run(['dnf', '-y', 'install', 'vector'], timeout=300)
        # Only the newly installed vendor service; existing Vector installations are untouched.
        run(['systemctl', 'disable', '--now', 'vector.service'], check=False)
        vector = trusted_executable('vector')
    run(['useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent',
         '--shell', '/sbin/nologin', '--user-group', READER])
    user = pwd.getpwnam(READER)
    profile['reader_gid'] = user.pw_gid
    profile['vector_binary'] = vector
    directory(BASE, gid=user.pw_gid)
    directory(ROOT, gid=user.pw_gid)
    directory(ROOT / 'private', mode=0o700)
    directory(ROOT / 'spool', gid=user.pw_gid)
    directory(ROOT / 'vector', uid=user.pw_uid, gid=user.pw_gid)
    write(BASE / 'profile.json', json.dumps(profile, indent=2))
    write(BASE / 'collector.py', (HERE / 'collector.py').read_text(), 0o700)
    write(BASE / 'vector.json', json.dumps(make_config(profile, creds['token'], creds['host']), indent=2),
          0o640, user.pw_gid)
    rollback = '''#!/bin/bash
set -euo pipefail
systemctl disable --now duplika-cloudlinux-observer.timer duplika-betterstack-cloudlinux.service
systemctl stop duplika-cloudlinux-observer.service
echo 'Monitoreo detenido. Configuración y datos conservados; sitios y bases de datos sin cambios.'
'''
    write(BASE / 'desactivar.sh', rollback, 0o700)
    try:
        run([vector, 'test', '--config-json', str(BASE / 'vector.json')])
        write(UNITS / (SERVICE + '.service'), '''[Unit]
Description=Duplika Better Stack - CloudLinux transport
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
User=duplikaobs
Group=duplikaobs
ExecStart=__VECTOR__ --config-json /etc/duplika-betterstack-cloudlinux/vector.json
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
UMask=0027
Nice=10
CPUAccounting=true
MemoryAccounting=true
CPUQuota=20%
MemoryMax=384M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/duplika-betterstack-cloudlinux/vector

[Install]
WantedBy=multi-user.target
'''.replace('__VECTOR__', vector), 0o644)
        collector_unit = '''[Unit]
Description=Duplika CloudLinux - read-only account and process observer

[Service]
Type=oneshot
ExecStart=__PYTHON__ -E -s -B /etc/duplika-betterstack-cloudlinux/collector.py
TimeoutStartSec=50
UMask=0027
Nice=15
CPUAccounting=true
MemoryAccounting=true
IOSchedulingClass=idle
CPUQuota=10%
MemoryMax=256M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/duplika-betterstack-cloudlinux/private /var/lib/duplika-betterstack-cloudlinux/spool
'''.replace('__PYTHON__', sys.executable)
        if profile['wordpress']:
            collector_unit = collector_unit.replace('ProtectHome=true', 'ProtectHome=tmpfs\n' + ''.join(
                'BindReadOnlyPaths=' + site['docroot'] + '\n' for site in profile['wordpress']))
        write(UNITS / (COLLECTOR + '.service'), collector_unit, 0o644)
        write(UNITS / (COLLECTOR + '.timer'), '''[Unit]
Description=Duplika CloudLinux - sample every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=2s
Unit=duplika-cloudlinux-observer.service

[Install]
WantedBy=timers.target
''', 0o644)
        if shutil.which('restorecon'):
            run(['restorecon', '-RF', str(BASE), str(ROOT)])
        run(['systemd-analyze', 'verify', str(UNITS / (SERVICE + '.service')),
             str(UNITS / (COLLECTOR + '.service')), str(UNITS / (COLLECTOR + '.timer'))])
        run(['systemctl', 'daemon-reload'])
        # Establish cursors at EOF: historical access/error logs are not uploaded.
        run(['systemctl', 'start', COLLECTOR + '.service'], timeout=60)
        events = (ROOT / 'spool' / 'events.jsonl').read_text().splitlines()
        heartbeat = next(json.loads(row) for row in reversed(events)
                         if json.loads(row).get('event_kind') == 'collector_heartbeat')
        expected_lve = True if profile['capabilities']['lve_status'] == 'available' else 'not_supported'
        if heartbeat['components'].get('lve') != expected_lve or heartbeat['components'].get('processes') == 'failed':
            raise RuntimeError('No se pudo medir procesos/LVE bajo systemd. Revisar journal antes de continuar.')
        access = heartbeat['components'].get('access')
        if not isinstance(access, dict) or access.get('files', 0) == 0:
            raise RuntimeError('No se pudo leer ningún domlog de los dominios cPanel bajo systemd.')
        run(['runuser', '-u', READER, '--', vector, 'validate', '--config-json', str(BASE / 'vector.json')])
        run(['systemctl', 'enable', '--now', SERVICE + '.service', COLLECTOR + '.timer'])
        time.sleep(3)
        verify()
        write(BASE / 'complete', profile['installation_id'] + '\n')
        print('Instalación local completada. A los 2-3 minutos habrá deltas de procesos y tráfico nuevo.')
        print('Desactivar: bash /etc/duplika-betterstack-cloudlinux/desactivar.sh')
    except BaseException:
        for unit in (COLLECTOR + '.timer', COLLECTOR + '.service', SERVICE + '.service'):
            run(['systemctl', 'stop', unit], check=False)
        for unit in (COLLECTOR + '.timer', SERVICE + '.service'):
            run(['systemctl', 'disable', unit], check=False)
        print('Instalación detenida. Archivos conservados para diagnóstico; no se tocaron sitios.')
        raise

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)

