#!/usr/bin/env python3
"""Selective metrics and error logs for one cPanel WordPress; based on Duplika v1/r2."""
import argparse
import datetime
import glob
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

MARKER = pathlib.Path('/var/lib/duplika-betterstack/ingestion-check.log')
SOURCE_VERSION = 'duplika-v1'
CONFIG_REVISION = 2
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


def make_config(profile, token, ingest):
    root = profile['docroot']
    cpanel_user = root.split('/')[2]
    cutoff = profile['since']
    start = r'^(?:\[?\d{4}-\d{2}-\d{2}[ T]|\[\d{2}-[A-Za-z]{3}-\d{4})'
    sources = {
        'duplika_host': {
            'type': 'host_metrics', 'scrape_interval_secs': profile['interval'],
            'collectors': ['cpu', 'disk', 'filesystem', 'load', 'host', 'memory', 'network'],
            'filesystem': {'mountpoints': {'excludes': profile.get('filesystem_excludes', FILESYSTEM_EXCLUDES)}},
        }
    }
    paths = {
        'litespeed': profile.get('web_logs', ['/var/log/apache2/error_log']),
        'lsapi': profile.get('php_logs', ['/var/log/apache2/stderr.log']),
        'wordpress': [root + '/error_log'],
        'woocommerce': [root + '/wp-content/uploads/wc-logs/fatal-errors-*.log'],
        'check': [str(MARKER)],
    }
    transforms = {}
    for kind, include in paths.items():
        sources['duplika_' + kind] = {
            'type': 'file', 'include': include, 'read_from': 'beginning',
            'ignore_older_secs': 86400, 'max_line_bytes': 262144,
            'glob_minimum_cooldown_ms': 2000,
            'multiline': {'start_pattern': start, 'condition_pattern': start,
                          'mode': 'halt_before', 'timeout_ms': 1000},
        }
        metadata = '.service = ' + json.dumps(kind) + '\n'
        metadata += '.host = ' + json.dumps(profile['host']) + '\n.cpanel_user = ' + json.dumps(cpanel_user) + '\n'
        metadata += '.scope = ' + json.dumps('site' if kind in ('wordpress', 'woocommerce') else 'host') + '\n'
        if kind in ('wordpress', 'woocommerce'):
            metadata += '.site = ' + json.dumps(profile['site']) + '\n'
        if kind == 'check':
            metadata += '.validation = true\n'
        transforms['tag_' + kind] = {
            'type': 'remap', 'inputs': ['duplika_' + kind], 'source': metadata,
        }

    # Do not classify global LiteSpeed/LSAPI events as belonging to a particular site.
    parser = r'''
msg = string!(.message)
.observed_at = del(.timestamp)
.dt = .observed_at
.timestamp_unparsed = false
php, php_err = parse_regex(msg, r'^\[(?P<ts>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2}) UTC\]')
iso, iso_err = parse_regex(msg, r'^\[?(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))')
local, local_err = parse_regex(msg, r'^\[?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)')
if php_err == null {
  .dt = parse_timestamp!(php.ts, format: "%d-%b-%Y %H:%M:%S", timezone: "UTC")
} else if iso_err == null {
  .dt = parse_timestamp!(iso.ts, format: "%+")
} else if local_err == null {
  .dt = parse_timestamp!(local.ts, format: "%Y-%m-%d %H:%M:%S%.f", timezone: "local")
} else {
  .timestamp_unparsed = true
}
.level = "error"
if match(msg, r'(?i)(\[warn(?:ing)?\]|PHP Warning:|abort request|busy: -|SIGTERM)') { .level = "warning" }
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
.message = replace(msg, r'(?i)(access_token|api_key|api_secret|password|passwd)=[^&\s]+', "[CREDENCIAL_OCULTA]")
.message = replace(.message, r'(?i)Bearer [A-Za-z0-9._~+/=-]+', "Bearer [OCULTO]")
'''
    # Keep unknown formats rather than silently losing a new class of error.
    # Known timestamps suppress history but retain the first event in a new/rotated file.
    transforms['normalize_errors'] = {
        'type': 'remap', 'inputs': ['tag_' + x for x in paths],
        'source': parser, 'drop_on_error': False,
    }
    useful = r'''
msg = string!(.message)
recent = .timestamp_unparsed == true || timestamp!(.dt) >= parse_timestamp!(__CUTOFF__, format: "%+")
noise = match(msg, r'(?i)^(?:\[[^\n\]]+\]\s*|[0-9]{4}-[0-9T: .+Z-]+\s*)*(?:PHP )?(Deprecated|Notice|Strict Standards):')
ls_problem = match(msg, r'(?i)(\[(WARN(?:ING)?|ERROR|CRIT(?:ICAL)?|ALERT|EMERG)\]|fatal|uncaught|abort request|busy: -[0-9]|SIGKILL|signal 9|segfault|segmentation fault|out of memory|connection (reset|refused)|LSAPI.*(fail|timeout|timed out)|reached.*max.*(conn|child))')
recent && !noise && (.service != "litespeed" || ls_problem)
'''.replace('__CUTOFF__', json.dumps(cutoff))
    transforms['useful_errors'] = {'type': 'filter', 'inputs': ['normalize_errors'], 'condition': useful}
    transforms['host_to_json'] = {'type': 'metric_to_log', 'inputs': ['duplika_host']}
    transforms['host_for_betterstack'] = {
        'type': 'remap', 'inputs': ['host_to_json'],
        'source': '.dt = del(.timestamp)\nif exists(.host) { .tags.host = del(.host) }\ndel(.namespace)\ndel(.kind)\n.tags.host = ' + json.dumps(profile['host']) + '\n',
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
        'data_dir': '/var/lib/duplika-betterstack', 'timezone': 'local',
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
    tests.append({'name': 'mantiene nombre y host de métricas', 'inputs': [{
        'insert_at': 'host_to_json', 'type': 'metric', 'metric': {
            'name': 'memory_available_bytes', 'namespace': 'host', 'kind': 'absolute',
            'tags': {'host': profile['host'], 'collector': 'memory'}, 'gauge': {'value': 12345}}}],
        'outputs': [{'extract_from': 'host_for_betterstack', 'conditions': [{
            'type': 'vrl', 'source': 'assert_eq!(.name, "memory_available_bytes")\nassert_eq!(.tags.host, ' + json.dumps(profile['host']) + ')\nassert_eq!(.gauge.value, 12345.0)'}]}]})
    result['tests'] = tests
    # Keep the inherited tests and parser; rename generic PHP/Apache events only
    # after the filter so detection does not depend on WordPress being installed.
    final = '.server = ' + json.dumps(profile['host']) + '\n'
    if not profile.get('wordpress', True):
        final += 'if .service == \"wordpress\" { .service = \"php\" }\n'
    if not profile.get('litespeed', True):
        final += 'if .service == \"litespeed\" { .service = \"apache\" }\n'
        transforms['useful_errors']['condition'] = '\n'.join(line for line in useful.splitlines() if not line.startswith('ls_problem =')).replace('(.service != \"litespeed\" || ls_problem)', 'true')
    transforms['final_labels'] = {'type': 'remap', 'inputs': ['useful_errors'], 'source': final}
    result['sinks']['duplika_logs']['inputs'] = ['final_labels']
    if not profile.get('wordpress', True):
        del sources['duplika_woocommerce']
        del transforms['tag_woocommerce']
        transforms['normalize_errors']['inputs'].remove('tag_woocommerce')
        result['tests'] = [t for t in tests if t['name'] != 'conserva primer fatal Woo']
    if not profile.get('litespeed', True):
        result['tests'] = [t for t in result['tests'] if t['name'] != 'descarta aviso normal LS']
    expected_service = 'wordpress' if profile.get('wordpress', True) else 'php'
    result['tests'].append({'name': 'identidad final del servidor y tipo de aplicación',
        'inputs': [{'insert_at': 'tag_wordpress', 'type': 'log', 'log_fields': {
            'message': '[19-Oct-2099 04:00:00 UTC] PHP Fatal error: test', 'host': 'cloud'}}],
        'outputs': [{'extract_from': 'final_labels', 'conditions': [{'type': 'vrl', 'source':
            'assert_eq!(.host, ' + json.dumps(profile['host']) + ')\nassert_eq!(.service, ' + json.dumps(expected_service) + ')\nassert_eq!(.cpanel_user, ' + json.dumps(cpanel_user) + ')'}]}]})
    return result

