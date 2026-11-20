"""Small compatibility probe; no issue or repair information supplied."""
import json
import os
from pathlib import Path
import tomllib
from openai import OpenAI


def main():
    root = Path(__file__).resolve().parents[3]
    cfg = tomllib.loads((root / 'config.toml').read_text())['llm']['hidden_qwen_coder']
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(key, '')
        if value.startswith('socks://'):
            os.environ[key] = value.replace('socks://', 'socks5://', 1)
    client = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'], timeout=120, max_retries=0)
    tools = [{'type': 'function', 'function': {'name': 'report_number', 'description': 'Report a number',
              'parameters': {'type': 'object', 'properties': {'number': {'type': 'integer'}}, 'required': ['number']}}}]
    result = {'requested_model': 'qwen3.8-flash', 'tests': []}
    for kind in ('tool', 'json'):
        kwargs = dict(model='qwen3.8-flash', temperature=0, top_p=1, max_tokens=512,
                      extra_body={'enable_thinking': False})
        if kind == 'tool':
            kwargs.update(messages=[{'role': 'user', 'content': 'Call report_number once with number 7.'}],
                          tools=tools, tool_choice={'type': 'function', 'function': {'name': 'report_number'}})
        else:
            kwargs.update(messages=[{'role': 'user', 'content': 'Return JSON only: {"ok":true}'}],
                          response_format={'type': 'json_object'})
        try:
            response = client.chat.completions.create(**kwargs)
            result['tests'].append({'kind': kind, 'response': response.model_dump()})
            print(kind, 'returned_model=', response.model, 'finish=', response.choices[0].finish_reason,
                  'tool_names=', [t.function.name for t in response.choices[0].message.tool_calls or []], flush=True)
        except Exception as error:
            result['tests'].append({'kind': kind, 'error_type': type(error).__name__, 'status_code': getattr(error, 'status_code', None)})
            print(kind, type(error).__name__, getattr(error, 'status_code', None), flush=True)
    (Path(__file__).resolve().parent / 'probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
