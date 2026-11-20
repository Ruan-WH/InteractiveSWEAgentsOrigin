"""Execute model-generated diagnostic edits without correcting them by hand."""
import argparse
import json
from pathlib import Path, PurePosixPath

import docker

from evaluation.analysis.model_swap_10554_diagnosis_20261119.check_public_composition import BASE, PROBE

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--response', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--operation-matrix', action='store_true', help='Independent public-API diagnostic, never hidden-test feedback.')
    parser.add_argument('--own-trace', action='store_true', help='Production paths allowed; existing tests only, no researcher failure probe.')
    args = parser.parse_args()
    data = json.loads(args.response.read_text())
    edits = data['response']['edits']
    if args.own_trace:
        assert not args.operation_matrix
        assert all(not PurePosixPath(e['path']).is_absolute() and '..' not in PurePosixPath(e['path']).parts
                   and e['path'].startswith('django/') and e['path'].endswith('.py') for e in edits)
    else:
        assert all(e['path'] in ('django/db/models/sql/compiler.py', 'django/db/models/sql/query.py', 'django/db/models/query.py') for e in edits)
    container = docker.from_env().containers.run('docker.io/xingyaoww/sweb.eval.x86_64.django_s_django-10554',
                  entrypoint='/bin/sleep', command=['600'], network_mode='none', detach=True)
    result = {'source_commit': BASE, 'diagnostic_only': not args.own_trace, 'response_file': str(args.response),
              'verification_scope': 'existing-baseline-tests-only' if args.own_trace else 'independent-public-probe-and-existing-tests'}
    try:
        head = container.exec_run(['git', '-C', '/testbed', 'rev-parse', 'HEAD'])
        assert head.output.decode().strip() == BASE
        code = "import json,os;edits=json.loads("+repr(json.dumps(edits))+");\nfor e in edits:\n p='/testbed/'+e['path'];s=open(p).read() if os.path.exists(p) else '';assert s.count(e['old_str'])==1;os.makedirs(os.path.dirname(p),exist_ok=True);open(p,'w').write(s.replace(e['old_str'],e['new_str']))"
        applied = container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', code])
        result['apply_exit'] = applied.exit_code
        result['apply_output'] = applied.output.decode()
        staged = container.exec_run(['git', '-C', '/testbed', 'add', '-A'])
        assert staged.exit_code == 0
        patch = container.exec_run(['git', '-C', '/testbed', 'diff', '--cached', BASE])
        (HERE / (args.name + '.patch')).write_bytes(patch.output)
        if applied.exit_code == 0:
            probe_source = PROBE
            if args.operation_matrix:
                from evaluation.analysis.model_swap_10554_diagnosis_20261119.public_operation_matrix import PROBE as probe_source
                baseline_container = docker.from_env().containers.run(
                    'docker.io/xingyaoww/sweb.eval.x86_64.django_s_django-10554',
                    entrypoint='/bin/sleep', command=['600'], network_mode='none', detach=True)
                try:
                    baseline_probe = baseline_container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', probe_source])
                    result['independent_baseline_probe_exit'] = baseline_probe.exit_code
                    result['independent_baseline_probe_output'] = baseline_probe.output.decode()
                finally:
                    baseline_container.remove(force=True)
            if not args.own_trace:
                probe = container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', probe_source])
                result['probe_exit'] = probe.exit_code
                result['probe_output'] = probe.output.decode()
            suite = container.exec_run(['/bin/bash', '-c', 'cd /testbed/tests && /opt/miniconda3/envs/testbed/bin/python runtests.py queries.test_qs_combinators --settings=test_sqlite --parallel 1 --verbosity 1'])
            result['existing_tests_exit'] = suite.exit_code
            result['existing_tests_output'] = suite.output.decode()[-20000:]
        (HERE / (args.name + '.validation.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({key:value for key,value in result.items() if key != 'existing_tests_output'}, ensure_ascii=False))
        print(result.get('existing_tests_output', '')[-2000:])
    finally:
        container.remove(force=True)


if __name__ == '__main__':
    main()
