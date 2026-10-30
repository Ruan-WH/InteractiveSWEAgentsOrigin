import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from litellm import ModelResponse

from evaluation.utils.mandatory_clarification import (
    run_mandatory_clarification, solving_instruction,
)


def response(content, tool_calls=None):
    return ModelResponse(choices=[{'index': 0, 'message': {
        'role': 'assistant', 'content': content, 'tool_calls': tool_calls,
    }}])


def test_questions_wait_for_proxy_and_are_handed_to_solver(tmp_path):
    path = tmp_path / 'phase.json'
    questions = '1. What input reproduces the issue?\n2. What result do you expect?'
    llm = SimpleNamespace(completion=Mock(return_value=response(questions)))

    def answer(message):
        assert path.exists()
        assert json.loads(path.read_text())['status'] == 'questions_received'
        assert message.content == questions
        assert message.wait_for_response
        return 'The input is X; the expected result is Y.'

    proxy = SimpleNamespace(generate_reply=Mock(side_effect=answer))
    record = run_mandatory_clarification(llm, 'summarized task', proxy, path)
    assert set(llm.completion.call_args.kwargs) == {'messages'}
    assert llm.completion.call_args.kwargs['messages'][1]['content'] == 'summarized task'
    assert json.loads(path.read_text())['status'] == 'answered'
    assert questions in record['question']
    instruction = solving_instruction('summarized task', record)
    assert 'The input is X' in instruction
    assert 'one of the three' in instruction


@pytest.mark.parametrize('bad', [response(''), response(None), response('', [{
    'id': 'bad', 'type': 'function', 'function': {'name': 'execute_bash', 'arguments': '{}'},
}])])
def test_invalid_phase_output_does_not_call_proxy(tmp_path, bad):
    llm = SimpleNamespace(completion=Mock(return_value=bad))
    proxy = SimpleNamespace(generate_reply=Mock())
    with pytest.raises(ValueError, match='nonempty text without tool calls'):
        run_mandatory_clarification(llm, 'task', proxy, tmp_path / 'phase.json')
    proxy.generate_reply.assert_not_called()
