#!/usr/bin/env python3
"""Duplika identity protocol 1. Python 3.6+, no application access.

An identity attests configuration/code, not successful delivery of metrics.
No credential, command line, unit text or profile contents leave this process.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request

BASE = Path('/etc/duplika-betterstack-identity')
STATE = Path('/var/lib/duplika-betterstack-identity')
UNITS = Path('/etc/systemd/system')
NAME = 'duplika-betterstack-identity'
HERE = Path(__file__).resolve().parent
MAX_BYTES = 4 * 1024 * 1024


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def secure(path):
    path = Path(path)
    if path.resolve() != path or path.is_symlink():
        raise RuntimeError('Ruta redirigida: ' + str(path))
    for p in [path] + list(path.parents):
        s = p.stat()
        if s.st_uid != 0 or s.st_mode & 0o022:
            raise RuntimeError('Propiedad o permisos inseguros: ' + str(p))
    return path


def read(path):
    path = secure(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise RuntimeError('Archivo no regular o demasiado grande: ' + str(path))
    return path.read_bytes()


def load(path):
    return json.loads(read(path).decode())


def run(args, check=True):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=30)
    if check and p.returncode:
        raise RuntimeError('Falló una comprobación de ' + args[0] + '; no se expone su salida privada.')
    return p


def atomic(path, data, mode=0o600):
    path = Path(path)
    secure(path.parent)
    if path.exists() or path.is_symlink():
        secure(path)
    temp = path.with_name('.' + path.name + '.tmp')
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def config_hash(config):
    # Tests are inactive. Rotation of a source token does not change the pipeline.
    cfg = json.loads(json.dumps(config))
    cfg.pop('tests', None)
    for sink in cfg.get('sinks', {}).values():
        if 'token' in sink.get('auth', {}):
            sink['auth']['token'] = '<SOURCE_TOKEN>'
    return sha(canonical(cfg))


def destination(config, target):
    sinks = config.get('sinks', {})
    tokens = set()
    paths = set()
    expected = 'https://' + target['ingesting_host']
    if not sinks:
        raise RuntimeError('No hay destinos de ingesta.')
    for sink in sinks.values():
        if sink.get('uri') not in (expected + '/', expected + '/metrics'):
            raise RuntimeError('Fuente/destino diferente al paquete; no se envían datos.')
        if sink.get('auth', {}).get('strategy') != 'bearer':
            raise RuntimeError('Autenticación no reconocida.')
        tokens.add(sink['auth'].get('token', ''))
        paths.add(sink['uri'][len(expected):])
    if len(tokens) != 1 or paths != {'/', '/metrics'}:
        raise RuntimeError('Destinos/credenciales ambiguos.')
    token = next(iter(tokens))
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', token):
        raise RuntimeError('Credencial inválida; no se muestra.')
    return expected + '/', token


def system_snapshot(spec):
    units = {}
    statuses = {}
    names = spec['services'] + [s[:-6] + '.service' for s in spec['services'] if s.endswith('.timer')]
    for name in names:
        text = run(['systemctl', 'cat', name]).stdout
        props = run(['systemctl', 'show', name, '-p', 'ExecStart', '-p', 'User',
                     '-p', 'Group', '-p', 'Environment', '-p', 'EnvironmentFiles']).stdout
        if name in spec.get('expected_commands', {}):
            check_command(props, spec['expected_commands'][name])
        # ExecStart show contains PID/start-time/result fields: never hash that
        # volatile text. systemctl cat includes the unit and all effective drop-ins.
        units[name] = sha(text.encode())
        if name in spec['services']:
            statuses[name] = run(['systemctl', 'is-active', '--quiet', name], False).returncode == 0
        else:
            status = run(['systemctl', 'show', name, '-p', 'Result', '--value']).stdout.strip()
            statuses[name + ':last_result'] = status == 'success'
    # Confirm the Vector process is using the configuration being measured.
    props = run(['systemctl', 'show', spec['vector_service'], '-p', 'ExecStart',
                 '-p', 'Environment', '-p', 'EnvironmentFiles']).stdout
    if '--config-dir' in props or 'VECTOR_CONFIG' in props:
        raise RuntimeError('Configuración adicional de Vector no admitida.')
    envfiles = re.search(r'^EnvironmentFiles=(.*)$', props, re.M)
    if envfiles and envfiles.group(1).strip():
        for path in re.findall(r'(/[^\s;]+)', envfiles.group(1)):
            if Path(path).exists():
                content = read(path)
                if b'VECTOR_CONFIG' in content:
                    raise RuntimeError('EnvironmentFiles modifica la configuración de Vector.')
                units['environment:' + path] = sha(content)
    args = re.findall(r'--config(?:-yaml|-json|-toml)?(?:=|\s+)([^\s;]+)|(?:^|\s)-c\s+([^\s;]+)', props)
    paths = [a or b for a, b in args]
    # Vendor vector.service uses its documented default /etc/vector/vector.yaml.
    if paths != [spec['config']] and not (not paths and spec['config'] == '/etc/vector/vector.yaml'):
        raise RuntimeError('Vector no usa la configuración esperada.')
    pid = run(['systemctl', 'show', spec['vector_service'], '-p', 'MainPID', '--value']).stdout.strip()
    if not re.fullmatch(r'[1-9][0-9]*', pid):
        raise RuntimeError('Vector no tiene un proceso activo.')
    # Inspect only our own monitor process, never hosted PHP/application arguments.
    command = (Path('/proc') / pid / 'cmdline').read_bytes().decode().split('\x00')
    running_paths = []
    for i, arg in enumerate(command):
        if arg.startswith('--config-dir'):
            raise RuntimeError('Vector ejecuta un directorio adicional de configuración.')
        if arg in ('--config', '--config-yaml', '--config-json', '--config-toml', '-c'):
            running_paths.append(command[i + 1])
        elif re.match(r'^--config(?:-yaml|-json|-toml)?=', arg):
            running_paths.append(arg.split('=', 1)[1])
    if running_paths != [spec['config']] and not (not running_paths and spec['config'] == '/etc/vector/vector.yaml'):
        raise RuntimeError('El proceso activo usa otra configuración.')
    executable = Path('/proc') / pid / 'exe'
    h = hashlib.sha256()
    size = 0
    with executable.open('rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 512 * 1024 * 1024:
                raise RuntimeError('Binario inesperadamente grande.')
            h.update(chunk)
    return units, statuses, h.hexdigest()


def check_command(properties, expected):
    matches = re.findall(r'argv\[\]=(.*?);', properties)
    if len(matches) != 1 or shlex.split(matches[0].strip()) != expected:
        raise RuntimeError('Un auxiliar ejecuta otro código o argumentos; requiere revisión.')


def observe(spec):
    target = spec['target']
    if target['host'] not in (socket.gethostname(), socket.getfqdn()):
        raise RuntimeError('Hostname diferente al paquete.')
    config = load(spec['config'])
    uri, token = destination(config, target)
    units, health, vector_sha = system_snapshot(spec)
    snapshot = {
        'config_sha256': config_hash(config),
        'profile_sha256': sha(canonical(load(spec['profile']))),
        'files': {p: sha(read(p)) for p in spec['runtime_files']},
        'units': units, 'vector_running_binary_sha256': vector_sha,
    }
    return snapshot, health, uri, token


def expected_config(spec):
    module = importlib.util.spec_from_file_location('duplika_blueprint', str(HERE / 'blueprint.py'))
    blueprint = importlib.util.module_from_spec(module)
    module.loader.exec_module(blueprint)
    profile = load(spec['profile'])
    t = spec['target']
    if profile.get('host') != t['host']:
        raise RuntimeError('Perfil de otro servidor.')
    if 'expected_site' in t:
        if profile.get('site') != t['expected_site'] or Path(profile['docroot']).parts[2] != t['cpanel_user']:
            raise RuntimeError('Cuenta/sitio diferente al paquete.')
    if spec['base_variant'] == 'cloudlinux-v2' and profile.get('server_ip') != t['server_ip']:
        raise RuntimeError('IP del perfil diferente.')
    actual = load(spec['config'])
    uri, token = destination(actual, t)
    generated = blueprint.make_config(profile, token, t['ingesting_host'])
    if config_hash(actual) != config_hash(generated):
        raise RuntimeError('La configuración no coincide con la base revisada. No se certifica ni reemplaza.')
    for path, expected in spec['known_files'].items():
        if sha(read(path)) != expected:
            raise RuntimeError('Código instalado distinto de la referencia: ' + path)


def identity_event(meta, snapshot, health):
    t = meta['spec']['target']
    drift = snapshot != meta['baseline']
    return {
        'message': 'Duplika collector identity', 'event_kind': 'collector_identity',
        'identity_schema': 1, 'template_id': t['template_id'], 'template_version': t['template_version'],
        'base_variant': meta['spec']['base_variant'], 'extensions': meta['spec']['extensions'],
        'host': t['host'], 'server_ip': t['server_ip'], 'source_id': t['source_id'],
        'package_sha256': meta['package_sha256'], 'template_code_sha256': meta['spec']['template_code_sha256'],
        'config_sha256': snapshot['config_sha256'], 'runtime_sha256': sha(canonical(snapshot)),
        'expected_runtime_sha256': sha(canonical(meta['baseline'])),
        'vector_binary_sha256': snapshot['vector_running_binary_sha256'],
        'drift_detected': drift, 'services_healthy': all(health.values()),
        'inactive_services': [k for k, v in health.items() if not v],
        'version_status': 'drift' if drift else 'matches_reviewed_baseline',
        'metrics_delivery_verified': False,
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(uri, token, event):
    # The only network destination is the source already validated above. No redirects/proxies.
    req = urllib.request.Request(uri, data=canonical(event), method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=15) as response:
            if response.status != 202:
                raise RuntimeError('Respuesta de ingesta inesperada.')
    except Exception:
        raise RuntimeError('No se confirmó la ingesta de identidad; se reintentará en el próximo ciclo.') from None


def check(force=False, publish=False):
    meta = load(BASE / 'manifest.json')
    snapshot, health, uri, token = observe(meta['spec'])
    event = identity_event(meta, snapshot, health)
    current_hash = sha(canonical(event))
    previous = load(STATE / 'last-send.json') if (STATE / 'last-send.json').exists() else {}
    due = force or current_hash != previous.get('hash') or time.time() - previous.get('at', 0) >= 3600
    if publish and due:
        event['dt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        send(uri, token, event)
        atomic(STATE / 'last-send.json', canonical({'at': time.time(), 'hash': current_hash, 'event': event}))
    print(json.dumps({'host': event['host'], 'version': event['template_version'],
                      'status': event['version_status'], 'services_healthy': event['services_healthy'],
                      'runtime_sha256': event['runtime_sha256'], 'identity_sent': publish and due}, indent=2))
    if event['drift_detected'] or not event['services_healthy']:
        raise RuntimeError('Se detectó un cambio o un servicio inactivo; no se recertificó la base.')


def enroll(args):
    spec = load(HERE / 'spec.json')
    if BASE.exists() or BASE.is_symlink():
        if not (BASE / 'complete').exists():
            raise RuntimeError('Identidad parcial; revisar el fallo antes de repetir.')
        existing = load(BASE / 'manifest.json')
        if existing['spec']['target'] != spec['target'] or existing['spec']['template_code_sha256'] != spec['template_code_sha256']:
            raise RuntimeError('Ya existe otra identidad; requiere una actualización explícita.')
        check()
        run(['systemctl', 'is-active', '--quiet', NAME + '.timer'])
        return
    if STATE.exists() or STATE.is_symlink() or any((UNITS / (NAME + suffix)).exists() for suffix in ('.service', '.timer')):
        raise RuntimeError('Hay un despliegue de identidad parcial; revisar antes de repetir.')
    osinfo = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    distro = osinfo.get('ID', '').strip('\"\'')
    major = osinfo.get('VERSION_ID', '').strip('\"\'').split('.')[0]
    allowed = spec['allowed_os']
    if distro not in allowed or major not in allowed[distro]:
        raise RuntimeError('Distribución/versión fuera del perfil autorizado.')
    addresses = json.loads(run(['ip', '-j', 'address', 'show']).stdout)
    ips = {a['local'] for row in addresses for a in row.get('addr_info', [])}
    if spec['target']['server_ip'] not in ips:
        raise RuntimeError('La IP del paquete no está asignada al servidor.')
    if not Path('/var/cpanel/users').is_dir():
        raise RuntimeError('cPanel no detectado.')
    expected_config(spec)
    snapshot, health, _, _ = observe(spec)
    if not all(health.values()):
        raise RuntimeError('Un servicio de monitoreo está inactivo.')
    print('Base y archivos conocidos coinciden; preflight de identidad correcto.')
    if args.preflight:
        return
    if not re.fullmatch(r'[a-f0-9]{64}', args.package_sha256 or ''):
        raise RuntimeError('Falta el hash del paquete ejecutado.')
    for directory in (BASE, STATE):
        secure(directory.parent)
        directory.mkdir(mode=0o700)
    rollback = ('#!/bin/bash\nset -eu\nsystemctl disable --now ' + NAME + '.timer || true\n'
                'systemctl stop ' + NAME + '.service || true\n'
                'echo "Identidad detenida. Colectores, métricas, archivos y cursores conservados."\n')
    try:
        atomic(BASE / 'identity.py', read(HERE / 'identity.py'), 0o700)
        atomic(BASE / 'desactivar.sh', rollback.encode(), 0o700)
        spec['runtime_files'] = list(spec['runtime_files']) + [str(BASE / 'identity.py')]
        snapshot['files'][str(BASE / 'identity.py')] = sha(read(BASE / 'identity.py'))
        service = '''[Unit]
Description=Duplika Better Stack identity and drift check
After=network-online.target
[Service]
Type=oneshot
User=root
UMask=0077
ExecStart=/usr/bin/python3 /etc/duplika-betterstack-identity/identity.py --publish
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/duplika-betterstack-identity
Nice=15
CPUQuota=10%
MemoryMax=128M
TimeoutStartSec=90
'''
        timer = '''[Unit]
Description=Check Duplika identity every 15 minutes; publish hourly or on change
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
AccuracySec=10s
[Install]
WantedBy=timers.target
'''
        atomic(UNITS / (NAME + '.service'), service.encode(), 0o644)
        atomic(UNITS / (NAME + '.timer'), timer.encode(), 0o644)
        for suffix in ('.service', '.timer'):
            path = str(UNITS / (NAME + suffix))
            spec['runtime_files'].append(path)
            snapshot['files'][path] = sha(read(path))
        atomic(BASE / 'manifest.json', canonical({'spec': spec, 'baseline': snapshot,
               'package_sha256': args.package_sha256,
               'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}))
        if shutil.which('restorecon'):
            run(['restorecon', '-RF', str(BASE), str(STATE)])
        run(['systemctl', 'daemon-reload'])
        # Real sandbox/TLS execution must succeed before enabling the timer.
        run(['systemctl', 'start', NAME + '.service'])
        run(['systemctl', 'enable', '--now', NAME + '.timer'])
        atomic(BASE / 'complete', b'identity-protocol-1\n')
    except BaseException:
        run(['systemctl', 'disable', '--now', NAME + '.timer'], False)
        run(['systemctl', 'stop', NAME + '.service'], False)
        # Only this attempt's new files; no pre-existing monitoring state is touched.
        for suffix in ('.service', '.timer'):
            path = UNITS / (NAME + suffix)
            if path.exists():
                path.unlink()
        for directory in (BASE, STATE):
            if directory.exists():
                shutil.rmtree(str(directory))
        run(['systemctl', 'daemon-reload'], False)
        print('Alta de identidad revertida; monitoreo anterior intacto. Revisar journalctl -u ' + NAME)
        raise
    print('Identidad instalada. Confirmar collector_identity y métricas en Better Stack.')
    print('Desactivar: bash ' + str(BASE / 'desactivar.sh'))


def main():
    p = argparse.ArgumentParser()
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--verify', action='store_true')
    modes.add_argument('--publish', action='store_true')
    p.add_argument('--package-sha256')
    args = p.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Ejecutar como root.')
    os.umask(0o077)
    if args.verify or args.publish:
        check(publish=args.publish)
    else:
        enroll(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never echo JSON decode errors, HTTP bodies, tokens or subprocess output.
        print('ERROR: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
