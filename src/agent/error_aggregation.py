"""Explicit, reviewed aggregation for the inherited minimal error transport."""
import copy

MARKER = 'minimal_errors_5m_v21'
CONDITION = 'includes(["lsapi_abort", "sql_commands_out_of_sync"], .event_kind) && .level != "critical"'


def prepare(original):
    config = copy.deepcopy(original)
    if config['sinks']['duplika_logs']['inputs'] != ['useful_errors']:
        raise RuntimeError('aggregation:unexpected_log_sink')
    transforms = config['transforms']
    transforms['repeat_route'] = {'type': 'route', 'inputs': ['useful_errors'],
                                  'route': {'repeat': CONDITION}}
    transforms['repeat_summary'] = {'type': 'remap', 'inputs': ['repeat_route.repeat'], 'source': '''
. = {"dt": .dt, "first_seen": .dt, "last_seen": .dt, "host": .host,
     "server_ip": .server_ip, "service": .service, "site": .site,
     "virtual_host": .virtual_host, "event_kind": .event_kind, "level": .level,
     "abort_kill": .abort_kill, "occurrences": 1, "aggregation_seconds": 300,
     "message": "Repeated application error; see event_kind and occurrences"}
'''}
    transforms['repeat_reduce'] = {'type': 'reduce', 'inputs': ['repeat_summary'],
        'group_by': ['host', 'service', 'site', 'virtual_host', 'event_kind', 'level', 'abort_kill'],
        'expire_after_ms': 300000, 'end_every_period_ms': 300000, 'flush_period_ms': 1000,
        'merge_strategies': {'occurrences': 'sum', 'aggregation_seconds': 'retain',
                             'dt': 'min', 'first_seen': 'min', 'last_seen': 'max'}}
    config['sinks']['duplika_logs']['inputs'] = ['repeat_route._unmatched', 'repeat_reduce']
    return config


def validate_plan(api, old):
    if old.get('pipeline_variant') != 'almalinux-wordpress-minimal-v4':
        raise RuntimeError('aggregation:requires_reviewed_minimal_v4')
    config = api.load(old['config_path'])
    if old.get('migration_review', {}).get('error_aggregation') != MARKER:
        config = prepare(config)
    api.context_cleanup.validate(config, api.trusted_executable('vector'), api.execute)
    return config
