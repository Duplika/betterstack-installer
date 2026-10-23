"""Explicit, reviewed aggregation for the inherited minimal error transport."""
import copy

LEGACY_MARKER = 'minimal_errors_5m_v21'
MARKER = 'minimal_errors_5m_v22'
LEGACY_CONDITION = 'includes(["lsapi_abort", "sql_commands_out_of_sync"], .event_kind) && .level != "critical"'
CONDITION = '''event_time, time_error = parse_timestamp(to_string(.dt) ?? "", format: "%+")
''' + LEGACY_CONDITION + ' && time_error == null && is_timestamp(event_time)'


def prepare_v21(original):
    """Frozen rc21 blueprint, used only to verify the installed configuration."""
    config = copy.deepcopy(original)
    if config['sinks']['duplika_logs']['inputs'] != ['useful_errors']:
        raise RuntimeError('aggregation:unexpected_log_sink')
    transforms = config['transforms']
    transforms['repeat_route'] = {'type': 'route', 'inputs': ['useful_errors'],
                                  'route': {'repeat': LEGACY_CONDITION}}
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


def prepare(original, marker=MARKER):
    if marker not in (LEGACY_MARKER, MARKER):
        raise RuntimeError('aggregation:unknown_review_marker')
    config = prepare_v21(original)
    if marker == LEGACY_MARKER:
        return config
    transforms = config['transforms']
    transforms['repeat_route']['route']['repeat'] = CONDITION
    # reduce min/max accept numbers, not ISO strings or VRL timestamps.
    # Integer microseconds preserve the source precision and sort by instant,
    # including timestamps with different UTC offsets and out-of-order input.
    transforms['repeat_summary']['source'] = '''
event_time = parse_timestamp!(string!(.dt), format: "%+")
event_us = to_unix_timestamp(event_time, unit: "microseconds")
. = {"first_seen_us": event_us, "last_seen_us": event_us, "host": .host,
     "server_ip": .server_ip, "service": .service, "site": .site,
     "virtual_host": .virtual_host, "event_kind": .event_kind, "level": .level,
     "abort_kill": .abort_kill, "occurrences": 1, "aggregation_seconds": 300,
     "message": "Repeated application error; see event_kind and occurrences"}
'''
    transforms['repeat_reduce']['merge_strategies'] = {
        'occurrences': 'sum', 'aggregation_seconds': 'retain',
        'first_seen_us': 'min', 'last_seen_us': 'max'}
    transforms['repeat_dates'] = {'type': 'remap', 'inputs': ['repeat_reduce'], 'source': '''
.first_seen = from_unix_timestamp!(int!(del(.first_seen_us)), unit: "microseconds")
.last_seen = from_unix_timestamp!(int!(del(.last_seen_us)), unit: "microseconds")
.dt = .first_seen
'''}
    config['sinks']['duplika_logs']['inputs'] = ['repeat_route._unmatched', 'repeat_dates']
    return config


def replace_v21(config):
    """Replace only an exact reviewed rc21 graph; never adopt a modified one."""
    original = copy.deepcopy(config)
    if original['sinks']['duplika_logs']['inputs'] != ['repeat_route._unmatched', 'repeat_reduce']:
        raise RuntimeError('aggregation:unexpected_legacy_sink')
    original['sinks']['duplika_logs']['inputs'] = ['useful_errors']
    for key in ('repeat_route', 'repeat_summary', 'repeat_reduce'):
        original['transforms'].pop(key, None)
    if prepare_v21(original) != config:
        raise RuntimeError('aggregation:legacy_graph_differs_from_blueprint')
    return prepare(original)


def validate_plan(api, old):
    if old.get('pipeline_variant') != 'almalinux-wordpress-minimal-v4':
        raise RuntimeError('aggregation:requires_reviewed_minimal_v4')
    config = api.load(old['config_path'])
    marker = old.get('migration_review', {}).get('error_aggregation')
    if marker == LEGACY_MARKER:
        config = replace_v21(config)
    elif marker is None:
        config = prepare(config)
    elif marker != MARKER:
        raise RuntimeError('aggregation:unknown_review_marker')
    api.context_cleanup.validate(config, api.trusted_executable('vector'), api.execute)
    return config
