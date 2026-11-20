"""Malformed native tool calls must yield recoverable validation feedback."""
import json

from litellm import ModelResponse
import pytest

from openhands.agenthub.codeact_agent.function_calling import response_to_actions
from openhands.core.exceptions import FunctionCallValidationError


def response(name, arguments):
    return ModelResponse(choices=[{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'call-one', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}])


@pytest.mark.parametrize('name,args', [
    ('str_replace_editor', {'command': 'str_replace', 'old_str': 'old', 'new_str': 'new'}),
    ('str_replace_editor', {'path': '/workspace/source.py'}),
    ('str_replace_editor', {'command': 'view', 'path': ['/workspace/source.py']}),
    ('execute_bash', {}), ('browser', {}), ('web_read', {}),
])
def test_missing_or_mistyped_required_fields_are_recoverable(name, args):
    with pytest.raises(FunctionCallValidationError, match='required|declared types'):
        response_to_actions(response(name, args))


def test_corrected_call_can_execute_after_a_missing_path_call():
    invalid = {'command': 'str_replace', 'old_str': 'old', 'new_str': 'new'}
    with pytest.raises(FunctionCallValidationError):
        response_to_actions(response('str_replace_editor', invalid))
    corrected = dict(invalid, path='/workspace/source.py')
    action, = response_to_actions(response('str_replace_editor', corrected))
    assert action.path == '/workspace/source.py'
    assert action.tool_call_metadata.tool_call_id == 'call-one'
