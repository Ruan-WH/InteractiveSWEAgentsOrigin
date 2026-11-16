"""Replay saved outputs through the runtime and optionally test source-backed closure."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tomllib
from types import SimpleNamespace
from unittest.mock import Mock

from openai import OpenAI
from openai.types.chat import ChatCompletion

from evaluation.utils.simple_clarification import decide_simple_followup, run_simple_clarification


PROJECT = Path(__file__).resolve().parents[3]
OUTPUTS = PROJECT / 'evaluation/evaluation_outputs'
ANSWERS = {
    '2317-missing-method-value': 'The argument was method = b"GET" (bytes). builtin_str(method) produced the literal string "b\'GET\'" rather than "GET".',
    '24661-missing-expression-text': 'expr_text was "1 < 2". The reported call was parse_expr("1 < 2", evaluate=False).',
    '14623-missing-limit-call': 'The calls were ax.set_yscale("log"); ax.set_ylim(y.max(), y.min()). The source constructs y = np.linspace(1000e2, 1, 100), so the limits are y.max() followed by y.min().',
}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--live-closure', action='store_true')
    parser.add_argument('--finish-pending', action='store_true')
    args = parser.parse_args()
    root = args.output_dir.resolve()
    cases = {c['id']: c for c in json.loads((root / 'cases.json').read_text())}
    entries = []
    for path in root.glob('fresh/focused_answer/*/*/response.json'):
        case = cases[path.parent.parent.name]
        saved = json.loads(path.read_text())
        llm = SimpleNamespace(completion=Mock(return_value=ChatCompletion.model_validate(saved['response'])))
        current = decide_simple_followup(llm, case['summary'], case['exchanges'])
        entries.append({'path': str(path), 'input_identical': current['request_messages'] == saved['request_messages'],
                        'decision_identical': current.get('question') == saved.get('question') and current['status'] == saved['status']})
    primary = OUTPUTS / 'simple-scope-confirm-20261116'
    primary_cases = {c['id']: c for c in json.loads((primary / 'cases.json').read_text())}
    primary_replay = []
    for path in primary.glob('development/scope_evidence/*/*/response.json'):
        case = primary_cases[path.parent.parent.name]
        saved = json.loads(path.read_text())
        responses = [ChatCompletion.model_validate(saved['response'])]
        after_path = path.parent / 'after.json'
        after = json.loads(after_path.read_text()) if after_path.exists() else None
        if after:
            responses.append(ChatCompletion.model_validate(after['response']))
        llm = SimpleNamespace(completion=Mock(side_effect=responses))
        proxy = SimpleNamespace(generate_reply=Mock(return_value=saved.get('followup_answer')))
        target = root / 'offline_replay' / case['id'] / path.parent.name
        target.mkdir(parents=True, exist_ok=True)
        current = run_simple_clarification(llm, case['summary'], case['exchanges'], proxy, target / 'record.json', max_followups=1)
        identical = current['checks'][0]['request_messages'] == saved['request_messages']
        if after:
            identical &= current['checks'][1]['request_messages'] == after['request_messages']
        primary_replay.append({'case': case['id'], 'repeat': path.parent.name,
            'inputs_identical': identical, 'followups': current['followups_used'],
            'status': current['status'], 'stop_reason': current['stop_reason'],
            'new_model_calls': 0})
    integrity = {
        'fresh_replays': entries, 'primary_runtime_replays': primary_replay,
        'source_snapshots_match_manifest': True, 'case_hash_matches_manifest': True,
    }
    manifest = json.loads((root / 'manifest.json').read_text())
    for rel, digest in manifest['source_sha256'].items():
        integrity['source_snapshots_match_manifest'] &= hashlib.sha256(
            (root / 'source_snapshot' / Path(rel).name).read_bytes()).hexdigest() == digest
    integrity['case_hash_matches_manifest'] = hashlib.sha256((root / 'cases.json').read_bytes()).hexdigest() == manifest['cases_sha256']
    integrity['runtime_sha256'] = hashlib.sha256((PROJECT / 'evaluation/utils/simple_clarification.py').read_bytes()).hexdigest()
    pending = []
    closure_replays = []
    for path in root.glob('fresh/focused_answer/*/*/closure.json'):
        case_id = path.parent.parent.name
        case = cases[case_id]
        initial = json.loads((path.parent / 'response.json').read_text())
        saved = json.loads(path.read_text())
        public = case['exchanges'] + [{'question': initial['question'], 'answer': ANSWERS[case_id]}]
        llm = SimpleNamespace(completion=Mock(return_value=ChatCompletion.model_validate(saved['response'])))
        current = decide_simple_followup(llm, case['summary'], public)
        row = {'case': case_id, 'repeat': path.parent.name, 'raw_status': saved['status'],
               'status': current['status'], 'question': current.get('question'),
               'source_parts': current.get('evidence_parts'),
               'input_identical': current['request_messages'] == saved['request_messages'],
               'format_recovered': saved['status'] == 'failed' and current['status'] == 'completed'}
        closure_replays.append(row)
        if current.get('question'):
            pending.append((path, public, current['question']))
    integrity['closure_replays'] = closure_replays
    dump(root / 'runtime_verification.json', integrity)
    assert len(entries) == 18 and all(e['input_identical'] and e['decision_identical'] for e in entries)
    assert len(primary_replay) == 8 and all(e['inputs_identical'] and e['status'] == 'completed' for e in primary_replay)
    print('Offline verification: 18/18 fresh decisions and 8/8 primary runtime inputs identical.', flush=True)
    if not args.live_closure and not args.finish_pending:
        return
    if args.live_closure and (root / 'closure_started.json').exists():
        raise ValueError('Closure already started; preserve attempt without reroll')
    if args.live_closure:
        dump(root / 'closure_started.json', {'answers': ANSWERS, 'model_config': 'hidden_deepseek_flash',
            'note': 'Scripted source-backed followup answers, not live Proxy generation.'})
    settings = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_deepseek_flash']
    bypass = set(filter(None, (os.environ.get('NO_PROXY', '') + ',' + os.environ.get('no_proxy', '')).split(',')))
    bypass.update(('localhost', '127.0.0.1', '::1'))
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(sorted(bypass))
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(name, '')
        if value.lower().startswith('socks://'):
            os.environ[name] = 'socks5://' + value[len('socks://'):]
    client = OpenAI(api_key=settings['api_key'], base_url=settings['base_url'], timeout=120, max_retries=0)

    class Model:
        def completion(self, messages):
            return client.chat.completions.create(model=settings['model'].removeprefix('openai/'),
                messages=messages, temperature=settings.get('temperature', 0), top_p=settings.get('top_p', 1),
                response_format={'type': 'json_object'})

    if args.finish_pending:
        if (root / 'completion_started.json').exists():
            raise ValueError('Completion already started; do not reroll')
        assert len(pending) == 1 and pending[0][0].parent.parent.name == '14623-missing-limit-call'
        answer = ('For both the linear and log scale cases, the same loop body calls '
            'ax.set_yscale(yscale); ax.set_ylim(y.max(), y.min()), with yscale taking '
            '"linear" and "log". The report uses y = np.linspace(1000e2, 1, 100). '
            'The limits supplied are therefore 100000 and 1 for both scales. '
            'Linear inverts, log does not; both should invert.')
        path, public, question = pending[0]
        dump(root / 'completion_started.json', {'case': path.parent.parent.name,
            'repeat': path.parent.name, 'question': question, 'answer': answer,
            'reason': 'First scripted answer gave log calls only; the model legitimately asked about linear calls/values. Public source uses one loop for both.'})
        result = decide_simple_followup(Model(), cases[path.parent.parent.name]['summary'],
            public + [{'question': question, 'answer': answer}])
        dump(root / 'completion_result.json', result)
        print('Remaining completion: ' + json.dumps({'status': result['status'], 'question': result.get('question')}, ensure_ascii=False), flush=True)
        return

    results = []
    for path in sorted(root.glob('fresh/focused_answer/*/*/response.json')):
        case_id = path.parent.parent.name
        if case_id not in ANSWERS:
            continue
        case = cases[case_id]
        initial = json.loads(path.read_text())
        public = case['exchanges'] + [{'question': initial['question'], 'answer': ANSWERS[case_id]}]
        try:
            result = decide_simple_followup(Model(), case['summary'], public)
        except Exception as error:
            result = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
        dump(path.parent / 'closure.json', result)
        results.append({'case': case_id, 'repeat': path.parent.name,
                        'status': result['status'], 'question': result.get('question'),
                        'pass': result['status'] == 'completed' and result.get('question') == ''})
        dump(root / 'closure_results.json', results)
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
