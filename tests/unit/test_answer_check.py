"""Offline checks of the public-only, bounded clarification control flow."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from litellm import ModelResponse

from evaluation.utils.answer_check import (
    PUBLIC_EVIDENCE_SYSTEM,
    _parse_evidence,
    checked_solving_instruction,
    run_answer_check,
)


def response(value):
    return ModelResponse(choices=[{'index': 0, 'message': {
        'role': 'assistant', 'content': value if isinstance(value, str) else json.dumps(value),
    }}])


def ask(question='What happens between the first successful display and the failure?'):
    return {'decision': 'ask', 'missing_information': 'The exact intervening operation.',
            'behavioral_impact': 'The supplied steps do not identify the failure trigger.',
            'question': question, 'reason': 'The initial display succeeds but the task reports a later failure.'}


def stop(missing=''):
    return {'decision': 'stop', 'missing_information': missing,
            'behavioral_impact': '', 'question': '',
            'reason': 'No further useful reporter question is needed.'}


def run(tmp_path, replies, answers=(), exchanges=None, limit=2):
    decisions = iter(response(r) for r in replies)

    def complete(messages):
        if messages[0]['content'] == PUBLIC_EVIDENCE_SYSTEM:
            return response({'reported_failure_transition': [], 'steps': [], 'unavailable_information': [], 'missing_transition_details': []})
        return next(decisions)

    llm = SimpleNamespace(completion=Mock(side_effect=complete))
    proxy = SimpleNamespace(generate_reply=Mock(side_effect=answers))
    record = run_answer_check(
        llm, 'Re-evaluating the original query after using a derived query fails.',
        exchanges or [{'question': 'What reproduces the issue?', 'answer': 'The first display of qs succeeds.'}],
        proxy, tmp_path / 'check.json', max_followups=limit,
    )
    return record, llm, proxy


def test_sufficient_answer_stops_without_user_call(tmp_path):
    record, llm, proxy = run(tmp_path, [stop()])
    assert record['status'] == 'completed'
    assert record['stop_reason'] == 'checker_stopped'
    assert record['followups_used'] == 0
    assert record['unresolved_information'] == ''
    proxy.generate_reply.assert_not_called()
    assert set(llm.completion.call_args.kwargs) == {'messages'}


def test_followup_uses_only_public_data_and_all_answers_reach_solver(tmp_path):
    exchanges = [{'question': 'What reproduces the issue?', 'answer': 'First display succeeds.',
                  'source_review': 'PRIVATE_FULL_ISSUE', 'condition': 'partial',
                  'hidden_facts': 'PRIVATE_OMISSION_KEY'}]
    answer = "Evaluate qs.order_by().values_list('pk', flat=True), then display qs again."
    record, llm, proxy = run(tmp_path, [ask(), stop()], [answer], exchanges)
    first = json.loads(llm.completion.call_args_list[0].kwargs['messages'][1]['content'])
    assert first['public_exchanges'] == [{'question': exchanges[0]['question'], 'answer': exchanges[0]['answer']}]
    requests = str(llm.completion.call_args_list)
    assert 'PRIVATE_FULL_ISSUE' not in requests and 'PRIVATE_OMISSION_KEY' not in requests
    assert '"condition"' not in requests
    message = proxy.generate_reply.call_args.args[0]
    assert message.wait_for_response and message.content == ask()['question']
    second = json.loads(llm.completion.call_args_list[2].kwargs['messages'][1]['content'])
    assert second['public_exchanges'][-1]['answer'] == answer
    handoff = checked_solving_instruction('TASK', record)
    assert answer in handoff and 'First display succeeds.' in handoff
    assert 'PRIVATE_' not in handoff and 'request_messages' not in handoff
    assert json.loads((tmp_path / 'check.json').read_text()) == record


def test_two_followup_limit_keeps_unresolved_gap_and_final_assessment(tmp_path):
    record, llm, proxy = run(
        tmp_path, [ask('What operation follows?'), ask('Which arguments?'), ask('What expected value?')],
        ['There is another operation.', 'The operation takes a field name.'],
    )
    assert proxy.generate_reply.call_count == 2
    assert llm.completion.call_count == 6
    assert record['stop_reason'] == 'followup_budget_exhausted'
    assert record['unresolved_information']
    assert len(record['exchanges']) == 3


def test_unavailable_gap_does_not_prevent_asking_a_different_gap(tmp_path):
    record, _, proxy = run(
        tmp_path, [ask(), stop('Model definition unavailable; operation sequence supplied.')],
        ["The intervening call is qs.order_by().values_list('pk', flat=True)."],
        [{'question': 'Can you provide the model definition?',
          'answer': 'The source does not contain the model definition.'}],
    )
    assert proxy.generate_reply.call_count == 1
    assert record['unresolved_information'].startswith('Model definition unavailable')


def test_repeated_question_does_not_spend_another_user_reply(tmp_path):
    question = 'Which exact operation?'
    record, _, proxy = run(tmp_path, [ask(question)], exchanges=[{'question': question, 'answer': 'I do not know.'}])
    assert record['stop_reason'] == 'repeated_question'
    proxy.generate_reply.assert_not_called()


def test_identical_answer_stops_no_progress(tmp_path):
    record, llm, proxy = run(tmp_path, [ask()], ['The first display of qs succeeds.'])
    assert record['stop_reason'] == 'repeated_answer_no_progress'
    assert record['followups_used'] == 1
    assert llm.completion.call_count == 2 and proxy.generate_reply.call_count == 1


def test_invalid_json_retries_do_not_change_public_history(tmp_path):
    record, llm, proxy = run(tmp_path, ['not JSON', stop()])
    assert record['status'] == 'completed'
    assert len(record['checks'][0]['attempts']) == 2
    assert 'validation_error' in record['checks'][0]['attempts'][0]
    assert len(record['exchanges']) == 1
    proxy.generate_reply.assert_not_called()
    assert llm.completion.call_count == 3


@pytest.mark.parametrize('bad', [
    'not JSON', {'decision': 'ask'}, {**ask(), 'behavioral_impact': ''},
    {**stop(), 'question': 'An inconsistent question?'},
])
def test_invalid_checks_are_logged_as_failure_not_sufficient(tmp_path, bad):
    record, llm, proxy = run(tmp_path, [bad, bad, bad])
    assert record['status'] == 'failed' and record['error']
    assert record['stop_reason'] == 'check_or_proxy_error'
    assert llm.completion.call_count == 4
    proxy.generate_reply.assert_not_called()
    assert '"check_status": "failed"' in checked_solving_instruction('TASK', record)


def test_empty_proxy_answer_is_not_accepted(tmp_path):
    record, _, _ = run(tmp_path, [ask()], [''])
    assert record['status'] == 'failed'
    assert record['followups_used'] == 0 and len(record['exchanges']) == 1


def test_zero_budget_assesses_but_does_not_ask(tmp_path):
    record, _, proxy = run(tmp_path, [ask()], limit=0)
    assert record['stop_reason'] == 'followup_budget_exhausted'
    proxy.generate_reply.assert_not_called()


@pytest.mark.parametrize('limit', [-1, 3, True])
def test_invalid_followup_budget_is_rejected_before_calls(tmp_path, limit):
    with pytest.raises(ValueError, match='max_followups'):
        run(tmp_path, [], limit=limit)


def test_evidence_cannot_quote_questions_as_source_facts():
    ledger = {'reported_failure_transition': [], 'steps': [], 'unavailable_information': [], 'missing_transition_details': [
        {'description': 'A proposed operation', 'source_ids': ['Q0']},
    ]}
    message = response(ledger).choices[0].message
    with pytest.raises(ValueError, match='known public source IDs'):
        _parse_evidence(message, 'A later evaluation fails.', [
            {'question': 'Run imagined_call()', 'answer': 'The first display succeeds.'},
        ])


def test_reported_transition_must_reference_summary():
    ledger = {'reported_failure_transition': [
        {'description': 'First evaluation', 'source_ids': ['A0_L0']},
    ], 'steps': [], 'unavailable_information': [], 'missing_transition_details': []}
    with pytest.raises(ValueError, match='public summary'):
        _parse_evidence(response(ledger).choices[0].message, 'A later evaluation fails.', [
            {'question': 'Steps?', 'answer': 'The first display succeeds.'},
        ])


def test_unavailable_context_reaches_solver_even_when_stop_omits_it(tmp_path):
    unavailable = {'description': 'Model definition', 'source_ids': ['A0_L0']}
    ledger = {'reported_failure_transition': [], 'steps': [],
              'unavailable_information': [unavailable], 'missing_transition_details': []}
    llm = SimpleNamespace(completion=Mock(side_effect=[response(ledger), response(stop())]))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_answer_check(llm, 'A query fails.', [
        {'question': 'Model?', 'answer': 'No model definition is available.'},
    ], proxy, tmp_path / 'check.json')
    assert record['unavailable_information'] == [{**unavailable, 'evidence': 'No model definition is available.'}]
    assert 'No model definition is available.' in checked_solving_instruction('TASK', record)
    proxy.generate_reply.assert_not_called()


def test_invalid_evidence_fails_before_decision_or_proxy(tmp_path):
    bad = {'reported_failure_transition': [], 'steps': [],
           'unavailable_information': [{'description': 'Imagined fact', 'source_ids': ['INVENTED_SOURCE']}],
           'missing_transition_details': []}
    llm = SimpleNamespace(completion=Mock(side_effect=[response(bad)] * 3))
    proxy = SimpleNamespace(generate_reply=Mock())
    record = run_answer_check(llm, 'A query fails.', [
        {'question': 'Steps?', 'answer': 'Initial display succeeds.'},
    ], proxy, tmp_path / 'check.json')
    assert record['status'] == 'failed'
    assert len(record['checks'][0]['evidence_attempts']) == 3
    assert not record['checks'][0]['attempts']
    proxy.generate_reply.assert_not_called()
