"""Native tool protocol failures must use the controller's recoverable path."""

import json

import pytest
from litellm import ModelResponse

from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.core.exceptions import FunctionCallValidationError
from openhands.events.action import AgentFinishAction, CmdRunAction


def response(name, arguments):
    return ModelResponse(choices=[{
        'index': 0,
        'finish_reason': 'tool_calls',
        'message': {
            'role': 'assistant',
            'content': None,
            'tool_calls': [{
                'id': 'call-regression',
                'type': 'function',
                'function': {'name': name, 'arguments': arguments},
            }],
        },
    }])


@pytest.mark.parametrize(('name', 'arguments'), [
    ('', ''),  # Actual Qwen response that previously terminated the run.
    ('execute_bash', ''),
    ('execute_bash', '{"command":'),
    ('execute_bash', '[]'),
    ('execute_bash', 'null'),
])
def test_malformed_native_call_is_recoverable(name, arguments):
    with pytest.raises(FunctionCallValidationError):
        response_to_actions(response(name, arguments))


def test_valid_native_call_after_invalid_response():
    with pytest.raises(FunctionCallValidationError):
        response_to_actions(response('', ''))
    actions = response_to_actions(response('execute_bash', json.dumps({'command': 'pwd'})))
    assert len(actions) == 1
    assert isinstance(actions[0], CmdRunAction)
    assert actions[0].command == 'pwd'
    assert actions[0].tool_call_metadata.tool_call_id == 'call-regression'


def test_finish_with_valid_empty_object_still_finishes():
    actions = response_to_actions(response('finish', '{}'))
    assert len(actions) == 1
    assert isinstance(actions[0], AgentFinishAction)
