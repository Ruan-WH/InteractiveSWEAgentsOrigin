"""Extract reproducible action accounting and causal evidence from preserved runs."""
import json
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OLD = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-offline-20261119'


def audit(arm):
    row = json.loads((OLD / arm / 'output.jsonl').read_text())
    actions = [e for e in row['history'] if e.get('source') == 'agent' and 'action' in e]
    observations = {e.get('cause'): e for e in row['history'] if 'observation' in e}
    counts = Counter()
    entries = []
    response_ids = set()
    for step, event in enumerate(actions, 1):
        args = event.get('args', {})
        response = event.get('tool_call_metadata', {}).get('model_response', {})
        if response.get('id'):
            response_ids.add(response['id'])
        command = args.get('command', args.get('translated_ipython_code', ''))
        path = args.get('path', '')
        if event['action'] == 'edit' and '/django/db/' in path:
            category = 'production_edit'
        elif event['action'] == 'edit':
            category = 'scratch_or_test_edit'
        elif event['action'] == 'read':
            category = 'file_read'
        elif 'runtests.py' in command or 'run-tests.py' in command:
            category = 'test_runner_command'
        elif 'repro' in command and ('python' in command or "file_text" in command):
            category = 'reproduction_command'
        else:
            category = 'other_command_or_tool'
        counts[category] += 1
        entries.append({'step': step, 'event_id': event['id'], 'category': category,
                        'command': command, 'path': path, 'thought': args.get('thought', ''),
                        'observation': observations.get(event['id'], {}).get('content', '')})
    (HERE / (arm + '.actions.json')).write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n')
    return {'agent_actions': len(actions), 'distinct_action_response_ids': len(response_ids),
            'action_categories': dict(counts),
            'first_production_edit_step': next((e['step'] for e in entries if e['category'] == 'production_edit'), None),
            'first_test_runner_step': next((e['step'] for e in entries if e['category'] == 'test_runner_command'), None),
            'answer_check_followups': row['test_result']['answer_check']['followups_used'],
            'patch_files': [line[11:] for line in row['test_result']['git_patch'].splitlines() if line.startswith('diff --git ')],
            'solver_error': row['error']}


def main():
    summary = {arm: audit(arm) for arm in ('ideal_no_followup', 'partial_followup', 'partial_no_followup')}
    (HERE / 'trajectory_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
