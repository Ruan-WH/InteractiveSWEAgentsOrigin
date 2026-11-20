"""Exercise the actual LLM -> CodeAct -> request path without benchmark data."""
import hashlib
import json
import os
from pathlib import Path
import toml
import tomllib

from openhands.agenthub.codeact_agent.codeact_agent import CodeActAgent
from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.core.config import AgentConfig, LLMConfig
from openhands.events.event import EventSource
from openhands.llm.llm import LLM

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]


def main():
    path = PROJECT / 'config.toml'
    cfg = tomllib.loads(path.read_text())['llm']['hidden_qwen38_flash_thinking']
    settings = dict(cfg, extra_body=dict(cfg['extra_body'], preserve_thinking=True))
    name = 'hidden_qwen38_flash_preserved'
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
    cfg = LLMConfig(**dict(settings, log_completions=False))
    llm = LLM(cfg)
    agent = CodeActAgent(llm=llm, config=AgentConfig())
    tools = [tool for tool in agent.tools if tool['function']['name'] in ('execute_bash', 'finish')]
    initial = {'role': 'user', 'content': 'Call execute_bash with command `printf 7` once. After its result is supplied, finish the task.'}
    first = llm.completion(messages=[initial], tools=tools)
    message = first.choices[0].message
    reasoning = getattr(message, 'reasoning_content', None)
    assert reasoning and message.tool_calls[0].function.name == 'execute_bash'
    action, = response_to_actions(first)
    action._source = EventSource.AGENT
    pending = {}
    agent.get_action_message(action, pending)
    outgoing = llm.format_messages_for_llm(list(pending.values()))
    assert outgoing[0]['reasoning_content'] == reasoning
    assert reasoning not in str(outgoing[0]['content'])
    second = llm.completion(messages=[initial] + outgoing + [
        {'role': 'tool', 'tool_call_id': message.tool_calls[0].id, 'name': 'execute_bash', 'content': '7'}], tools=tools)
    final = second.choices[0].message
    assert final.tool_calls and final.tool_calls[0].function.name == 'finish'
    result = {'benchmark_content_used': False, 'first_model': first.model, 'second_model': second.model,
              'provider_state_preserved_exactly': True, 'provider_state_characters': len(reasoning),
              'provider_state_sha256': hashlib.sha256(reasoning.encode()).hexdigest(),
              'first_tool': message.tool_calls[0].function.name, 'second_tool': final.tool_calls[0].function.name,
              'usage': [first.usage.model_dump(), second.usage.model_dump()], 'extra_body': settings['extra_body']}
    (HERE / 'preserved_probe.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
