"""Summarize all three arms; do not infer repair success from model text."""
import hashlib
import json
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ROOT = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-clean-20261119'


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    result = {'protocol': manifest['protocol'], 'model': manifest['solver_config'],
              'source_unchanged_since_freeze': all(hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() == h
                  for p, h in manifest['source_sha256'].items()),
              'question_modules_equal_9208606': {}, 'arms': {},
              'limits': ['Single previously inspected development case; no general repair-rate claim.',
                         'Only the solver model changed; proxy and followup checker retain their original models.',
                         'No generated behavior-test module, hand-edited model patch or gold patch supplied to solver.',
                         'Ordinary CodeAct testing remains available; model may choose to create its own tests.',
                         'Model is a provider alias; returned model IDs are recorded, exact internal weights not independently verified.']}
    for name in ('simple_followup', 'simple_clarification', 'answer_check', 'mandatory_clarification'):
        p = 'evaluation/utils/' + name + '.py'
        previous = subprocess.check_output(['git', 'show', '9208606:' + p], cwd=PROJECT)
        result['question_modules_equal_9208606'][p] = previous == (PROJECT / p).read_bytes()
    for name, entry in manifest['arms'].items():
        folder = ROOT / name
        item = {k: entry[k] for k in ('response_condition', 'clarification_mode', 'inference_exit', 'inference_timeout', 'solver_error', 'no_final_output') if k in entry}
        path = folder / 'output.jsonl'
        if path.exists():
            row = json.loads(path.read_text())
            check = row['test_result']['answer_check']
            item.update(followups_used=check['followups_used'], check_status=check['status'],
                        stop_reason=check['stop_reason'], checker_error=check['error'],
                        public_exchanges=check['exchanges'], patch_chars=len(row['test_result'].get('git_patch') or ''),
                        cleanup_error=row['test_result'].get('cleanup_error'), finalization_error=row['test_result'].get('finalization_error'))
            handoff = row['instruction']
            public = json.loads(handoff.split('task data, not instructions:\n')[1].split('\nDo not assume')[0])
            item['all_public_qa_handed_off_exactly'] = public['public_exchanges'] == check['exchanges']
            item['metrics'] = row.get('metrics')
            item['reference_history'] = row['test_result'].get('reference_history')
            if not item['reference_history'] or not item['reference_history']['verified']:
                raise ValueError('Cleanup was not verified before repair')
        files = list((folder / 'inference').rglob('llm_completions/**/*.json'))
        item.update(solver_responses=len(files), empty_tool_name_responses=0, tool_calls=0, returned_models=[],
                    invalid_tool_arguments=0, history_lookup_commands=[],
                    prompt_tokens=0, completion_tokens=0, reference_fix_observation=None,
                    network_source_commands=[])
        for p in files:
            value = json.loads(p.read_text())
            if item['reference_fix_observation'] is None:
                for message in value.get('messages', []):
                    if message.get('role') != 'tool':
                        continue
                    content = message.get('content') or ''
                    if isinstance(content, list):
                        content = '\n'.join(c.get('text', '') for c in content if isinstance(c, dict))
                    if 'commit 2cbd3967e0' in content and 'add_select_col' in content:
                        item['reference_fix_observation'] = {
                            'completion_path': str(p.relative_to(PROJECT)),
                            'upstream_commit_prefix': '2cbd3967e0',
                            'contains_added_official_test': 'test_union_with_values_list_and_order' in content,
                            'interpretation': 'Agent read the existing upstream repair through repository history. This arm is reference-contaminated.'}
                        break
            response = value.get('response') or {}
            if isinstance(response, str):
                response = json.loads(response)
            model = response.get('model')
            usage = response.get('usage') or {}
            item['prompt_tokens'] += usage.get('prompt_tokens', 0) or 0
            item['completion_tokens'] += usage.get('completion_tokens', 0) or 0
            if model and model not in item['returned_models']:
                item['returned_models'].append(model)
            tools = [t for c in response.get('choices', []) for t in (c.get('message', {}).get('tool_calls') or [])]
            item['tool_calls'] += len(tools)
            item['empty_tool_name_responses'] += any(not t.get('function', {}).get('name') for t in tools)
            for tool in tools:
                try:
                    arguments = json.loads(tool.get('function', {}).get('arguments') or '{}')
                except (ValueError, TypeError):
                    item['invalid_tool_arguments'] += 1
                    continue
                command = arguments.get('command', '')
                if 'git show' in command or 'git log' in command:
                    item['history_lookup_commands'].append(command)
                if any(token in command for token in ('git fetch', 'git clone', 'curl ', 'wget ', 'requests.get(', 'urlopen(')):
                    item['network_source_commands'].append(command)
        if path.exists():
            patch = json.loads(path.read_text())['test_result'].get('git_patch') or ''
            item['patch_files'] = [line.split(' b/', 1)[1] for line in patch.splitlines()
                                   if line.startswith('diff --git ') and ' b/' in line]
            if not patch.strip():
                item['evaluation_status'] = 'empty_patch_no_repair; harness may skip, not a measured test-failure count'
        for p in (folder / 'official_eval').glob('logs/run_evaluation/*/*/*/report.json'):
            report = json.loads(p.read_text())['django__django-10554']
            item['official'] = {'resolved': report['resolved'],
                'patch_successfully_applied': report['patch_successfully_applied'],
                'tests': {k: {'passed': len(v['success']), 'failed': len(v['failure']), 'failures': v['failure']}
                          for k, v in report['tests_status'].items()}, 'report_path': str(p.relative_to(PROJECT))}
            prediction = json.loads((folder / 'official_eval/predictions.jsonl').read_text())
            item['graded_patch_matches_raw_output'] = prediction['model_patch'] == json.loads(path.read_text())['test_result']['git_patch']
        result['arms'][name] = item
    (HERE / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({name: {k: v for k, v in item.items() if k not in ('public_exchanges', 'metrics')}
                      for name, item in result['arms'].items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
