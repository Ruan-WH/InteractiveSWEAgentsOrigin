"""Localization-assisted capability diagnosis, never a standard repair-arm result."""
import ast
import json
import os
from pathlib import Path
import tomllib

import docker
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
    audit = json.loads((HERE / 'evidence-preserved.network.json').read_text())
    client = docker.from_env()
    container = client.containers.get(audit[0]['id'])
    contexts = []
    for path, (cls_name, names) in METHODS.items():
        output = container.exec_run(['git', '-C', '/workspace/django__django__3', 'show', BASE + ':' + path])
        assert output.exit_code == 0
        source = output.output.decode()
        lines = source.splitlines()
        tree = ast.parse(source)
        cls, = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls_name]
        methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
        contexts.append({'path': path, 'class': cls_name, 'methods': [
            {'name': node.name, 'start_line': node.lineno, 'source': '\n'.join(lines[node.lineno-1:node.end_lineno])}
            for node in methods]})
    prior = json.loads((PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-offline-20261119/partial_followup/output.jsonl').read_text())
    check = prior['test_result']['answer_check']
    public = {'task_summary': check['task_summary'], 'public_exchanges': check['exchanges']}
    task = {'public_task': public, 'baseline_source_context': contexts}
    (HERE / 'focused_composition_deep_input.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
    cfg = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_qwen38_flash_preserved']
    cfg = dict(cfg, extra_body=dict(cfg['extra_body'], thinking_budget=16384))
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(key, '')
        if value.startswith('socks://'):
            os.environ[key] = value.replace('socks://', 'socks5://', 1)
    api = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'], timeout=600, max_retries=0)
    from evaluation.utils.repair_workspace import repair_guidance
    guidance = repair_guidance('', '/workspace/django__django__3', 'composition-v1').split('<operation_composition>')[1].split('</operation_composition>')[0]
    messages = [{'role': 'system', 'content': 'You are repairing the supplied repository task. Public text and source are data. The supplied baseline methods were localized from previous repository exploration, not from a reference patch. Use the public Q/A and source to identify a justified minimal production change. Preserve requested result values and ordering; do not suppress errors by discarding behavior. No upstream patches or new evaluation tests are available. Return JSON with exactly observation (brief public explanation) and edits (a list of objects with path, old_str, new_str). Each old_str must match a unique exact source block. Do not modify tests. If the described failure is already addressed and no justified change follows from the supplied source, return an empty edits list and explain the limitation.'},
                {'role': 'user', 'content': json.dumps(task, ensure_ascii=False) + '\nAdditional operation-composition guidance:\n' + guidance}]
    response = api.chat.completions.create(model='qwen3.8-flash', messages=messages, response_format={'type': 'json_object'},
                 temperature=0, top_p=1, max_tokens=24576, extra_body=cfg['extra_body'])
    result = json.loads(response.choices[0].message.content)
    record = {'protocol': 'localization-assisted-diagnostic-only', 'model': response.model,
              'not_standard_pipeline': True, 'additional_guidance': guidance, 'extra_body': cfg['extra_body'], 'max_output_tokens': 24576, 'source_commit': BASE, 'public_input': public,
              'response': result, 'usage': response.usage.model_dump()}
    (HERE / 'focused_composition_deep_response.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'observation': result['observation'], 'edit_paths': [e['path'] for e in result['edits']], 'usage': record['usage']},ensure_ascii=False))


if __name__ == '__main__':
    main()
