"""Vector transport, with no access to application home directories."""
import json

def essential_config(config, disk_io_observer=False):
    """Reviewed observer-only graph: real storage is measured by its own probe."""
    import copy
    result = copy.deepcopy(config)
    host = result['sources']['host']
    host['collectors'] = ['cpu', 'load', 'host', 'memory']
    host.pop('filesystem', None)
    result['sources']['host_slow'] = {'type': 'host_metrics', 'scrape_interval_secs': 300,
                                      'collectors': ['network'] if disk_io_observer else ['disk', 'network']}
    result['transforms']['host_json']['inputs'] = ['host', 'host_slow']
    result['transforms']['host_essential'] = {'type': 'filter', 'inputs': ['host_format'],
        'condition': '!starts_with(string!(.name), "memory_") || includes(["memory_total_bytes", "memory_available_bytes", "memory_swap_total_bytes", "memory_swap_used_bytes", "memory_oom_kill_events_total"], .name)'}
    result['sinks']['metrics']['inputs'] = ['metrics_json', 'host_essential']
    result['tests'] += [
        {'name': 'essential memory keeps availability', 'inputs': [{'insert_at': 'host_essential',
            'type': 'log', 'log_fields': {'name': 'memory_available_bytes', 'gauge': {'value': 123}}}],
         'outputs': [{'extract_from': 'host_essential', 'conditions': [{'type': 'vrl',
             'source': 'assert_eq!(.name, "memory_available_bytes")'}]}]},
        {'name': 'unused memory is dropped', 'inputs': [{'insert_at': 'host_essential',
            'type': 'log', 'log_fields': {'name': 'memory_cached_bytes', 'gauge': {'value': 123}}}],
         'no_outputs_from': ['host_essential']},
        {'name': 'slow disk metrics keep names', 'inputs': [{'insert_at': 'host_essential',
            'type': 'log', 'log_fields': {'name': 'disk_read_bytes_total', 'counter': {'value': 123}}}],
         'outputs': [{'extract_from': 'host_essential', 'conditions': [{'type': 'vrl',
             'source': 'assert_eq!(.name, "disk_read_bytes_total")'}]}]},
    ]
    if disk_io_observer:
        result['tests'].append({'name': 'privileged disk counter survives spool parser',
            'inputs': [{'insert_at': 'metrics_json', 'type': 'log', 'log_fields': {'message': json.dumps({
                'dt': '2026-10-19T18:00:00+00:00', 'name': 'disk_written_bytes_total',
                'counter': {'value': 1024}, 'tags': {'device': 'sda'}})}}],
            'outputs': [{'extract_from': 'metrics_json', 'conditions': [{'type': 'vrl', 'source':
                'assert_eq!(.name, "disk_written_bytes_total")\nassert_eq!(.counter.value, 1024)\nassert_eq!(.tags.device, "sda")'}]}]})
    return result

def make_config(profile, token, ingest):
    root = '/var/lib/duplika-betterstack-cloudlinux'
    def files(pattern):
        return {'type': 'file', 'include': [root + '/spool/' + pattern],
                'read_from': 'beginning', 'max_line_bytes': 262144,
                'glob_minimum_cooldown_ms': 5000}
    def sink(inputs, suffix):
        return {'type': 'http', 'inputs': inputs, 'uri': 'https://' + ingest + suffix,
                'method': 'post', 'compression': 'gzip', 'encoding': {'codec': 'json'},
                'auth': {'strategy': 'bearer', 'token': token},
                'batch': {'max_bytes': 1048576, 'timeout_secs': 5},
                'buffer': {'type': 'disk', 'max_size': 268435488, 'when_full': 'block'}}
    parser = '. = parse_json!(string!(.message))\n.dt = parse_timestamp!(string!(.dt), format: "%+")'
    result = {
        'data_dir': root + '/vector',
        'sources': {
            'host': {'type': 'host_metrics', 'scrape_interval_secs': 60,
                'collectors': ['cpu', 'disk', 'filesystem', 'load', 'host', 'memory', 'network'],
                'filesystem': {'mountpoints': {'excludes': [
                    '/home/virtfs', '/home/virtfs/*', '/var/cagefs', '/var/cagefs/*',
                    '/usr/share/cagefs-skeleton', '/usr/share/cagefs-skeleton/*',
                    '/proc', '/proc/*', '/sys', '/sys/*', '/dev', '/dev/*', '/run', '/run/*']}}},
            'observer_logs': files('events.jsonl*'),
            'observer_metrics': files('metrics.jsonl*'),
        },
        'transforms': {
            'logs_json': {'type': 'remap', 'inputs': ['observer_logs'], 'source': parser,
                          'drop_on_error': True},
            'metrics_json': {'type': 'remap', 'inputs': ['observer_metrics'], 'source': parser,
                             'drop_on_error': True},
            'host_json': {'type': 'metric_to_log', 'inputs': ['host']},
            'host_format': {'type': 'remap', 'inputs': ['host_json'], 'source':
                '.dt = del(.timestamp)\ndel(.namespace)\ndel(.kind)\ndel(.host)\n.tags.host = ' +
                json.dumps(profile['host']) + '\n.tags.server_ip = ' + json.dumps(profile['server_ip']) + '\n'},
        },
        'sinks': {'logs': sink(['logs_json'], '/'),
                  'metrics': sink(['metrics_json', 'host_format'], '/metrics')},
    }
    result['tests'] = [
        {'name': 'structured metrics keep identity and zero',
         'inputs': [{'insert_at': 'metrics_json', 'type': 'log', 'log_fields': {'message':
             json.dumps({'dt': '2026-10-19T18:00:00+00:00', 'name': 'duplika_lve_cpu_percent',
                         'gauge': {'value': 0}, 'tags': {'user': 'example', 'host': profile['host']}})}}],
         'outputs': [{'extract_from': 'metrics_json', 'conditions': [{'type': 'vrl', 'source':
             'assert_eq!(.gauge.value, 0)\nassert_eq!(.tags.user, "example")\nassert_eq!(.name, "duplika_lve_cpu_percent")'}]}]},
        {'name': 'bad JSON is dropped', 'inputs': [{'insert_at': 'logs_json', 'type': 'log',
              'log_fields': {'message': 'not JSON'}}], 'no_outputs_from': ['logs_json']},
        {'name': 'host metric keeps the original name', 'inputs': [{
            'insert_at': 'host_json', 'type': 'metric', 'metric': {
                'name': 'load1', 'namespace': 'host', 'kind': 'absolute', 'gauge': {'value': 3}}}],
         'outputs': [{'extract_from': 'host_format', 'conditions': [{'type': 'vrl', 'source':
              'assert_eq!(.name, "load1")\nassert_eq!(.tags.server_ip, ' + json.dumps(profile['server_ip']) + ')'}]}]},
    ]
    return essential_config(result, bool(profile.get('disk_io_observer'))) if profile.get('monitoring_policy') == 'essential-v1' else result

