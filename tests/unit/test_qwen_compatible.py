"""Check provider state survives the transport, action history, and serializers."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openhands.agenthub.codeact_agent.codeact_agent import CodeActAgent
from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.core.config import AgentConfig, LLMConfig
from openhands.core.message import Message, TextContent
from openhands.events.event import EventSource
from openhands.llm.llm import LLM


@pytest.mark.parametrize('function_calling', [False, True])
def test_provider_state_is_separate_from_displayed_content(function_calling):
    message = Message(role='assistant', content=[TextContent(text='Inspect a file.')],
                      reasoning_content='opaque-provider-state', function_calling_enabled=function_calling)
    serialized = message.model_dump()
    assert serialized['reasoning_content'] == 'opaque-provider-state'
    assert 'opaque-provider-state' not in str(serialized['content'])
    assert 'reasoning_content' not in Message(role='user', content=[]).model_dump()


def test_qwen_state_roundtrips_through_codeact_native_tools(monkeypatch):
    raw = {'id': 'response-one', 'model': 'qwen3.8-flash', 'created': 1,
           'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
               'role': 'assistant', 'content': '', 'reasoning_content': 'opaque-provider-state',
               'tool_calls': [{'id': 'call-one', 'type': 'function', 'function': {
                   'name': 'execute_bash', 'arguments': '{"command":"pwd"}'}}]}}],
           'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
    create = Mock(return_value=SimpleNamespace(model_dump=lambda: raw))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr('openhands.llm.qwen_compatible._client', Mock(return_value=client))
    cfg = LLMConfig(model='openai/qwen3.8-flash', api_key='offline', base_url='https://example.invalid/v1',
                    native_tool_calling=True, max_input_tokens=131072, max_output_tokens=8192,
                    extra_body={'enable_thinking': True, 'preserve_thinking': True}, log_completions=False)
    llm = LLM(cfg)
    agent = CodeActAgent(llm=llm, config=AgentConfig())
    response = llm.completion(messages=[{'role': 'user', 'content': 'Inspect the working directory.'}])
    action, = response_to_actions(response)
    action._source = EventSource.AGENT
    pending = {}
    assert agent.get_action_message(action, pending) == []
    outgoing = llm.format_messages_for_llm(list(pending.values()))
    assert outgoing[0]['reasoning_content'] == 'opaque-provider-state'
    assert outgoing[0]['tool_calls'][0]['id'] == 'call-one'
    llm.completion(messages=outgoing + [{'role': 'tool', 'tool_call_id': 'call-one', 'content': '/workspace/repo'}])
    assert create.call_args.kwargs['messages'][0]['reasoning_content'] == 'opaque-provider-state'
    assert create.call_args.kwargs['model'] == 'qwen3.8-flash'


@pytest.mark.parametrize('model,extra', [('openai/qwen3.8-flash', {'preserve_thinking': False}),
                                       ('openai/qwen3.8-flash', None), ('gpt-4o', {'preserve_thinking': True})])
def test_other_modes_keep_existing_transport(monkeypatch, model, extra):
    original = Mock()
    monkeypatch.setattr('openhands.llm.llm.litellm_completion', original)
    llm = LLM(LLMConfig(model=model, extra_body=extra))
    assert llm._completion_unwrapped.func is original
