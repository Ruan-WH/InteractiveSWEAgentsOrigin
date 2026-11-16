import json
from types import SimpleNamespace
from unittest.mock import Mock

from litellm import ModelResponse

from evaluation.utils.candidate_followup import run_candidate_followup


def response(value):
    return ModelResponse(choices=[{'index': 0, 'message': {
        'role': 'assistant', 'content': value if isinstance(value, str) else json.dumps(value),
    }}])


def review(approve):
    return {'verdict': 'approve' if approve else 'reject',
            'category': 'useful' if approve else 'already_answered',
            'missing_information': 'Intervening operation' if approve else '',
            'behavioral_impact': 'Identifies failure trigger' if approve else '',
            'reason': 'Concrete gap' if approve else 'Already supplied'}


def test_rejected_candidate_is_not_sent_and_not_rerolled(tmp_path):
    generator = SimpleNamespace(completion=Mock(return_value=response('What model?')))
    reviewer = SimpleNamespace(completion=Mock(return_value=response(review(False))))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_candidate_followup(generator, reviewer, 'A failure.', [
        {'question': 'Reproduction?', 'answer': 'The first display succeeds.'},
    ], proxy, tmp_path / 'record.json')
    assert record['stop_reason'] == 'candidate_rejected'
    assert record['followups_used'] == 0
    proxy.generate_reply.assert_not_called()
    assert generator.completion.call_count == reviewer.completion.call_count == 1


def test_roles_see_only_public_history_and_approved_question_is_verbatim(tmp_path):
    question = 'What operation occurs before the failure?'
    answer = 'Call a derived operation, then display the original again.'
    generator = SimpleNamespace(completion=Mock(side_effect=[response(question), response('Which operation?')]))
    reviewer = SimpleNamespace(completion=Mock(side_effect=[response(review(True)), response(review(False))]))
    proxy = SimpleNamespace(generate_reply=Mock(return_value=answer))
    record = run_candidate_followup(generator, reviewer, 'A later evaluation fails.', [
        {'question': 'Reproduction?', 'answer': 'First display succeeds.',
         'private_source': 'HIDDEN_ORACLE', 'condition': 'HIDDEN_CONDITION'},
    ], proxy, tmp_path / 'record.json')
    assert proxy.generate_reply.call_args.args[0].content == question
    assert proxy.generate_reply.call_args.args[0].wait_for_response
    assert record['exchanges'][-1] == {'question': question, 'answer': answer}
    requests = str(generator.completion.call_args_list) + str(reviewer.completion.call_args_list)
    assert 'HIDDEN_' not in requests
    reviewer_payload = json.loads(reviewer.completion.call_args_list[0].kwargs['messages'][1]['content'])
    assert set(reviewer_payload) == {'task_summary', 'public_exchanges', 'candidate'}
    assert json.loads((tmp_path / 'record.json').read_text()) == record


def test_invalid_review_retries_same_candidate_without_user_turn(tmp_path):
    generator = SimpleNamespace(completion=Mock(return_value=response('Which operation?')))
    reviewer = SimpleNamespace(completion=Mock(side_effect=[response('bad JSON'), response(review(False))]))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_candidate_followup(generator, reviewer, 'Failure.', [
        {'question': 'Steps?', 'answer': 'First display succeeds.'},
    ], proxy, tmp_path / 'record.json')
    assert len(record['checks'][0]['reviewer_attempts']) == 2
    assert generator.completion.call_count == 1
    proxy.generate_reply.assert_not_called()


def test_two_reply_budget_assesses_last_answer_but_never_sends_third(tmp_path):
    generator = SimpleNamespace(completion=Mock(side_effect=[response(q) for q in ('First?', 'Second?', 'Third?')]))
    reviewer = SimpleNamespace(completion=Mock(side_effect=[response(review(True))] * 3))
    proxy = SimpleNamespace(generate_reply=Mock(side_effect=['One answer.', 'Another answer.']))
    record = run_candidate_followup(generator, reviewer, 'Failure.', [
        {'question': 'Steps?', 'answer': 'First display succeeds.'},
    ], proxy, tmp_path / 'record.json')
    assert record['stop_reason'] == 'followup_budget_exhausted'
    assert record['followups_used'] == proxy.generate_reply.call_count == 2
    assert generator.completion.call_count == reviewer.completion.call_count == 3


def test_review_failure_is_explicit_and_never_delivered(tmp_path):
    generator = SimpleNamespace(completion=Mock(return_value=response('Which operation?')))
    reviewer = SimpleNamespace(completion=Mock(side_effect=[response({'verdict': 'approve'})] * 3))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_candidate_followup(generator, reviewer, 'Failure.', [
        {'question': 'Steps?', 'answer': 'First display succeeds.'},
    ], proxy, tmp_path / 'record.json')
    assert record['status'] == 'failed' and record['error']
    proxy.generate_reply.assert_not_called()
