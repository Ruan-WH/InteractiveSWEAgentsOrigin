"""Probe tool-call compatibility of thinking mode without any benchmark content."""
import json
import os
from pathlib import Path
import toml
import tomllib
from openai import OpenAI

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]


def main():
    path = PROJECT / 'config.toml'
    cfg = tomllib.loads(path.read_text())['llm']['hidden_qwen38_flash']
    settings = dict(cfg, extra_body={'enable_thinking': True, 'thinking_budget': 4096, 'preserve_thinking': False})
    name = 'hidden_qwen38_flash_thinking'
    existing = tomllib.loads(path.read_text())['llm'].get(name)
    if existing is None:
        with path.open('a') as stream:
            stream.write('\n' + toml.dumps({'llm': {name: settings}}))
    else:
        assert existing == settings
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(key, '')
        if value.startswith('socks://'):
            os.environ[key] = value.replace('socks://', 'socks5://', 1)
    client = OpenAI(api_key=settings['api_key'], base_url=settings['base_url'], timeout=180, max_retries=0)
    tools = [{'type': 'function', 'function': {'name': 'inspect_value', 'description': 'Read an integer',
              'parameters': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}}}]
    messages = [{'role': 'user', 'content': 'Use inspect_value to read n. Then return n squared as plain text.'}]
    result = {'extra_body': settings['extra_body'], 'turns': []}
    first = client.chat.completions.create(model='qwen3.8-flash', messages=messages, tools=tools,
                temperature=0, top_p=1, max_tokens=8192, extra_body=settings['extra_body'])
    message = first.choices[0].message
    assert message.tool_calls and message.tool_calls[0].function.name == 'inspect_value'
    result['turns'].append({'model': first.model, 'finish_reason': first.choices[0].finish_reason,
                           'reasoning_characters': len(getattr(message, 'reasoning_content', '') or ''),
                           'tool_names': [t.function.name for t in message.tool_calls],
                           'usage': first.usage.model_dump()})
    # Match current CodeAct's history representation, which does not retain reasoning_content.
    messages.append({'role': 'assistant', 'content': message.content or '',
                     'tool_calls': [t.model_dump() for t in message.tool_calls]})
    for call in message.tool_calls:
        messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': '7'})
    second = client.chat.completions.create(model='qwen3.8-flash', messages=messages, tools=tools,
                temperature=0, top_p=1, max_tokens=8192, extra_body=settings['extra_body'])
    answer = second.choices[0].message
    assert '49' in answer.content
    result['turns'].append({'model': second.model, 'finish_reason': second.choices[0].finish_reason,
                           'reasoning_characters': len(getattr(answer, 'reasoning_content', '') or ''),
                           'content': answer.content, 'usage': second.usage.model_dump()})
    result['official_parameter_documentation'] = 'https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions'
    (HERE / 'thinking_probe.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
