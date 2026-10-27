"""Verify the native response -> waiting message -> proxy request boundary."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litellm import ModelResponse

from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.controller.agent_controller import AgentController
from openhands.controller.state.state import State
from openhands.core.schema import AgentState
from openhands.events.action import CmdRunAction, MessageAction
from openhands.events.event import EventSource


@pytest.fixture
def interaction(monkeypatch):
    monkeypatch.setenv('API_KEY', 'offline-key')
    monkeypatch.setenv('INTERACTION_USER_BASE_URL', 'https://example.invalid/v1')
    return importlib.import_module('evaluation.benchmarks.swe_bench.interact_run_infer')


def native_response(content, tool_calls=None):
    return ModelResponse(choices=[{
        'index': 0,
        'message': {'role': 'assistant', 'content': content, 'tool_calls': tool_calls},
        'finish_reason': 'tool_calls' if tool_calls else 'stop',
    }])


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    'Which column was removed, and what were all required columns?',
    'Let me examine core.py to understand the implementation.',
])
async def test_current_text_reaches_proxy_unchanged(interaction, monkeypatch, text):
    captured = []

    def reply(**kwargs):
        captured.append([dict(message) for message in kwargs['messages']])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='Reported details.'))],
            model='offline', usage=None,
        )

    monkeypatch.setattr(interaction.client.chat.completions, 'create', reply)
    proxy = interaction.FakeUser('full issue', 'hints', 'core.py')
    monkeypatch.setattr(interaction, 'fake_user', proxy, raising=False)
    old = MessageAction(content='An older question?', wait_for_response=True)
    old._source = EventSource.AGENT
    action, = response_to_actions(native_response(text))
    action._source = EventSource.AGENT
    state = State(history=[old, action])
    controller = SimpleNamespace(set_agent_state_to=AsyncMock())

    await AgentController._handle_message_action(controller, action)
    controller.set_agent_state_to.assert_awaited_once_with(AgentState.AWAITING_USER_INPUT)
    assert interaction.fake_user_response(state) == 'Reported details.'
    assert captured[0][-1] == {'role': 'user', 'content': text}
    assert proxy.interactions[0]['question'] == text


def test_tool_call_explanation_is_a_thought_not_a_proxy_message():
    question = 'Which behavior should be preserved?'
    tool = {'id': 'offline-call', 'type': 'function',
            'function': {'name': 'execute_bash', 'arguments': '{"command":"pwd"}'}}
    action, = response_to_actions(native_response(question, [tool]))
    assert isinstance(action, CmdRunAction)
    assert action.thought == question
    assert not isinstance(action, MessageAction)


def test_real_question_after_cap_does_not_reach_proxy(interaction, monkeypatch):
    create = Mock()
    monkeypatch.setattr(interaction.client.chat.completions, 'create', create)
    proxy = interaction.FakeUser('issue', '', '')
    proxy.turns = 3
    monkeypatch.setattr(interaction, 'fake_user', proxy, raising=False)
    action, = response_to_actions(native_response('What is the expected result?'))
    action._source = EventSource.AGENT
    reply = interaction.fake_user_response(State(history=[action]))
    assert 'Do NOT ask' in reply
    create.assert_not_called()
