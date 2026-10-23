"""Explicit, reviewed aggregation for the inherited minimal error transport."""
import copy

LEGACY_MARKER = 'minimal_errors_5m_v21'
RC22_MARKER = 'minimal_errors_5m_v22'
RC23_MARKER = 'minimal_errors_5m_v23'
MARKER = 'minimal_errors_5m_v24'
LEGACY_CONDITION = 'includes(["lsapi_abort", "sql_commands_out_of_sync"], .event_kind) && .level != "critical"'
RC22_CONDITION = '''event_time, time_error = parse_timestamp(to_string(.dt) ?? "", format: "%+")
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


def prepare_v22(original):
    """Frozen rc22 blueprint: preserves its original typed-date defect."""
    config = prepare_v21(original)
    transforms = config['transforms']
    transforms['repeat_route']['route']['repeat'] = RC22_CONDITION
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


# The reader normalizes .dt to a VRL timestamp; JSON inputs can still be text.
# to_string serializes either form before parsing, without a string-only assertion.
CONDITION = RC22_CONDITION + ' && .timestamp_unparsed != true'


def prepare(original, marker=MARKER):
    if marker == LEGACY_MARKER:
        return prepare_v21(original)
    if marker not in (RC22_MARKER, RC23_MARKER, MARKER):
        raise RuntimeError('aggregation:unknown_review_marker')
    config = prepare_v22(original)
    if marker == RC22_MARKER:
        return config
    transforms = config['transforms']
    transforms['repeat_route']['route']['repeat'] = CONDITION
    transforms['repeat_summary']['source'] = transforms['repeat_summary']['source'].replace(
        'parse_timestamp!(string!(.dt), format: "%+")', 'parse_timestamp!(to_string!(.dt), format: "%+")')
    if marker == RC23_MARKER:
        return config
    # Wall-clock expiry alone cannot bound the span of original event dates.
    # Fixed UTC event-time buckets also separate late and out-of-order events.
    # Epoch seconds are exact here; floor handles dates before the epoch too.
    transforms['repeat_summary']['source'] = transforms['repeat_summary']['source'].replace(
        '. = {"first_seen_us": event_us,',
        'bucket = floor(to_float(to_unix_timestamp(event_time)) / 300.0)\n'
        '. = {"aggregation_bucket": bucket, "first_seen_us": event_us,')
    transforms['repeat_reduce']['group_by'].append('aggregation_bucket')
    transforms['repeat_dates']['source'] += '\ndel(.aggregation_bucket)\n'
    return config


def replace_reviewed(config, marker):
    """Replace only a byte-equivalent reviewed graph; never adopt drift."""
    if marker not in (LEGACY_MARKER, RC22_MARKER, RC23_MARKER):
        raise RuntimeError('aggregation:unknown_review_marker')
    original = copy.deepcopy(config)
    output = 'repeat_reduce' if marker == LEGACY_MARKER else 'repeat_dates'
    if original['sinks']['duplika_logs']['inputs'] != ['repeat_route._unmatched', output]:
        raise RuntimeError('aggregation:unexpected_legacy_sink')
    original['sinks']['duplika_logs']['inputs'] = ['useful_errors']
    keys = ['repeat_route', 'repeat_summary', 'repeat_reduce']
    if marker in (RC22_MARKER, RC23_MARKER):
        keys.append('repeat_dates')
    for key in keys:
        original['transforms'].pop(key, None)
    if prepare(original, marker) != config:
        raise RuntimeError('aggregation:legacy_graph_differs_from_blueprint')
    return prepare(original)


def replace_v21(config):
    return replace_reviewed(config, LEGACY_MARKER)



def validate_plan(api, old):
    if old.get('pipeline_variant') != 'almalinux-wordpress-minimal-v4':
        raise RuntimeError('aggregation:requires_reviewed_minimal_v4')
    config = api.load(old['config_path'])
    marker = old.get('migration_review', {}).get('error_aggregation')
    if marker in (LEGACY_MARKER, RC22_MARKER, RC23_MARKER):
        config = replace_reviewed(config, marker)
    elif marker is None:
        config = prepare(config)
    elif marker != MARKER:
        raise RuntimeError('aggregation:unknown_review_marker')
    api.context_cleanup.validate(config, api.trusted_executable('vector'), api.execute)
    return config
