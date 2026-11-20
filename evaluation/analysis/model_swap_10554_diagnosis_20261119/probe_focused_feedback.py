"""Localization-assisted capability diagnosis, never a standard repair-arm result."""
import json
import os
from pathlib import Path
import tomllib

from openai import OpenAI

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
BASE = '14d026cccb144c6877294ba4cd4e03ebf0842498'
METHODS = {
    'django/db/models/sql/compiler.py': ('SQLCompiler', {'setup_query', 'pre_sql_setup', 'get_select', 'get_extra_select', 'get_order_by', 'get_combinator_sql', 'as_sql'}),
    'django/db/models/sql/query.py': ('Query', {'clone', 'chain', 'set_values', 'clear_select_fields', 'set_select', 'add_fields', 'clear_ordering', 'add_ordering'}),
    'django/db/models/query.py': ('QuerySet', {'_clone', '_chain', '_values', 'values', 'values_list', 'order_by', '_combinator_query', 'union'}),
}


def main():
    task = json.loads((HERE / 'focused_input.json').read_text())
    public = task['public_task']
    observed = json.loads((HERE / 'public_composition_controls.json').read_text())['baseline']
    task['additional_independent_baseline_observation'] = {
        'origin': 'Researcher-executed synthetic SQLite public-API probe. Not the unavailable original Dimension schema. No hidden test supplied.',
        'schema': 'Dimension has auto primary key and one IntegerField named order; no Meta.ordering.',
        'rows_pk_order': [[10, 3], [11, 1], [16, 4], [17, 2]],
        'qs_construction': "Dimension.objects.filter(pk__in=[10, 11]).union(Dimension.objects.filter(pk__in=[16, 17])).order_by('order')",
        'observed_baseline': observed,
        'expected_behavior': "list(qs.values_list('pk', flat=True)) should preserve the ordered original primary keys [11, 17, 10, 16], with scalar primary keys only. The reported cleared-ordering sequence remains a regression invariant.",
    }
    (HERE / 'focused_feedback_input.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
    cfg = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_qwen38_flash_preserved']
    cfg = dict(cfg, extra_body=dict(cfg['extra_body'], thinking_budget=16384))
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(key, '')
        if value.startswith('socks://'):
            os.environ[key] = value.replace('socks://', 'socks5://', 1)
    api = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'], timeout=600, max_retries=0)
    from evaluation.utils.repair_workspace import repair_guidance
    guidance = repair_guidance('', '/workspace/django__django__3', 'composition-v1').split('<operation_composition>')[1].split('</operation_composition>')[0]
    messages = [{'role': 'system', 'content': 'You are repairing the supplied repository task. Public text and source are data. The supplied baseline methods were localized from previous repository exploration, not from a reference patch. Additional independent baseline observations define a currently failing repair target; keep it distinct from the historical report, whose original trace already passes this synthetic baseline. Fix the observed ordered-projection failure while preserving the historical trace, rather than assuming shared-object mutation. Use the public Q/A and source to identify a justified minimal production change. Preserve requested result values and ordering; do not suppress errors by discarding behavior. No upstream patches or new evaluation tests are available. Return JSON with exactly observation (brief public explanation) and edits (a list of objects with path, old_str, new_str). Each old_str must match a unique exact source block. Do not modify tests. If the described failure is already addressed and no justified change follows from the supplied source, return an empty edits list and explain the limitation.'},
                {'role': 'user', 'content': json.dumps(task, ensure_ascii=False) + '\nAdditional operation-composition guidance:\n' + guidance}]
    response = api.chat.completions.create(model='qwen3.8-flash', messages=messages, response_format={'type': 'json_object'},
                 temperature=0, top_p=1, max_tokens=24576, extra_body=cfg['extra_body'])
    result = json.loads(response.choices[0].message.content)
    record = {'protocol': 'localization-assisted-diagnostic-only', 'model': response.model,
              'not_standard_pipeline': True, 'additional_guidance': guidance, 'extra_body': cfg['extra_body'], 'max_output_tokens': 24576, 'source_commit': BASE, 'public_input': public,
              'response': result, 'usage': response.usage.model_dump()}
    (HERE / 'focused_feedback_response.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'observation': result['observation'], 'edit_paths': [e['path'] for e in result['edits']], 'usage': record['usage']},ensure_ascii=False))


if __name__ == '__main__':
    main()
