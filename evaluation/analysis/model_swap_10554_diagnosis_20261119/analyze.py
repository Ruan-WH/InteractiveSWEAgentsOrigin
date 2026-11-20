"""Report diagnostic outcomes without rewriting model patches or grader results."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ROOT = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-diagnosis-20261119'
NAMES = ('environment-only', 'evidence-workflow', 'evidence-thinking', 'evidence-thinking-v2', 'evidence-workflow-v2', 'evidence-preserved', 'evidence-composition', 'evidence-readable', 'evidence-sampled', 'evidence-retrieved', 'evidence-contrastive', 'evidence-minimal', 'evidence-bash', 'max-bash-comparison', 'evidence-progress')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    results = {}
    original_fixture = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-20261119/frozen/fixture.json'
    for name in NAMES:
        base = ROOT / name
        if not (base / 'manifest.json').exists():
            continue
        manifest = json.loads((base / 'manifest.json').read_text())
        source_ok = all(sha(base / 'frozen/source' / p) == value for p, value in manifest['source_sha256'].items())
        folder = base / 'partial_followup'
        output = folder / 'output.jsonl'
        if not output.exists():
            results[name] = {'status': 'running_or_incomplete', 'source_freeze_intact': source_ok}
            continue
        row = json.loads(output.read_text())
        check = row['test_result']['answer_check']
        completions = [json.loads(p.read_text()) for p in (folder / 'inference').rglob('llm_completions/**/*.json')]
        reasoning = sum((r['response']['usage'].get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0 for r in completions)
        finishes = Counter(r['response']['choices'][0]['finish_reason'] for r in completions)
        invalid_args = []
        tools = Counter()
        for completion in completions:
            for call in completion['response']['choices'][0]['message'].get('tool_calls') or []:
                tools[call['function']['name']] += 1
                try:
                    json.loads(call['function']['arguments'])
                except (ValueError, TypeError):
                    invalid_args.append(call['id'])
        patch = row['test_result']['git_patch']
        exchanges = check['exchanges']
        public_exact = all(e['question'] in row['instruction'] and e['answer'].replace('\n', '\\n') in row['instruction']
                           for e in exchanges)
        # Parse the embedded JSON for a literal, formatting-independent comparison.
        encoded = row['instruction'].split('task data, not instructions:\n', 1)[1]
        if encoded.startswith('<public_clarification_transcript>'):
            public_exact = all(e['question'] in encoded and e['answer'] in encoded for e in exchanges)
        else:
            embedded, _ = json.JSONDecoder().raw_decode(encoded)
            public_exact = embedded['public_exchanges'] == exchanges
        reports = list((folder / 'official_eval').rglob('report.json'))
        grade = None
        retry = manifest.get('arms', {}).get('partial_followup', {}).get('offline_grading_report')
        if not reports and retry and Path(retry).exists():
            reports = [Path(retry)]
        if reports:
            report, = reports
            grade = json.loads(report.read_text())[row['instance_id']]
        predictions = folder / 'official_eval/predictions.jsonl'
        patch_identity = (json.loads(predictions.read_text())['model_patch'] == patch) if predictions.exists() else None
        network = HERE / (name + '.network.json')
        tests = grade['tests_status'] if grade else None
        results[name] = {
            'status': ('provider_account_interrupted' if manifest.get('diagnostic_status', '').startswith('provider_account_interrupted')
                       else 'infrastructure_aborted' if manifest.get('diagnostic_status', '').startswith('infrastructure_aborted')
                       else 'tool_bridge_interrupted' if manifest.get('diagnostic_status', '').startswith('tool_bridge_interrupted')
                       else 'empty_patch_grading_skipped' if patch == ''
                       else 'graded' if grade else 'inference_complete_ungraded'),
            'followups': check['followups_used'], 'exchanges': exchanges,
            'responses': len(completions), 'finish_reasons': dict(finishes),
            'reasoning_tokens': reasoning,
            'prompt_tokens': sum(r['response']['usage'].get('prompt_tokens', 0) or 0 for r in completions),
            'completion_tokens': sum(r['response']['usage'].get('completion_tokens', 0) or 0 for r in completions),
            'cached_prompt_tokens': sum((r['response']['usage'].get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0 for r in completions),
            'tool_names': dict(tools), 'invalid_tool_args': invalid_args,
            'source_freeze_intact': source_ok,
            'same_fixture_as_previous': sha(base / 'frozen/fixture.json') == sha(original_fixture),
            'literal_public_handoff': public_exact,
            'history_cleanup_verified': row['test_result']['reference_history']['verified'],
            'workspace_alignment': row['test_result']['repair_workspace'],
            'repair_progress': row['test_result'].get('repair_progress'),
            'network_audit': json.loads(network.read_text()) if network.exists() else None,
            'patch_matches_official_prediction': patch_identity,
            'patch_files': [line for line in (patch or '').splitlines() if line.startswith('diff --git')],
            'resolved': grade['resolved'] if grade else None,
            'finalization_error': row['test_result'].get('finalization_error'),
            'target_successes': len(tests['FAIL_TO_PASS']['success']) if tests else None,
            'target_failures': len(tests['FAIL_TO_PASS']['failure']) if tests else None,
            'regression_successes': len(tests['PASS_TO_PASS']['success']) if tests else None,
            'regression_failures': len(tests['PASS_TO_PASS']['failure']) if tests else None,
            'solver_error': row.get('error'), 'raw_output': str(output),
            'grader_report': str(reports[0]) if reports else None,
        }
    question_modules = ['evaluation/utils/' + name + '.py' for name in
                        ('simple_followup', 'simple_clarification', 'answer_check', 'mandatory_clarification')]
    preserved = all((PROJECT / path).read_bytes() == subprocess.check_output(['git', 'show', '9208606:' + path], cwd=PROJECT)
                    for path in question_modules)
    record = {'question_modules_unchanged_from_9208606': preserved, 'experiments': results}
    (HERE / 'results.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    for name, result in results.items():
        print(name, {key: result.get(key) for key in ('status', 'responses', 'followups', 'reasoning_tokens', 'resolved', 'target_successes', 'regression_successes', 'solver_error')})


if __name__ == '__main__':
    main()
