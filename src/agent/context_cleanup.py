"""Explicit reduction of the byte-reviewed context-r6 pipeline, never automatic."""
import copy
import json
from pathlib import Path
import tempfile


def paths(units):
    return [Path('/usr/local/sbin/duplika-vector-context.py'),
            units / 'duplika-vector-context.service', units / 'duplika-vector-context.timer']


def prepare(original):
    # Caller must first prove the original configuration matches the bundled r6.
    config = copy.deepcopy(original)
    del config['sources']['duplika_server_context']
    for name in ('decode_context', 'tag_server_context'):
        del config['transforms'][name]
    config['transforms']['normalize_errors']['inputs'].remove('tag_server_context')
    journal = config['sources']['duplika_journal']
    journal['include_matches']['_SYSTEMD_UNIT'].remove('crond.service')
    journal['include_matches']['SYSLOG_IDENTIFIER'] = ['systemd', 'kernel', 'systemd-coredump']
    matches = ['_SYSTEMD_UNIT=' + value for value in journal['include_matches']['_SYSTEMD_UNIT']]
    matches += ['SYSLOG_IDENTIFIER=' + value for value in journal['include_matches']['SYSLOG_IDENTIFIER']]
    matches += ['_TRANSPORT=kernel']
    journal['extra_args'] = [part for index, value in enumerate(matches)
                             for part in (['+', value] if index else [value])]
    program = config['transforms']['tag_journal']['source']
    start, rest = program.split('cron = identifier ==', 1)
    _, rest = rest.split('reader_unit =', 1)
    program = (start + 'reader_unit =' + rest).replace(' || selected_cron', '')
    program, _ = program.split('if selected_cron {', 1)
    # CROND can match a target word through systemd metadata: explicitly discard.
    program = ('if (string(.SYSLOG_IDENTIFIER) ?? "") == "CROND" || (string(.SYSLOG_IDENTIFIER) ?? "") == "crond" '
               '|| (string(._SYSTEMD_UNIT) ?? "") == "crond.service" { abort }\n' + program)
    config['transforms']['tag_journal']['source'] = program
    tests = []
    for test in config.get('tests', []):
        inputs = test.get('inputs', [])
        if any(item.get('insert_at') in ('tag_server_context', 'decode_context') for item in inputs):
            continue
        if any(item.get('log_fields', {}).get('SYSLOG_IDENTIFIER') in ('CROND', 'crond') for item in inputs):
            test['name'] = 'descarta cron: ' + test['name']
            test.pop('outputs', None)
            test['no_outputs_from'] = ['useful_errors']
        tests.append(test)
    config['tests'] = tests
    return config


def validate(config, vector, execute):
    if not vector:
        raise RuntimeError('cleanup:vector_binary_missing')
    # These files contain a source credential. No command output or contents leave
    # this private temporary directory; delete it after both native checks.
    with tempfile.TemporaryDirectory(prefix='duplika-context-review-', dir='/root') as folder:
        path = Path(folder) / 'vector.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        execute([vector, 'test', '--config-json', str(path)])
        execute([vector, 'validate', '--no-environment', '--config-json', str(path)])
