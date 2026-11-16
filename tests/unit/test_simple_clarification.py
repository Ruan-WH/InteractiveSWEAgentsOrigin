import json
from types import SimpleNamespace
from unittest.mock import Mock

from litellm import ModelResponse

from evaluation.utils.answer_check import checked_solving_instruction
from evaluation.utils.simple_clarification import run_simple_clarification


def response(evidence, question=''):
    return ModelResponse(choices=[{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps({
        'trigger_evidence': evidence, 'question': question,
    })}}])


def test_one_followup_restores_context_then_stops_with_one_call_per_check(tmp_path):
    question = 'What operation occurs between the success and the failure?'
    answer = 'obj.change(2); show(obj) fails.'
    llm = SimpleNamespace(completion=Mock(side_effect=[response(None, question), response(answer)]))
    proxy = SimpleNamespace(generate_reply=Mock(return_value=answer))
    record = run_simple_clarification(llm, 'After a change, another display fails.', [
        {'question': 'Steps?', 'answer': 'The first display succeeds.', 'private_source': 'HIDDEN_KEY'},
    ], proxy, tmp_path / 'record.json')
    assert record['followups_used'] == 1 and record['stop_reason'] == 'evidence_supplied'
    assert llm.completion.call_count == 2 and proxy.generate_reply.call_count == 1
    assert proxy.generate_reply.call_args.args[0].content == question
    assert 'HIDDEN_KEY' not in str(llm.completion.call_args_list)
    assert answer in checked_solving_instruction('TASK', record)
    assert json.loads((tmp_path / 'record.json').read_text()) == record


def test_failure_to_prove_evidence_is_not_sufficiency(tmp_path):
    llm = SimpleNamespace(completion=Mock(return_value=response('invented_source()')))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_simple_clarification(llm, 'Issue.', [{'question': 'Steps?', 'answer': 'Initial success.'}],
                                       proxy, tmp_path / 'record.json')
    assert record['status'] == 'failed'
    proxy.generate_reply.assert_not_called()


def test_budget_preserves_unanswered_question_without_sending_it(tmp_path):
    llm = SimpleNamespace(completion=Mock(return_value=response(None, 'Which inputs trigger this?')))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_simple_clarification(llm, 'Issue.', [{'question': 'Steps?', 'answer': 'Initial success.'}],
                                       proxy, tmp_path / 'record.json', max_followups=0)
    assert record['stop_reason'] == 'followup_budget_exhausted'
    assert record['unresolved_information'] == 'Which inputs trigger this?'
    proxy.generate_reply.assert_not_called()


def test_closed_question_guard_preserves_uncertainty_for_solver(tmp_path):
    llm = SimpleNamespace(completion=Mock(return_value=response(None, 'What is the model definition?')))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_simple_clarification(llm, 'Issue.', [
        {'question': 'Reproduction?', 'answer': 'Initial success.'},
        {'question': 'Can you provide the complete model definition?',
         'answer': 'I do not have that information in the supplied source.'},
    ], proxy, tmp_path / 'record.json')
    assert record['stop_reason'] == 'closed_question_overlap'
    assert record['unavailable_information']
    assert record['unresolved_information'] == 'What is the model definition?'
    proxy.generate_reply.assert_not_called()


def test_transport_failure_retains_request_and_never_calls_proxy(tmp_path):
    llm = SimpleNamespace(completion=Mock(side_effect=RuntimeError('connection failed')))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_simple_clarification(llm, 'Issue.', [{'question': 'Steps?', 'answer': 'Initial success.'}],
                                       proxy, tmp_path / 'record.json')
    assert record['status'] == 'failed'
    assert record['checks'][0]['request_messages']
    proxy.generate_reply.assert_not_called()


def test_phase_closes_proxy_before_returning_to_repair(tmp_path):
    llm = SimpleNamespace(completion=Mock(return_value=response('All requested details supplied.')))
    proxy = SimpleNamespace(generate_reply=Mock(), clarification_closed=False)
    record = run_simple_clarification(llm, 'Issue.', [
        {'question': 'Details?', 'answer': 'All requested details supplied.'},
    ], proxy, tmp_path / 'record.json')
    assert record['status'] == 'completed' and proxy.clarification_closed
    proxy.generate_reply.assert_not_called()
