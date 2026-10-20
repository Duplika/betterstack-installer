#!/usr/bin/env python3
"""Duplika Vector profile v1, revision 6. Python 3.6+, standard library only.

Run as root on the chosen cPanel/LiteSpeed host. Requires an already working
Vector installation and its Better Stack credentials in /etc/vector/vector.yaml.
"""
import argparse
import datetime
import glob
import grp
import json
import os
import pathlib
import pwd
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

CONFIG = pathlib.Path('/etc/vector/vector.yaml')
PROFILE = pathlib.Path('/etc/vector/duplika-profile.json')
ACL_SCRIPT = pathlib.Path('/usr/local/sbin/duplika-vector-acl.py')
SERVICE = pathlib.Path('/etc/systemd/system/duplika-vector-acl.service')
TIMER = pathlib.Path('/etc/systemd/system/duplika-vector-acl.timer')
MARKER = pathlib.Path('/var/lib/vector/duplika-ingestion-check.log')
SOURCE_VERSION = 'duplika-v1'
CONFIG_REVISION = 6
JOURNAL_DROPIN = pathlib.Path('/etc/systemd/system/vector.service.d/30-duplika-journal.conf')
CONTEXT_SCRIPT = pathlib.Path('/usr/local/sbin/duplika-vector-context.py')
CONTEXT_SERVICE = pathlib.Path('/etc/systemd/system/duplika-vector-context.service')
CONTEXT_TIMER = pathlib.Path('/etc/systemd/system/duplika-vector-context.timer')
CONTEXT_DIR = pathlib.Path('/var/log/duplika-vector-context')
CONTEXT_FILES = [
    '/var/log/chkservd.log',
    '/usr/local/cpanel/logs/safeapacherestart_log',
    '/usr/local/cpanel/logs/queueprocd.log',
    '/usr/local/cpanel/logs/tailwatchd_log',
    '/usr/local/cpanel/logs/error_log',
    '/var/log/dnf.rpm.log',
]
JOURNAL_UNITS = [
    'lsws.service', 'lshttpd.service', 'httpd.service', 'cpanel.service',
    'tailwatchd.service', 'crond.service', 'mariadb.service', 'mysqld.service',
    'imunify360.service', 'imunify360-webshield.service',
    'dnf-automatic.service', 'dnf-automatic-install.service',
]
FILESYSTEM_EXCLUDES = [
    '/home/virtfs', '/home/virtfs/*',
    '/proc', '/proc/*', '/sys', '/sys/*',
    '/dev', '/dev/*', '/run', '/run/*',
    '/var/lib/nfs/rpc_pipefs', '/var/lib/nfs/rpc_pipefs/*',
]


def filesystem_excludes():
    """Keep real volumes; exclude /var/tmp only when it aliases /tmp itself."""
    excludes = list(FILESYSTEM_EXCLUDES)
    try:
        if os.path.samefile('/tmp', '/var/tmp'):
            excludes.append('/var/tmp')
    except OSError:
        pass  # An unknown layout must retain its volume metrics.
    return excludes


def run(args, check=True):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True)
    if check and result.returncode:
        raise RuntimeError('Fallo: ' + ' '.join(args[:3]))
    return result


def credentials(text):
    """Read ONLY known literal credential fields; never evaluate YAML or print secrets."""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        sinks = list(parsed.get('sinks', {}).values())
        tokens = {s.get('auth', {}).get('token') for s in sinks if s.get('auth', {}).get('token')}
        uris = {s['uri'] for s in sinks if s.get('uri')}
    else:
        # Official preset uses quoted, literal scalars. Fail closed on other formats.
        tokens = set(re.findall(r'^\s+token:\s*"([A-Za-z0-9_.-]+)"\s*$', text, re.M))
        uris = set(re.findall(r'^\s+uri:\s*"(https://[^"\s]+)"\s*$', text, re.M))
    hosts = set()
    for uri in uris:
        p = urlsplit(uri)
        if p.scheme != 'https' or not p.hostname or not p.hostname.endswith('.betterstackdata.com'):
            raise RuntimeError('Destino ajeno a Better Stack: revisar configuración antes de migrar.')
        if p.username or p.password or p.query or p.fragment or p.port:
            raise RuntimeError('URI de ingesta no reconocida.')
        hosts.add(p.hostname)
    if len(tokens) != 1 or len(hosts) != 1:
        raise RuntimeError('No hay una única credencial/destino literal; no se modificó la configuración.')
    token = next(iter(tokens))
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', token):
        raise RuntimeError('Token no literal; no se modificó la configuración.')
    return token, next(iter(hosts))


def journal_args(profile):
    # Vector alone owns the journal position: --after-cursor when resuming,
    # its default --since otherwise. An extra --since conflicts with a cursor
    # on systemd 239. The existing VRL cutoff still limits exported history.
    # With no checkpoint the initial local read can cover retained history.
    args = []
    matches = ['_SYSTEMD_UNIT=' + unit for unit in JOURNAL_UNITS]
    matches += ['SYSLOG_IDENTIFIER=' + ident for ident in ('systemd', 'kernel', 'CROND', 'crond', 'systemd-coredump')]
    matches += ['_TRANSPORT=kernel']
    # Apply the same OR selection in journalctl itself, before Vector reads the stream.
    for index, match in enumerate(matches):
        if index:
            args.append('+')
        args.append(match)
    return args


def make_config(profile, token, ingest):
    root = profile['docroot']
    cutoff = profile['since']
    start = r'^(?:(?:\[[0-9]+\] )?\[?\d{4}-\d{2}-\d{2}[ T]|\[\d{2}-[A-Za-z]{3}-\d{4})'
    sources = {
        'duplika_host': {
            'type': 'host_metrics', 'scrape_interval_secs': profile['interval'],
            'collectors': ['cpu', 'disk', 'filesystem', 'load', 'host', 'memory', 'network'],
            'filesystem': {'mountpoints': {'excludes': profile.get('filesystem_excludes', FILESYSTEM_EXCLUDES)}},
        }
    }
    paths = {
        'litespeed': ['/var/log/apache2/error_log'],
        'lsapi': ['/var/log/apache2/stderr.log'],
        'wordpress': [root + '/error_log'],
        'woocommerce': [root + '/wp-content/uploads/wc-logs/fatal-errors-*.log'],
        'check': [str(MARKER)],
        'server_context': [str(CONTEXT_DIR / 'context*.jsonl')],
    }
    transforms = {}
    for kind, include in paths.items():
        sources['duplika_' + kind] = {
            'type': 'file', 'include': include, 'read_from': 'beginning',
            'ignore_older_secs': 86400, 'max_line_bytes': 262144,
            'glob_minimum_cooldown_ms': 2000,
            'offset_key': 'file_offset',
            'multiline': {'start_pattern': start, 'condition_pattern': start,
                          'mode': 'halt_before', 'timeout_ms': 1000},
        }
        metadata = '.service = ' + json.dumps(kind) + '\n'
        metadata += '.scope = ' + json.dumps('site' if kind in ('wordpress', 'woocommerce') else 'host') + '\n'
        if kind in ('wordpress', 'woocommerce'):
            metadata += '.site = ' + json.dumps(profile['site']) + '\n'
        if kind == 'check':
            metadata += '.validation = true\n'
        transforms['tag_' + kind] = {
            'type': 'remap', 'inputs': ['duplika_' + kind], 'source': metadata,
        }
        if kind == 'server_context':
            del sources['duplika_' + kind]['multiline']
            sources['duplika_' + kind]['max_line_bytes'] = 1048576
            transforms['decode_context'] = {
                'type': 'remap', 'inputs': ['duplika_server_context'],
                'drop_on_error': True,
                'source': 'observed = .timestamp\nrelay = .file\nrecord = object!(parse_json!(string!(.message)))\n. = record\n.timestamp = observed\n.relay_file = relay\n',
            }
            transforms['tag_' + kind]['inputs'] = ['decode_context']

    # Select units plus manager/kernel/cron metadata. Export a field allowlist,
    # never journal environment, command lines, authentication or core contents.
    sources['duplika_journal'] = {
        'type': 'journald', 'current_boot_only': False, 'since_now': False,
        'include_matches': {
            '_SYSTEMD_UNIT': JOURNAL_UNITS,
            'SYSLOG_IDENTIFIER': ['systemd', 'kernel', 'CROND', 'crond', 'systemd-coredump'],
            '_TRANSPORT': ['kernel'],
        },
        'extra_args': journal_args(profile),
    }
    journal = r'''
msg = string(.message) ?? string(.MESSAGE) ?? ""
unit = string(.UNIT) ?? string(._SYSTEMD_UNIT) ?? ""
identifier = string(.SYSLOG_IDENTIFIER) ?? ""
transport = string(._TRANSPORT) ?? ""
pid = to_int(._PID) ?? null
uid = to_int(._UID) ?? null
boot = string(._BOOT_ID) ?? ""
ts = .timestamp
origin_ts = to_int(.__REALTIME_TIMESTAMP) ?? null
if origin_ts != null { ts = from_unix_timestamp!(origin_ts, unit: "microseconds") }
relevant = match(unit, r'(?i)^(?:lsws|lshttpd|httpd|cpanel|tailwatchd|mariadb|mysqld|imunify360(?:-webshield)?|dnf-automatic(?:-install)?)\.service$')
lifecycle = match(msg, r'(?i)(start|stop|restart|reload|failed|failure|terminat|kill|signal|timeout|core dump)')
target = match(msg, r'(?i)(litespeed|lshttpd|lsws|httpd|apache|cpanel|tailwatch|chkservd|queueprocd|mariadb|mysqld|imunify|monarx|ea-php|dnf-automatic)')
kernel_problem = (transport == "kernel" || identifier == "kernel") && match(msg, r'(?i)(out of memory|oom-kill|killed process|segfault|general protection|trap|hung task)')
crash = identifier == "systemd-coredump" && match(msg, r'(?i)(lsphp|php|lshttpd|httpd|mysqld|mariadbd)')
cron = identifier == "CROND" || identifier == "crond" || unit == "crond.service"
job, job_err = parse_regex(msg, r'\((?P<user>[^)]+)\) CMD(?:END)? \((?P<command>.*)\)')
selected_cron = cron && job_err == null && (job.user == "root" || job.user == __ACCOUNT__)
reader_unit = starts_with(unit, "duplika-vector-context.")
reader_failure = reader_unit && match(msg, r'(?i)(fail|error|timeout|killed|signal)')
if reader_unit && !reader_failure { abort }
if !(kernel_problem || crash || reader_failure || (relevant && lifecycle) || (identifier == "systemd" && target && lifecycle) || selected_cron) { abort }
# Replace the whole event so no arbitrary journal fields can reach the sink.
. = {"message": msg, "timestamp": ts, "service": "server_journal", "scope": "host", "host": __HOST__,
     "journal_unit": unit, "journal_identifier": identifier, "process_pid": pid, "process_uid": uid,
     "boot_id": boot, "event_kind": "service_lifecycle", "timestamp_source": "journal"}
if kernel_problem { .event_kind = "kernel_runtime_error" }
if crash {
  .event_kind = "native_crash"
  # Coredump MESSAGE can include stack arguments or environments. Keep only its first line.
  .message = split(msg, "\n", limit: 2)[0]
}
if selected_cron {
  .event_kind = "scheduled_task"
  .task_user = job.user
  command = string!(job.command)
  .task_family = "other"
  if match(command, r'(?i)(restartsrv|safeapacherestart|lswsctrl|lshttpd|systemctl\s+(restart|reload))') { .task_family = "service_restart" }
  if match(command, r'(?i)(upcp|updatenow|yum|dnf|rpm)') { .task_family = "package_update" }
  if match(command, r'(?i)(wp-cron\.php|wp\s+cron)') { .task_family = "wordpress_cron" }
  if match(command, r'(?i)(backup|acronis)') { .task_family = "backup" }
  .task_scope = "host_or_other"
  if contains(command, __DOCROOT__) { .task_scope = "production_docroot" }
  # No commands, URLs, parameters, environment variables or hashes of them.
  .message = "Scheduled task (command omitted)"
}
'''.replace('__ACCOUNT__', json.dumps(root.split('/')[2])).replace('__HOST__', json.dumps(profile['host'])).replace('__DOCROOT__', json.dumps(root))
    transforms['tag_journal'] = {'type': 'remap', 'inputs': ['duplika_journal'],
                                 'source': journal, 'drop_on_abort': True, 'drop_on_error': True}

    # Do not classify global LiteSpeed/LSAPI events as belonging to a particular site.
    parser = r'''
msg = string!(.message)
.observed_at = del(.timestamp)
.dt = .observed_at
.timestamp_unparsed = false
cpanel, cpanel_err = parse_regex(msg, r'^(?:\[[0-9]+\] )?\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\]')
php, php_err = parse_regex(msg, r'^\[(?P<ts>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2}) UTC\]')
iso, iso_err = parse_regex(msg, r'^\[?(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))')
local, local_err = parse_regex(msg, r'^\[?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)')
if .timestamp_source == "journal" && .dt != null {
  .observed_at = now()
} else if cpanel_err == null {
  .dt = parse_timestamp!(cpanel.ts, format: "%Y-%m-%d %H:%M:%S %z")
} else if php_err == null {
  .dt = parse_timestamp!(php.ts, format: "%d-%b-%Y %H:%M:%S", timezone: "UTC")
} else if iso_err == null {
  .dt = parse_timestamp(iso.ts, format: "%+") ?? parse_timestamp!(iso.ts, format: "%Y-%m-%dT%H:%M:%S%.f%z")
} else if local_err == null {
  .dt = parse_timestamp!(local.ts, format: "%Y-%m-%d %H:%M:%S%.f", timezone: "local")
} else {
  .timestamp_unparsed = true
}
.level = "error"
if .service == "server_journal" || .service == "server_context" { .level = "info" }
if match(msg, r'(?i)(\[warn(?:ing)?\]|PHP Warning:|abort request|busy: -|SIGTERM)') { .level = "warning" }
if match(msg, r'(?i)(failed|failure|\[error\])') { .level = "error" }
if match(msg, r'(?i)(fatal|uncaught|out of memory|segmentation fault|segfault|SIGKILL|signal 9)') { .level = "critical" }
if .service == "wordpress" && contains(msg, "Commands out of sync") {
  .event_kind = "sql_commands_out_of_sync"
}
if .service == "litespeed" {
  lsabort, lsabort_err = parse_regex(msg, r'Abort request processing by PID:\s*(?P<pid>[0-9]+), kill: (?P<kill>[01])')
  if lsabort_err == null {
    .event_kind = "lsapi_abort"
    .php_pid = to_int!(lsabort.pid)
    .abort_kill = lsabort.kill == "1"
    if .abort_kill == false { .level = "info" }
  }
  vhost, vhost_err = parse_regex(msg, r'#APVH_(?P<name>[^:\]\s]+)')
  if vhost_err == null { .virtual_host = vhost.name }
  if match(msg, r'LiteSpeed/\S+ (?:Enterprise )?starts successfully!') {
    .event_kind = "service_start"
    .level = "info"
  }
}
if .validation == true { .level = "info" }
if .service == "server_journal" && .event_kind == "scheduled_task" { .level = "info" }
if match(msg, r'(?i)(malloc.*assertion|sysmalloc|double free|corrupted (size|double)|heap corruption)') {
  .event_kind = "allocator_failure"
  .level = "critical"
}
if .service == "lsapi" {
  child, child_err = parse_regex(msg, r'\[UID:(?P<uid>[0-9]+)\]\[(?P<parent>[0-9]+)\] Child process with pid: (?P<pid>[0-9]+) was killed by signal: (?P<signal>[0-9]+)')
  if child_err == null {
    .event_kind = "lsapi_child_exit"
    .process_uid = to_int!(child.uid)
    .parent_pid = to_int!(child.parent)
    .php_pid = to_int!(child.pid)
    .signal = to_int!(child.signal)
  }
}
.message = replace(msg, r'(?i)(access_token|api_key|api_secret|password|passwd)=[^&\s]+', "[CREDENCIAL_OCULTA]")
.message = replace(.message, r'(?i)Bearer [A-Za-z0-9._~+/=-]+', "Bearer [OCULTO]")
if .service == "server_context" || .service == "server_journal" {
  .message = replace(.message, r'(?i)(token|secret|password|passwd|authorization|cookie|session|api[_-]?key)\s*[:=]\s*[^\s,;]+', "[CREDENCIAL_OCULTA]")
  .message = replace(.message, r'(?i)(?:https?://)[^\s<>]+', "[URL_OCULTA]")
  .message = replace(.message, r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', "[EMAIL_OCULTO]")
  .message = replace(.message, r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', "[IP_OCULTA]")
}
'''
    # Keep unknown formats rather than silently losing a new class of error.
    # Known timestamps suppress history but retain the first event in a new/rotated file.
    transforms['normalize_errors'] = {
        'type': 'remap', 'inputs': ['tag_' + x for x in paths] + ['tag_journal'],
        'source': parser, 'drop_on_error': False,
    }
    useful = r'''
msg = string!(.message)
recent = .timestamp_unparsed == true || timestamp!(.dt) >= parse_timestamp!(__CUTOFF__, format: "%+")
noise = match(msg, r'(?i)^(?:\[[^\n\]]+\]\s*|[0-9]{4}-[0-9T: .+Z-]+\s*)*(?:PHP )?(Deprecated|Notice|Strict Standards):')
ls_problem = match(msg, r'(?i)(\[(WARN(?:ING)?|ERROR|CRIT(?:ICAL)?|ALERT|EMERG)\]|fatal|uncaught|abort request|busy: -[0-9]|SIGKILL|signal 9|segfault|segmentation fault|out of memory|connection (reset|refused)|LSAPI.*(fail|timeout|timed out)|reached.*max.*(conn|child)|graceful|restart|reload|shut.*down|stopping|received.*signal|SIGTERM)')
context_ok = true
if .service == "server_context" || .service == "server_journal" {
  recent = .timestamp_unparsed == true || timestamp!(.dt) >= parse_timestamp!(__CONTEXT_CUTOFF__, format: "%+")
}
if .service == "server_context" {
  # Context relay lines may be historical fragments without a date. Do not
  # assign their ingestion time as an incident time. Keep the private relay
  # and original source available for a targeted investigation instead.
  recent = .timestamp_unparsed != true && timestamp!(.dt) >= parse_timestamp!(__CONTEXT_CUTOFF__, format: "%+")
  file = string(.file) ?? ""
  context_ok = ends_with(file, "/safeapacherestart_log") || (ends_with(file, "/chkservd.log") && match(msg, r'(?i)(httpd|apache|litespeed|lsws|restartsrv)')) || match(msg, r'(?i)(restartsrv_(httpd|apache|lsws)|safeapacherestart|rebuildhttpdconf|(?:restart|reload|graceful|stop|start|fail|kill).*?(?:httpd|apache|litespeed|lsws)|(?:httpd|apache|litespeed|lsws).*?(?:restart|reload|graceful|stop|start|fail|kill))')
  if ends_with(file, "/dnf.rpm.log") { context_ok = match(msg, r'(?i)(ea-php|litespeed|lsphp|imunify|monarx|glibc|mariadb)') }
  if .event_kind == "context_reader_status" || .event_kind == "context_reader_heartbeat" { context_ok = true }
}
recent && !noise && context_ok && (.service != "litespeed" || ls_problem)
'''.replace('__CUTOFF__', json.dumps(cutoff)).replace('__CONTEXT_CUTOFF__', json.dumps(profile['context_since']))
    transforms['useful_errors'] = {'type': 'filter', 'inputs': ['normalize_errors'], 'condition': useful}
    transforms['host_to_json'] = {'type': 'metric_to_log', 'inputs': ['duplika_host']}
    transforms['host_for_betterstack'] = {
        'type': 'remap', 'inputs': ['host_to_json'],
        'source': '.dt = del(.timestamp)\nif exists(.host) { .tags.host = del(.host) }\ndel(.namespace)\ndel(.kind)\n',
    }

    def sink(inputs, path):
        return {
            'type': 'http', 'inputs': inputs, 'uri': 'https://' + ingest + path,
            'method': 'post', 'compression': 'gzip', 'encoding': {'codec': 'json'},
            'auth': {'strategy': 'bearer', 'token': token},
            'batch': {'max_bytes': 1048576, 'timeout_secs': 5},
            'buffer': {'type': 'memory', 'max_events': 2000, 'when_full': 'block'},
        }
    result = {
        'data_dir': '/var/lib/vector', 'timezone': 'local',
        'sources': sources, 'transforms': transforms,
        'sinks': {'duplika_logs': sink(['useful_errors'], '/'),
                  'duplika_metrics': sink(['host_for_betterstack'], '/metrics')},
    }
    # Real Vector tests are run by the server's binary before activation.
    tests = []
    def case(name, service, message, keep, condition=None):
        test = {'name': name, 'inputs': [{'insert_at': 'tag_' + service,
                'type': 'log', 'log_fields': {'message': message, 'host': profile['host']}}]}
        if keep:
            test['outputs'] = [{'extract_from': 'useful_errors', 'conditions': [
                {'type': 'vrl', 'source': condition or 'assert_eq!(.service, ' + json.dumps(service) + ')'}]}]
        else:
            test['no_outputs_from'] = ['useful_errors']
        tests.append(test)
    case('conserva SQL', 'wordpress', '[19-Oct-2099 04:00:00 UTC] WordPress database error Commands out of sync', True)
    case('conserva diagnostico por peticion', 'wordpress', '[19-Oct-2099 04:00:00 UTC] DUPLIKA_SQL_SYNC_V1 {"diagnostic_version":1,"event_kind":"sql_sync_request","php_pid":42}', True,
         'assert!(contains(string!(.message), "DUPLIKA_SQL_SYNC_V1"))\nassert!(!exists(.event_kind))')
    case('descarta PHP deprecated', 'wordpress', '[19-Oct-2099 04:00:00 UTC] PHP Deprecated: obsolete function', False)
    case('descarta deprecated en stderr', 'lsapi', '2099-10-19 01:00:00.123456 [STDERR] PHP Deprecated: obsolete function', False)
    case('conserva error de formato desconocido', 'wordpress', 'New custom plugin failure: connection closed', True)
    case('descarta historial', 'wordpress', '[19-Oct-2020 04:00:00 UTC] PHP Fatal error: old', False)
    case('conserva primer fatal Woo', 'woocommerce', '2099-10-19T04:00:00+00:00 CRITICAL Uncaught Error\nStack trace:\n#0 test.php', True)
    case('conserva aborto LSAPI', 'litespeed', '2099-10-19 01:00:00.123456 [NOTICE] abort request', True)
    case('descarta aviso normal LS', 'litespeed', '2099-10-19 01:00:00.123456 [NOTICE] Worker started', False)
    case('oculta token en URL', 'wordpress', '[19-Oct-2099 04:00:00 UTC] PHP Warning: https://example.invalid/?access_token=TEST_SECRET&x=1', True,
         'assert!(!contains(string!(.message), "TEST_SECRET"))')
    case('errores globales sin atribución a tienda', 'lsapi', '[19-Oct-2099 04:00:00 UTC] PHP Fatal error: crash', True,
         'assert_eq!(.scope, "host")\nassert!(!exists(.site))')
    case('clasifica SQL sin perder el mensaje', 'wordpress', '[19-Oct-2099 04:00:00 UTC] WordPress database error Commands out of sync', True,
         'assert_eq!(.event_kind, "sql_commands_out_of_sync")\nassert_eq!(.level, "error")')
    case('extrae PID de aborto real sin inventar sitio', 'litespeed',
         '2099-10-19 01:53:32.161454 [INFO] [1174672] [T0] [192.0.2.1:1234:HTTP2-1#APVH_example.invalid:443] Abort request processing by PID:1755175, kill: 1, begin time: 0, sent time: 0, req processed: 0', True,
         'assert_eq!(.php_pid, 1755175)\nassert_eq!(.abort_kill, true)\nassert_eq!(.virtual_host, "example.invalid")\nassert_eq!(.event_kind, "lsapi_abort")\nassert_eq!(.level, "warning")\nassert!(!exists(.site))')
    case('aborto sin kill es informativo pero se conserva', 'litespeed',
         '2099-10-19 01:53:32.161454 [INFO] Abort request processing by PID:42, kill: 0', True,
         'assert_eq!(.level, "info")\nassert_eq!(.abort_kill, false)')
    case('arranque exitoso no es error pero se conserva', 'litespeed',
         '2099-10-19 02:09:51.647914 [ALERT] [1846556] [T0] LiteSpeed/6.3.7 Enterprise starts successfully!', True,
         'assert_eq!(.level, "info")\nassert_eq!(.event_kind, "service_start")')
    case('alerta real conserva su gravedad', 'litespeed',
         '2099-10-19 02:09:51.647914 [ALERT] listener cannot bind socket', True,
         'assert_eq!(.level, "error")\nassert!(!exists(.event_kind))')
    case('conserva recarga informativa LiteSpeed', 'litespeed',
         '2099-10-19 12:00:00.123456 [INFO] Graceful restart requested', True)
    case('asercion sin fecha sigue sin fecha original', 'lsapi',
         "ndex.php: malloc.c:2415: sysmalloc: Assertion failed.", True,
         'assert_eq!(.event_kind, "allocator_failure")\nassert_eq!(.timestamp_unparsed, true)\nassert!(!exists(.php_pid))\nassert!(!exists(.site))')
    case('extrae salida LSAPI sin atribucion de dominio', 'lsapi',
         '2099-10-19 11:35:35.015335 [UID:1003][816618] Child process with pid: 836800 was killed by signal: 9, core dumped: no', True,
         'assert_eq!(.php_pid, 836800)\nassert_eq!(.process_uid, 1003)\nassert_eq!(.signal, 9)\nassert!(!exists(.site))')

    def context_case(name, file, message, keep, condition=None):
        case(name, 'server_context', message, keep, condition)
        tests[-1]['inputs'][0]['log_fields']['file'] = file
    context_case('fecha cPanel respeta offset', CONTEXT_FILES[1],
                 '[2099-10-19 11:30:10 -0300] info [safeapacherestart] Restart elapsed seconds: 2', True,
                 'assert_eq!(.dt, t\'2099-10-19T14:30:10Z\')\nassert_eq!(.scope, "host")')
    context_case('fecha TailWatch tras PID', CONTEXT_FILES[3],
                 '[1234] [2099-10-19 11:30:10 -0300] httpd restart requested', True,
                 'assert_eq!(.dt, t\'2099-10-19T14:30:10Z\')')
    context_case('descarta cPanel ajeno a recargas', CONTEXT_FILES[4],
                 '[2099-10-19 11:30:10 -0300] mail account warning unrelated', False)
    context_case('omite contexto anterior a ventana', CONTEXT_FILES[1],
                 '[2020-10-19 11:30:10 -0300] safeapacherestart restart', False)
    context_case('conserva paquete PHP', CONTEXT_FILES[5],
                 '2099-10-19T11:30:10+00:00 INFO Upgraded: ea-php83-php-cli', True)
    context_case('fecha DNF con offset compacto', CONTEXT_FILES[5],
                 '2099-10-19T11:30:10-0300 SUBDEBUG Upgraded: ea-php83-php-cli', True,
                 'assert_eq!(.dt, t\'2099-10-19T14:30:10Z\')\nassert_eq!(.timestamp_unparsed, false)')
    context_case('fecha compacta positiva y fracciones', CONTEXT_FILES[5],
                 '2099-10-19T11:30:10.123+0530 SUBDEBUG Upgraded: ea-php83-php-cli', True,
                 'assert_eq!(.dt, t\'2099-10-19T06:00:10.123Z\')')
    context_case('descarta paquete historico con offset compacto', CONTEXT_FILES[5],
                 '2026-10-02T00:45:00-0300 SUBDEBUG Upgraded: imunify-antivirus', False)
    context_case('descarta fragmento chkservd sin fecha', CONTEXT_FILES[0],
                 'httpd [[check command:N/A][socket connect:+]]...', False)
    context_case('descarta fragmento syslog sin ano de queueprocd', CONTEXT_FILES[2],
                 '\tOct 01 05:24:05 cloud.example.test systemd[1]: Started Apache SpamAssassin deferral daemon.', False)
    context_case('conserva fallo chkservd fechado', CONTEXT_FILES[0],
                 '[2099-10-19 11:30:10 -0300] httpd failed; restart requested', True,
                 'assert_eq!(.dt, t\'2099-10-19T14:30:10Z\')')
    context_case('descarta paquete ajeno', CONTEXT_FILES[5],
                 '2099-10-19T11:30:10+00:00 INFO Upgraded: unrelated-package', False)
    context_case('oculta datos en contexto nuevo', CONTEXT_FILES[1],
                 '[2099-10-19 11:30:10 -0300] restart token=SECRET abc@example.test 192.0.2.10 https://example.test/?key=HIDDEN', True,
                 'assert!(!contains(string!(.message), "SECRET"))\nassert!(!contains(string!(.message), "abc@example"))\nassert!(!contains(string!(.message), "192.0.2"))\nassert!(!contains(string!(.message), "HIDDEN"))')
    tests.append({'name': 'relay conserva fecha archivo y offset originales', 'inputs': [{
        'insert_at': 'decode_context', 'type': 'log', 'log_fields': {
            'message': json.dumps({'message': '[2099-10-19 11:30:10 -0300] httpd restart requested',
                                   'file': CONTEXT_FILES[4], 'file_offset': 12345}),
            'file': str(CONTEXT_DIR / 'context.jsonl')}}], 'outputs': [{
        'extract_from': 'useful_errors', 'conditions': [{'type': 'vrl', 'source':
        'assert_eq!(.file_offset, 12345)\nassert_eq!(.file, "/usr/local/cpanel/logs/error_log")\nassert_eq!(.dt, t\'2099-10-19T14:30:10Z\')'}]}]})
    tests.append({'name': 'heartbeat relay no necesita archivo fuente', 'inputs': [{
        'insert_at': 'decode_context', 'type': 'log', 'log_fields': {
            'message': json.dumps({'message': '2099-10-19T14:30:10+00:00 Context reader heartbeat',
                                   'event_kind': 'context_reader_heartbeat'})}}], 'outputs': [{
        'extract_from': 'useful_errors', 'conditions': [{'type': 'vrl', 'source':
        'assert_eq!(.event_kind, "context_reader_heartbeat")\nassert_eq!(.scope, "host")'}]}]})

    def journal_case(name, fields, keep, condition=None):
        fields.update({'__REALTIME_TIMESTAMP': '4096103410000000', '_BOOT_ID': 'fixture-boot'})
        test = {'name': name, 'inputs': [{'insert_at': 'tag_journal', 'type': 'log', 'log_fields': fields}]}
        if keep:
            test['outputs'] = [{'extract_from': 'useful_errors', 'conditions': [
                {'type': 'vrl', 'source': condition or 'assert_eq!(.scope, "host")'}]}]
        else:
            test['no_outputs_from'] = ['useful_errors']
        tests.append(test)
    journal_case('captura manager aunque unidad emisora init.scope',
                 {'message': 'Starting LiteSpeed web server...', '_SYSTEMD_UNIT': 'init.scope',
                  'UNIT': 'lsws.service', 'SYSLOG_IDENTIFIER': 'systemd', '_PID': '1', '_CMDLINE': 'SECRET',
                  'ENVIRONMENT': 'TOKEN=SECRET'}, True,
                 'assert_eq!(.journal_unit, "lsws.service")\nassert_eq!(.process_pid, 1)\nassert_eq!(.timestamp_source, "journal")\nassert_eq!(.timestamp_unparsed, false)\nassert!(!exists(._CMDLINE))\nassert!(!exists(.ENVIRONMENT))')
    journal_case('descarta manager ajeno', {'message': 'Started unrelated.service.', 'SYSLOG_IDENTIFIER': 'systemd'}, False)
    journal_case('omite arranques rutinarios del propio lector',
                 {'message': 'Starting Duplika: lectura acotada de contexto cPanel...',
                  'UNIT': 'duplika-vector-context.service', 'SYSLOG_IDENTIFIER': 'systemd'}, False)
    journal_case('conserva fallo del lector',
                 {'message': 'duplika-vector-context.service: Failed with result exit-code.',
                  'UNIT': 'duplika-vector-context.service', 'SYSLOG_IDENTIFIER': 'systemd'}, True,
                 'assert_eq!(.event_kind, "service_lifecycle")')
    journal_case('cron root sin comando ni secretos', {'message': '(root) CMD (TOKEN=SECRET /usr/local/cpanel/scripts/upcp --cron)',
                 'SYSLOG_IDENTIFIER': 'CROND', '_PID': '42'}, True,
                 'assert_eq!(.event_kind, "scheduled_task")\nassert_eq!(.task_family, "package_update")\nassert!(!contains(encode_json(.), "SECRET"))')
    journal_case('cron tienda clasificado', {'message': '(' + root.split('/')[2] + ') CMD (php ' + root + '/wp-cron.php)',
                 'SYSLOG_IDENTIFIER': 'CROND'}, True,
                 'assert_eq!(.task_scope, "production_docroot")\nassert_eq!(.task_family, "wordpress_cron")')
    journal_case('descarta cron de otra cuenta', {'message': '(other_account) CMD (wp cron event run --due-now)',
                 'SYSLOG_IDENTIFIER': 'CROND'}, False)
    journal_case('conserva OOM kernel', {'message': 'Out of memory: Killed process 42 (lsphp)',
                 'SYSLOG_IDENTIFIER': 'kernel', '_TRANSPORT': 'kernel'}, True,
                 'assert_eq!(.event_kind, "kernel_runtime_error")')
    journal_case('descarta kernel rutinario', {'message': 'device connected', 'SYSLOG_IDENTIFIER': 'kernel'}, False)
    journal_case('coredump no reenvia entorno ni argumentos', {'message': 'Process 42 (lsphp) dumped core.\nEnvironment: SECRET',
                 'SYSLOG_IDENTIFIER': 'systemd-coredump', 'COREDUMP_ENVIRON': 'SECRET', 'COREDUMP_CMDLINE': 'SECRET'}, True,
                 'assert_eq!(.event_kind, "native_crash")\nassert!(!contains(encode_json(.), "SECRET"))')
    tests.append({'name': 'mantiene nombre y host de métricas', 'inputs': [{
        'insert_at': 'host_to_json', 'type': 'metric', 'metric': {
            'name': 'memory_available_bytes', 'namespace': 'host', 'kind': 'absolute',
            'tags': {'host': profile['host'], 'collector': 'memory'}, 'gauge': {'value': 12345}}}],
        'outputs': [{'extract_from': 'host_for_betterstack', 'conditions': [{
            'type': 'vrl', 'source': 'assert_eq!(.name, "memory_available_bytes")\nassert_eq!(.tags.host, ' + json.dumps(profile['host']) + ')\nassert_eq!(.gauge.value, 12345.0)'}]}]})
    result['tests'] = tests
    return result


# Bundled from lector-contexto.py; kept self-contained for one-file deployment.
CONTEXT_HELPER = r"""#!/usr/bin/env python3
'''Read only selected root logs; write a bounded, private JSON relay. Python 3.6+.'''
import argparse
import datetime
import fcntl
import json
import os
import pathlib
import pwd
import re
import stat
import time

FILES = (
    '/var/log/chkservd.log',
    '/usr/local/cpanel/logs/safeapacherestart_log',
    '/usr/local/cpanel/logs/queueprocd.log',
    '/usr/local/cpanel/logs/tailwatchd_log',
    '/usr/local/cpanel/logs/error_log',
    '/var/log/dnf.rpm.log',
)
DIRECTORY = pathlib.Path('/var/log/duplika-vector-context')
INITIAL_BYTES = 4 * 1024 * 1024
READ_BYTES = 2 * 1024 * 1024
MAX_LINE = 64 * 1024
ROTATE_BYTES = 8 * 1024 * 1024
KEEP = 4


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def open_source(path):
    if os.path.realpath(path) != path:
        raise RuntimeError('Ruta redirigida: ' + path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError('Log no regular: ' + path)
    return os.fdopen(fd, 'rb')


def selected(path, text):
    if path.endswith('/safeapacherestart_log'):
        return True
    if path.endswith('/dnf.rpm.log'):
        return re.search(r'ea-php|litespeed|lsphp|imunify|monarx|glibc|mariadb', text, re.I) is not None
    if path.endswith('/chkservd.log'):
        return re.search(r'httpd|apache|litespeed|lsws|restartsrv', text, re.I) is not None
    return re.search(r'restartsrv_(httpd|apache|lsws)|safeapacherestart|rebuildhttpdconf|'
                     r'(restart|reload|graceful|stop|start|fail|kill).*?(httpd|apache|litespeed|lsws)|'
                     r'(httpd|apache|litespeed|lsws).*?(restart|reload|graceful|stop|start|fail|kill)', text, re.I) is not None


def scrub(text):
    text = re.sub(r'''(?ix)(?:access_token|api[_-]?(?:key|secret)|token|secret|password|passwd|authorization|cookie|session)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''', '[CREDENCIAL_OCULTA]', text)
    text = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [OCULTO]', text)
    text = re.sub(r'(?i)https?://[^\s<>]+', '[URL_OCULTA]', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL_OCULTO]', text)
    return re.sub(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', '[IP_OCULTA]', text)


def read_batch(path, previous, host):
    '''No writes to the source, including no chmod, chown, setfacl or chattr.'''
    records = []
    with open_source(path) as stream:
        st = os.fstat(stream.fileno())
        same = previous.get('inode') == st.st_ino and previous.get('device') == st.st_dev
        saved = int(previous.get('offset', 0))
        reset = not same or saved > st.st_size
        start = max(0, st.st_size - INITIAL_BYTES) if reset else saved
        dropping = bool(previous.get('dropping')) if not reset else start > 0
        if reset and start > 0:
            stream.seek(start - 1)
            dropping = stream.read(1) != b'\n'
        stream.seek(start)
        oversize = 0
        while stream.tell() - start < READ_BYTES:
            offset = stream.tell()
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            complete = line.endswith(b'\n')
            if dropping:
                dropping = not complete
                continue
            if len(line) > MAX_LINE:
                oversize += 1
                dropping = not complete
                continue
            if not complete:
                stream.seek(offset)  # Retry a partially written line on the next run.
                break
            text = line.decode('utf-8', errors='replace').rstrip('\r\n')
            if selected(path, text):
                records.append({'message': scrub(text), 'file': path, 'file_offset': offset,
                                'source_inode': st.st_ino, 'source_device': st.st_dev,
                                'host': host, 'context_reader': True})
        current = {'inode': st.st_ino, 'device': st.st_dev, 'offset': stream.tell(), 'dropping': dropping, 'status': 'ok'}
        if reset or previous.get('status') != 'ok' or oversize:
            records.insert(0, {'message': utcnow() + ' Context reader source status', 'file': path,
                              'host': host, 'event_kind': 'context_reader_status', 'reader_status': 'ok',
                              'read_from_offset': start, 'source_size': st.st_size,
                              'initial_bytes_skipped': start if reset else 0,
                              'source_reset': reset, 'oversize_lines_skipped': oversize})
    return records, current


def private_file(path, flags, mode):
    fd = os.open(str(path), flags | os.O_NOFOLLOW, mode)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1:
        os.close(fd)
        raise RuntimeError('Archivo privado inseguro: ' + str(path))
    os.fchmod(fd, mode)
    return fd


def append_records(directory, records, gid):
    target = directory / 'context.jsonl'
    out = None
    size = target.stat().st_size if target.exists() else 0
    try:
        for record in records:
            data = (json.dumps(record, ensure_ascii=True) + '\n').encode()
            if size + len(data) > ROTATE_BYTES:
                if out is not None:
                    out.flush()
                    os.fsync(out.fileno())
                    out.close()
                    out = None
                for index in range(KEEP, 0, -1):
                    src = target if index == 1 else directory / ('context.%d.jsonl' % (index - 1))
                    dst = directory / ('context.%d.jsonl' % index)
                    if src.exists():
                        os.replace(str(src), str(dst))
                size = 0
            if out is None:
                fd = private_file(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
                out = os.fdopen(fd, 'ab')
                os.fchown(fd, os.geteuid(), gid)
            out.write(data)
            size += len(data)
    finally:
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
            out.close()


def check_sources():
    for path in FILES:
        try:
            with open_source(path):
                print('LECTURA OK: ' + path)
        except FileNotFoundError:
            print('AUSENTE (opcional): ' + path)


def collect(directory, host, gid):
    if os.path.realpath(str(directory)) != str(directory):
        raise RuntimeError('Directorio de salida redirigido')
    st = directory.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise RuntimeError('Directorio de salida no privado')
    lock = private_file(directory / '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock, 'r+') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        state_path = directory / 'state.json'
        if state_path.exists():
            with os.fdopen(private_file(state_path, os.O_RDONLY, 0o600)) as src:
                state = json.load(src)
        else:
            state = {}
        for path in FILES:
            previous = state.get(path, {})
            try:
                records, current = read_batch(path, previous, host)
            except (OSError, RuntimeError) as exc:
                status = 'missing' if isinstance(exc, FileNotFoundError) else 'unreadable'
                current = dict(previous, status=status)
                records = [] if previous.get('status') == status else [
                    {'message': utcnow() + ' Context reader source status', 'file': path, 'host': host,
                     'event_kind': 'context_reader_status', 'reader_status': status,
                     'error_type': type(exc).__name__, 'error_errno': getattr(exc, 'errno', None)}]
            append_records(directory, records, gid)
            state[path] = current
        if time.time() - state.get('_heartbeat', 0) >= 600:
            append_records(directory, [{'message': utcnow() + ' Context reader heartbeat', 'host': host,
                                        'event_kind': 'context_reader_heartbeat'}], gid)
            state['_heartbeat'] = time.time()
        fd = private_file(directory / 'state.new', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(str(directory / 'state.new'), str(state_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        check_sources()
        return
    with open('/etc/vector/duplika-profile.json') as src:
        profile = json.load(src)
    if tuple(profile.get('context_files', [])) != FILES:
        raise RuntimeError('Lista de logs distinta de la autorizada')
    collect(DIRECTORY, profile['host'], pwd.getpwnam('vector').pw_gid)


if __name__ == '__main__':
    main()
"""

ACL_HELPER = '''#!/usr/bin/env python3
# Duplika Vector profile v1: only grant traversal/read to the configured paths.
import glob, json, os, stat, subprocess, sys
p = json.load(open('/etc/vector/duplika-profile.json'))
root = p['docroot']
def can(mode, path):
    return subprocess.call(['runuser','-u','vector','--','test',mode,path]) == 0
def grant(path, perm, test):
    if os.path.islink(path):
        raise SystemExit('No se conceden permisos sobre symlinks: ' + path)
    if not can(test, path) and '--check' not in sys.argv:
        subprocess.check_call(['setfacl','-m','u:vector:' + perm, path])
# Paths must remain in the approved directory trees after resolving ancestors.
for path in ['/var/log/apache2', root, root + '/wp-content', root + '/wp-content/uploads', root + '/wp-content/uploads/wc-logs']:
    if os.path.realpath(path) != path:
        raise SystemExit('Ruta redirigida: ' + path)
    if os.path.isdir(path):
        grant(path, '--x', '-x')
files = ['/var/log/apache2/error_log', '/var/log/apache2/stderr.log', root + '/error_log']
# cPanel context is read by the separate root reader. Never change its ACL/flags.
files += glob.glob(root + '/wp-content/uploads/wc-logs/fatal-errors-*.log')
for path in files:
    if os.path.exists(path):
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise SystemExit('Archivo no regular: ' + path)
        grant(path, 'r--', '-r')
'''


def write_file(path, data, mode, gid=0):
    path.write_text(data)
    os.chown(str(path), 0, gid)
    os.chmod(str(path), mode)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', required=True)
    ap.add_argument('--site', required=True)
    ap.add_argument('--docroot', required=True)
    ap.add_argument('--interval', type=int, default=60)
    ap.add_argument('--context-hours', type=int, default=4,
                    help='Historial inicial de contexto nuevo (1 a 24 horas). Mantiene cursor/corte al repetir.')
    args = ap.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Ejecutar como root en el VPS elegido.')
    if socket.getfqdn() != args.host and socket.gethostname() != args.host:
        raise RuntimeError('El hostname no coincide; no se modificó nada.')
    for value in (args.host, args.site):
        if not re.fullmatch(r'[a-zA-Z0-9.-]+', value):
            raise RuntimeError('Nombre de host/sitio inválido.')
    root = str(pathlib.Path(args.docroot).absolute())
    if not re.fullmatch(r'/home/[a-zA-Z0-9_-]+/public_html', root):
        raise RuntimeError('Este perfil requiere /home/USUARIO/public_html; adaptar otros layouts por separado.')
    if os.path.realpath(root) != root or not os.path.isfile(root + '/wp-config.php'):
        raise RuntimeError('Docroot no coincide con una instalación WordPress directa.')
    if args.interval not in (30, 60):
        raise RuntimeError('Intervalo admitido: 30 o 60 segundos.')
    if not 1 <= args.context_hours <= 24:
        raise RuntimeError('Ventana inicial admitida: 1 a 24 horas.')
    for cmd in ('vector', 'systemctl', 'setfacl', 'getfacl', 'runuser', 'journalctl'):
        if not shutil.which(cmd):
            raise RuntimeError('Falta dependencia: ' + cmd)
    for prop in ('User', 'Group'):
        if run(['systemctl', 'show', 'vector', '-p', prop, '--value']).stdout.strip() != 'vector':
            raise RuntimeError('El servicio debe ejecutar como vector:vector.')
    # Reject alternate config paths, config directories and environment-based config.
    unit = run(['systemctl', 'show', 'vector', '-p', 'ExecStart', '-p', 'Environment']).stdout
    if '--config-dir' in unit or 'VECTOR_CONFIG' in unit:
        raise RuntimeError('Servicio con configuración adicional; requiere revisión antes de reemplazar el preset.')
    config_args = re.findall(r'--config(?:-yaml|-json|-toml)?(?:=|\s+)([^\s;]+)|(?:^|\s)-c\s+([^\s;]+)', unit)
    if any((a or b) != str(CONFIG) for a, b in config_args):
        raise RuntimeError('El servicio usa otra ruta de configuración.')
    run(['systemctl', 'is-active', '--quiet', 'vector'])
    grp.getgrnam('systemd-journal')  # Fail before changes if the standard group is absent.
    journal_user = ['runuser', '-u', 'vector', '-g', 'vector', '-G', 'systemd-journal', '--']
    journal_probe = run(journal_user + ['journalctl', '-q', '-n', '1', '-o', 'json'], False)
    if journal_probe.returncode or not journal_probe.stdout.strip().startswith('{'):
        raise RuntimeError('Vector no puede leer el journal con systemd-journal; revisar permisos antes de aplicar.')
    original = CONFIG.read_bytes()
    token, ingest = credentials(original.decode())
    profile = {'version': SOURCE_VERSION, 'revision': CONFIG_REVISION,
               'filesystem_excludes': filesystem_excludes(),
               'host': args.host, 'site': args.site,
               'docroot': root, 'interval': args.interval,
               'context_files': CONTEXT_FILES,
               'context_since': (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.context_hours)).replace(microsecond=0).isoformat(),
               'since': datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()}
    if PROFILE.exists():
        old_profile = json.loads(PROFILE.read_text())
        if any(old_profile.get(k) != profile[k] for k in ('host', 'site', 'docroot', 'version')):
            raise RuntimeError('Ya existe otro perfil; no se sobrescribe.')
        profile['since'] = old_profile['since']
        profile['context_since'] = old_profile.get('context_since', profile['context_since'])
        if old_profile.get('revision', 1) > CONFIG_REVISION:
            raise RuntimeError('Existe una revisión más nueva; no se permite retroceder.')
    gid = pwd.getpwnam('vector').pw_gid
    backup = pathlib.Path(tempfile.mkdtemp(prefix='vector-duplika.', dir='/root'))
    os.chmod(str(backup), 0o700)
    affected = [CONFIG, PROFILE, ACL_SCRIPT, SERVICE, TIMER, MARKER, JOURNAL_DROPIN,
                CONTEXT_SCRIPT, CONTEXT_SERVICE, CONTEXT_TIMER]
    if os.path.realpath(str(JOURNAL_DROPIN.parent)) != str(JOURNAL_DROPIN.parent):
        raise RuntimeError('Directorio de unidad Vector redirigido; no se modifica.')
    existing = {}
    for n, path in enumerate(affected):
        if path.is_symlink():
            raise RuntimeError('Archivo de configuración redirigido: ' + str(path))
        if path.exists():
            target = backup / ('file-' + str(n))
            run(['cp', '-a', str(path), str(target)])
            existing[str(path)] = str(target)
    acl_paths = ['/var/log/apache2', '/var/log/apache2/error_log', '/var/log/apache2/stderr.log', root,
                 root + '/error_log', root + '/wp-content', root + '/wp-content/uploads', root + '/wp-content/uploads/wc-logs']
    acl_paths += glob.glob(root + '/wp-content/uploads/wc-logs/fatal-errors-*.log')
    acl_paths = [x for x in acl_paths if os.path.exists(x)]
    acl_text = run(['getfacl', '-p'] + acl_paths).stdout
    (backup / 'permissions.acl').write_text(acl_text)
    was_enabled = run(['systemctl', 'is-enabled', '--quiet', TIMER.name], False).returncode == 0
    was_active = run(['systemctl', 'is-active', '--quiet', TIMER.name], False).returncode == 0
    context_enabled = run(['systemctl', 'is-enabled', '--quiet', CONTEXT_TIMER.name], False).returncode == 0
    context_active = run(['systemctl', 'is-active', '--quiet', CONTEXT_TIMER.name], False).returncode == 0
    rollback = ['#!/bin/bash', 'set -eu', 'systemctl stop duplika-vector-acl.timer duplika-vector-acl.service || true',
                'systemctl disable duplika-vector-acl.timer || true',
                'systemctl stop duplika-vector-context.timer duplika-vector-context.service || true',
                'systemctl disable duplika-vector-context.timer || true']
    for path in affected:
        if str(path) in existing:
            rollback += ['cp -a ' + shlex.quote(existing[str(path)]) + ' ' + shlex.quote(str(path))]
        else:
            rollback += ['rm -f -- ' + shlex.quote(str(path))]
    rollback += ['setfacl --restore=' + shlex.quote(str(backup / 'permissions.acl')), 'systemctl daemon-reload']
    if was_enabled:
        rollback += ['systemctl enable duplika-vector-acl.timer']
    if was_active:
        rollback += ['systemctl start duplika-vector-acl.timer']
    if context_enabled:
        rollback += ['systemctl enable duplika-vector-context.timer']
    if context_active:
        rollback += ['systemctl start duplika-vector-context.timer']
    rollback += ['systemctl reset-failed vector', 'systemctl restart vector', 'systemctl is-active vector']
    write_file(backup / 'restaurar.sh', '\n'.join(rollback) + '\n', 0o700)
    print('Respaldo privado: ' + str(backup))
    print('Reversión: bash ' + str(backup / 'restaurar.sh'))
    candidate = CONFIG.parent / ('.duplika-' + backup.name + '.yaml')
    changed = False
    try:
        write_file(candidate, json.dumps(make_config(profile, token, ingest), indent=2) + '\n', 0o640, gid)
        # Tests do not send telemetry; run them before changing permissions or configuration.
        result = run([shutil.which('vector'), 'test', '--config-yaml', str(candidate)], False)
        (backup / 'tests.txt').write_text(result.stdout.replace(token, '[TOKEN]'))
        if result.returncode:
            print(result.stdout.replace(token, '[TOKEN]'))
            raise RuntimeError('Candidato no aprobado por Vector; sigue vigente la configuración anterior.')
        if CONFIG.read_bytes() != original:
            raise RuntimeError('La configuración cambió durante la preparación; se detiene sin sobrescribirla.')
        # Read-only checks before touching active files or permissions.
        acl_check = ACL_HELPER.replace("p = json.load(open('/etc/vector/duplika-profile.json'))", 'p = ' + repr(profile))
        write_file(backup / 'check-acl.py', acl_check, 0o700)
        write_file(backup / 'check-context.py', CONTEXT_HELPER, 0o700)
        for label, command in (
                ('acl-preflight', ['/usr/bin/python3', str(backup / 'check-acl.py'), '--check']),
                ('context-preflight', ['/usr/bin/python3', str(backup / 'check-context.py'), '--check'])):
            checked = run(command, False)
            (backup / (label + '.txt')).write_text(checked.stdout.replace(token, '[TOKEN]'))
            if checked.returncode:
                print(checked.stdout.replace(token, '[TOKEN]'))
                raise RuntimeError('Falló ' + label + '; no se modificó la configuración activa.')
        if os.path.realpath(str(CONTEXT_DIR)) != str(CONTEXT_DIR):
            raise RuntimeError('Directorio relay redirigido; no se modifica.')
        if CONTEXT_DIR.exists() and (CONTEXT_DIR.stat().st_uid != 0 or CONTEXT_DIR.stat().st_mode & 0o022):
            raise RuntimeError('Directorio relay no privado; requiere revisión.')
        changed = True
        write_file(PROFILE, json.dumps(profile, indent=2) + '\n', 0o600)
        write_file(ACL_SCRIPT, ACL_HELPER, 0o700)
        write_file(SERVICE, '[Unit]\nDescription=Duplika Vector: permisos de logs seleccionados\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 ' + str(ACL_SCRIPT) + '\n', 0o644)
        write_file(TIMER, '[Unit]\nDescription=Revisar permisos de logs tras rotaciones\n\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec=1min\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n', 0o644)
        JOURNAL_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        write_file(JOURNAL_DROPIN, '[Service]\nSupplementaryGroups=systemd-journal\n', 0o644)
        if not CONTEXT_DIR.exists():
            CONTEXT_DIR.mkdir(mode=0o750)
            os.chown(str(CONTEXT_DIR), 0, gid)
        write_file(CONTEXT_SCRIPT, CONTEXT_HELPER, 0o700)
        write_file(CONTEXT_SERVICE, '[Unit]\nDescription=Duplika: lectura acotada de contexto cPanel\n\n[Service]\nType=oneshot\nUser=root\nGroup=vector\nUMask=0027\nExecStart=/usr/bin/python3 ' + str(CONTEXT_SCRIPT) + '\nNoNewPrivileges=true\nPrivateTmp=true\nProtectHome=true\nProtectSystem=strict\nReadWritePaths=' + str(CONTEXT_DIR) + '\nTimeoutStartSec=45\nNice=10\n', 0o644)
        write_file(CONTEXT_TIMER, '[Unit]\nDescription=Recoger contexto cPanel cada 30 segundos\n\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=30s\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n', 0o644)
        applied_acl = run(['/usr/bin/python3', str(ACL_SCRIPT)], False)
        (backup / 'acl-apply.txt').write_text(applied_acl.stdout.replace(token, '[TOKEN]'))
        if applied_acl.returncode:
            print(applied_acl.stdout.replace(token, '[TOKEN]'))
            raise RuntimeError('No se pudieron aplicar las ACL; detalle conservado en acl-apply.txt.')
        # Start the constrained reader once, then validate the actual relay as vector.
        run(['systemctl', 'daemon-reload'])
        reader = run(['systemctl', 'start', CONTEXT_SERVICE.name], False)
        if reader.returncode:
            details = run(['journalctl', '-u', CONTEXT_SERVICE.name, '-n', '12', '--no-pager'], False)
            (backup / 'context-start.txt').write_text(details.stdout.replace(token, '[TOKEN]'))
            print(details.stdout.replace(token, '[TOKEN]'))
            raise RuntimeError('Falló el lector de contexto; detalle conservado en context-start.txt.')
        run(['runuser', '-u', 'vector', '--', 'test', '-r', str(CONTEXT_DIR / 'context.jsonl')])
        run(['runuser', '-u', 'vector', '--', 'test', '-r', '/var/log/apache2/error_log'])
        run(['runuser', '-u', 'vector', '--', 'test', '-r', '/var/log/apache2/stderr.log'])
        run(['runuser', '-u', 'vector', '--', 'test', '-r', root + '/error_log'])
        # Now check the exact service user's environment and destinations, still before activation.
        result = run(journal_user + [shutil.which('vector'), 'validate', '--config-yaml', str(candidate)], False)
        (backup / 'validate.txt').write_text(result.stdout.replace(token, '[TOKEN]'))
        if result.returncode:
            print(result.stdout.replace(token, '[TOKEN]'))
            raise RuntimeError('Validación fallida; se restauran configuración y permisos previos.')
        # Preserve inode/SELinux context and limit access to root and the Vector group.
        with open(str(CONFIG), 'wb') as f:
            f.write(candidate.read_bytes())
            f.flush()
            os.fsync(f.fileno())
        os.chown(str(CONFIG), 0, gid)
        os.chmod(str(CONFIG), 0o640)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', '--now', TIMER.name])
        run(['systemctl', 'enable', '--now', CONTEXT_TIMER.name])
        run(['systemctl', 'restart', 'vector'])
        time.sleep(3)
        run(['systemctl', 'is-active', '--quiet', 'vector'])
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        marker_id = 'duplika-' + datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        # One explicitly identified test event, in a separate file (not an application error).
        write_file(MARKER, now + ' INFO Better Stack ingestion check ' + marker_id + '\n', 0o640, gid)
        print('Vector: active. Perfil selectivo aplicado.')
        print('Revisión del perfil: %s. Exclusiones de filesystem: %s' % (CONFIG_REVISION, ', '.join(profile['filesystem_excludes'])))
        print('Métricas cada %ss. Logs: LiteSpeed, LSAPI, WordPress y fatales Woo.' % args.interval)
        print('Contexto nuevo: journal de servicios/kernel/cron y archivos cPanel seleccionados; desde ' + profile['context_since'])
        print('Logs cPanel: lector acotado cada 30s; sin modificar ACL ni atributos de sus fuentes.')
        print('Contexto disponible: ' + ', '.join(x for x in CONTEXT_FILES if os.path.isfile(x)))
        print('Contexto ausente (no se crea): ' + ', '.join(x for x in CONTEXT_FILES if not os.path.isfile(x)))
        print('Prueba de ingesta: ' + marker_id)
        print('Respaldo privado: ' + str(backup))
        print('Reversión: bash ' + str(backup / 'restaurar.sh'))
        print('Falta confirmar recepción por MCP y observar las próximas rotaciones.')
    except Exception:
        if changed:
            print('Se restaura la configuración anterior...')
            result = run(['bash', str(backup / 'restaurar.sh')], False)
            (backup / 'rollback.txt').write_text(result.stdout.replace(token, '[TOKEN]'))
            if result.returncode:
                print('La restauración requiere revisión. Ejecutar: bash ' + str(backup / 'restaurar.sh'))
            else:
                print('Restauración terminada: Vector activo con la configuración anterior.')
        raise
    finally:
        if candidate.exists():
            candidate.unlink()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
