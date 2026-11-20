"""Revise an assisted diagnostic candidate using only actual public execution feedback."""
import argparse
import json
import os
from pathlib import Path
import re
import tomllib

from openai import OpenAI

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--solver-config', default='hidden_qwen38_flash_sampled')
    args = parser.parse_args()
    task = json.loads((HERE / 'focused_feedback_input.json').read_text())
    previous = json.loads(args.previous.read_text())['response']
    feedback = json.loads(args.validation.read_text())
    baseline_tests = json.loads((HERE / 'retrieval_preflight.json').read_text())
    paths = set(re.findall(r'File "/testbed/(tests/[^\"]+)"', feedback.get('existing_tests_output', '')))
    tests = [item for item in baseline_tests['files'] if item['path'] in paths]
    chunks = ['PUBLIC TASK SUMMARY:', task['public_task']['task_summary']]
    for index, exchange in enumerate(task['public_task']['public_exchanges'], 1):
        chunks.extend([f'PUBLIC QUESTION {index}:', exchange['question'], 'PUBLIC ANSWER:', exchange['answer']])
    chunks.extend(['ADDITIONAL INDEPENDENT BASELINE OBSERVATION (diagnostic assistance):',
                   json.dumps(task['additional_independent_baseline_observation'], ensure_ascii=False, indent=2)])
    for source in task['baseline_source_context']:
        chunks.append('ORIGINAL BASELINE SOURCE: ' + source['path'] + ' / class ' + source['class'])
        for method in source['methods']:
            chunks.extend(['```python', method['source'], '```'])
    for item in tests:
        chunks.extend(['EXISTING PUBLIC BASELINE TEST SOURCE: ' + item['path'], '```python', item['source'], '```'])
    chunks.extend(['PREVIOUS MODEL PROPOSAL:', json.dumps(previous, ensure_ascii=False, indent=2),
                   'ACTUAL EXECUTION FEEDBACK (no hidden tests):', json.dumps(feedback, ensure_ascii=False, indent=2)])
    messages = [{'role': 'system', 'content': 'Repair the current ordered-projection failure using the supplied public task, source, and actual execution feedback. The historical report is a regression invariant, not proof of the mechanism. The previous proposal was applied exactly as written and failed. Return JSON with exactly observation (brief explanation) and edits (list of path, old_str, new_str). Return COMPLETE edits relative to the ORIGINAL BASELINE source supplied here, not incremental edits relative to the previous failed proposal. Every old_str must match exactly one baseline block. Preserve existing public test contracts and result ordering/shape; do not modify tests, suppress ordering, or fetch upstream fixes. No reference repair or hidden test output is supplied.'},
                {'role': 'user', 'content': '\n\n'.join(chunks)}]
    (HERE / (args.name + '.input.json')).write_text(json.dumps({'messages': messages, 'diagnostic_only': True}, ensure_ascii=False, indent=2)+'\n')
    cfg = tomllib.loads((PROJECT / 'config.toml').read_text())['llm'][args.solver_config]
    extra = dict(cfg['extra_body'], thinking_budget=16384)
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(key, '')
        if value.startswith('socks://'):
            os.environ[key] = value.replace('socks://', 'socks5://', 1)
    api = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'], timeout=600, max_retries=0)
    requested_model = cfg['model'].removeprefix('openai/')
    response = api.chat.completions.create(model=requested_model, messages=messages,
                 response_format={'type':'json_object'}, temperature=1.0, top_p=0.95,
                 max_tokens=24576, extra_body=extra)
    result = json.loads(response.choices[0].message.content)
    record = {'protocol': 'assisted-local-repair-diagnostic', 'not_standard_pipeline': True,
              'model': response.model, 'requested_model': requested_model, 'response': result, 'usage': response.usage.model_dump(),
              'requested_temperature':1.0, 'requested_top_p':0.95, 'extra_body':extra,
              'feedback_file':str(args.validation), 'public_test_files':[item['path'] for item in tests]}
    (HERE / (args.name + '.response.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'observation':result['observation'], 'edit_paths':[e['path'] for e in result['edits']], 'usage':record['usage']},ensure_ascii=False))


if __name__ == '__main__':
    main()
