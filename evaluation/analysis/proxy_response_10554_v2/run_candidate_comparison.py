"""Compare frozen v5 with candidate-first clarification, without code repair."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomllib

from openai import OpenAI

from evaluation.analysis.proxy_response_10554_v2.run_clarification import make_fixture
from evaluation.utils.answer_check import run_answer_check
from evaluation.utils.candidate_followup import REVIEW_SYSTEM, _parse_review, run_candidate_followup
from evaluation.utils.controlled_proxy import ControlledProxy


PROJECT = Path(__file__).resolve().parents[3]
SCENARIOS = ('ideal', 'partial', 'partial_after_unavailable', 'ideal_after_unavailable')
REVIEW_CASES = [
    ('missing-operation', 'partial',
     'Which exact operations and arguments occur after the first successful display and before the error?', 'approve'),
    ('already-given-operation', 'ideal',
     'Which exact operations and arguments occur after the first successful display and before the error?', 'reject'),
    ('unavailable-model', 'partial', 'Can you provide the complete Dimension model definition?', 'reject'),
    ('known-filter-values', 'partial', 'What are the pk__in values on each side of the union?', 'reject'),
    ('known-error', 'partial', 'What is the exact reported exception message?', 'reject'),
    ('vague', 'partial', 'Can you give more details?', 'reject'),
]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('live_started_at'):
            raise ValueError('Do not reroll a started attempt; preserve this output directory')
    else:
        snapshot = root / 'source_snapshot'
        snapshot.mkdir()
        dump(snapshot / 'fixture.json', make_fixture())
        hashes = {}
        for rel in [
            'evaluation/utils/answer_check.py', 'evaluation/utils/candidate_followup.py',
            'evaluation/utils/controlled_proxy.py',
            'evaluation/analysis/proxy_response_10554_v1/fixture.json',
            'evaluation/analysis/proxy_response_10554_v2/run_clarification.py',
            'evaluation/analysis/proxy_response_10554_v2/run_candidate_comparison.py',
        ]:
            source = PROJECT / rel
            hashes[rel] = hashlib.sha256(source.read_bytes()).hexdigest()
            # Original fixture has the same basename; preserve it separately.
            shutil.copy2(source, snapshot / ('original_fixture.json' if rel.endswith('v1/fixture.json') else source.name))
        manifest = {
            'protocol': 'candidate-first-comparison-v1',
            'created_at': datetime.now().astimezone().isoformat(),
            'source_sha256': hashes, 'repair_runs': 0, 'max_followups': 2,
            'plan': [{'scenario': scenario,
                      'methods': ['baseline_v5', 'candidate_v1'] if index % 2 == 0 else ['candidate_v1', 'baseline_v5']}
                     for index, scenario in enumerate(SCENARIOS)],
            'review_cases': REVIEW_CASES,
            'acceptance': {
                'ideal': 'No actual followup; reject redundant/unavailable candidates.',
                'partial': 'Ask specifically for the missing intervening operation; exact F3 restored.',
                'unknown': 'Do not ask again for explicitly unavailable model definitions.',
                'proxy': 'Manually inspect rule match; receiving F3 alone is insufficient.',
            },
            'limitations': 'One instance, one trajectory per method/scenario; synthetic unknown histories; same Qwen model in both roles, separate calls; no repair efficacy claim.',
        }
        dump(manifest_path, manifest)
    if args.prepare_only:
        print('Prepared: ' + str(root), flush=True)
        return
    for rel, digest in manifest['source_sha256'].items():
        if hashlib.sha256((PROJECT / rel).read_bytes()).hexdigest() != digest:
            raise ValueError('Source changed after preparation: ' + rel)
    settings = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_qwen_coder']
    bypass = set(filter(None, (os.environ.get('NO_PROXY', '') + ',' + os.environ.get('no_proxy', '')).split(',')))
    bypass.update(('localhost', '127.0.0.1', '::1'))
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(sorted(bypass))
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(name, '')
        if value.lower().startswith('socks://'):
            os.environ[name] = 'socks5://' + value[len('socks://'):]
    client = OpenAI(api_key=settings['api_key'], base_url=settings['base_url'], timeout=120, max_retries=0)
    model = settings['model'].removeprefix('openai/')

    class Model:
        def __init__(self, json_output):
            self.json_output = json_output

        def completion(self, messages):
            kwargs = {'response_format': {'type': 'json_object'}} if self.json_output else {}
            return client.chat.completions.create(
                model=model, messages=messages, temperature=settings.get('temperature', 0),
                top_p=settings.get('top_p', 1), **kwargs,
            )

    fixture = json.loads((root / 'source_snapshot/fixture.json').read_text())
    manifest.update(live_started_at=datetime.now().astimezone().isoformat(), model=model,
                    temperature=settings.get('temperature', 0), top_p=settings.get('top_p', 1),
                    api_retries=0, semantic_candidate_retries=0)
    dump(manifest_path, manifest)
    summary = {}
    for item in manifest['plan']:
        scenario = item['scenario']
        condition = scenario.split('_')[0]
        for method in item['methods']:
            folder = root / scenario / method
            folder.mkdir(parents=True)
            proxy = ControlledProxy(fixture, condition, client, model, folder / 'interactions.json',
                                    settings.get('temperature', 0), settings.get('top_p', 1))
            exchanges = [proxy.initial_exchange()]
            if scenario.endswith('_after_unavailable'):
                seeded = {'question': 'Can you provide the complete Dimension model definition?',
                          'answer': 'I do not have that information in the supplied source.'}
                exchanges.append(seeded)
                proxy.public_history.append(dict(seeded))
            dump(folder / 'initial_public_context.json', {'task_summary': fixture['public_task']['summary'], 'exchanges': exchanges})
            print(f'Running {scenario}/{method}', flush=True)
            if method == 'baseline_v5':
                record = run_answer_check(Model(True), fixture['public_task']['summary'], exchanges,
                                          proxy, folder / 'answer_check.json')
            else:
                record = run_candidate_followup(Model(False), Model(True), fixture['public_task']['summary'],
                                                exchanges, proxy, folder / 'answer_check.json')
            facts = [f for e in proxy.interactions[1:] for f in e.get('fact_ids', [])]
            result = {'status': record['status'], 'followups': record['followups_used'],
                      'stop_reason': record['stop_reason'], 'f3_received': 'F3' in facts,
                      'rules': [r for e in proxy.interactions[1:] for r in e.get('matched_rule_ids', [])],
                      'error': record['error']}
            summary.setdefault(scenario, {})[method] = result
            dump(root / 'summary.json', summary)
            print(json.dumps(result), flush=True)
    review_results = []
    for case_id, condition, candidate, expected in REVIEW_CASES:
        initial = fixture['initial_responses']
        messages = [{'role': 'system', 'content': REVIEW_SYSTEM},
                    {'role': 'user', 'content': json.dumps({
                        'task_summary': fixture['public_task']['summary'],
                        'public_exchanges': [{'question': initial['question'], 'answer': initial[condition]['answer']}],
                        'candidate': candidate,
                    }, ensure_ascii=False)}]
        result = {'case': case_id, 'expected': expected, 'candidate': candidate,
                  'request_messages': messages}
        try:
            response = Model(True).completion(messages)
            result['response'] = response.model_dump()
            result['review'] = _parse_review(response.choices[0].message)
            result['pass'] = result['review']['verdict'] == expected
        except Exception as error:
            result.update(error=f'{type(error).__name__}: {error}', **{'pass': False})
        review_results.append(result)
        dump(root / 'review_cases.json', review_results)
        print(f'Review boundary {case_id}: {result["pass"]}', flush=True)
    manifest['live_finished_at'] = datetime.now().astimezone().isoformat()
    dump(manifest_path, manifest)
    print('Finished: ' + str(root), flush=True)


if __name__ == '__main__':
    main()
