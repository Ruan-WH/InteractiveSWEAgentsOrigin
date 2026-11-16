import json
from types import SimpleNamespace
from unittest.mock import Mock

from litellm import ModelResponse

from evaluation.utils.simple_followup import generate_simple_followup, is_unknown_only, repeats_closed_question, has_public_evidence, public_evidence_parts


def response(value):
    return ModelResponse(choices=[{'index': 0, 'message': {
        'role': 'assistant', 'content': json.dumps(value),
    }}])


def test_one_call_and_only_public_fields_are_sent():
    llm = SimpleNamespace(completion=Mock(return_value=response({'question': 'What operation follows?'})))
    record = generate_simple_followup(llm, 'A later operation fails.', [
        {'question': 'Reproduction?', 'answer': 'Initial display succeeds.',
         'private_source': 'HIDDEN_SOURCE', 'expected_gap': 'HIDDEN_GAP', 'condition': 'HIDDEN_CONDITION'},
    ], 'direct_en')
    assert record['question'] == 'What operation follows?'
    assert llm.completion.call_count == 1
    assert 'HIDDEN_' not in str(record['request_messages'])


def test_stop_is_distinct_from_malformed_output():
    history = [{'question': 'Steps?', 'answer': 'All steps supplied.'}]
    stop_llm = SimpleNamespace(completion=Mock(return_value=response({'question': ''})))
    bad_llm = SimpleNamespace(completion=Mock(return_value=response({'decision': 'stop'})))
    assert generate_simple_followup(stop_llm, 'Issue.', history, 'direct_en')['status'] == 'completed'
    bad = generate_simple_followup(bad_llm, 'Issue.', history, 'direct_en')
    assert bad['status'] == 'failed' and 'question' not in bad


def test_replay_cannot_pass_fabricated_quote_as_known_step():
    llm = SimpleNamespace(completion=Mock(return_value=response({'steps': [
        {'action': 'A guessed operation', 'quote': 'invented_call()'},
    ]})))
    record = generate_simple_followup(llm, 'Later operation fails.', [
        {'question': 'Steps?', 'answer': 'Initial display succeeds.'},
    ], 'replay')
    assert record['status'] == 'failed'
    assert 'question' not in record


def test_replay_asks_for_null_step_without_inventing_its_call():
    llm = SimpleNamespace(completion=Mock(return_value=response({'steps': [
        {'action': 'Intermediate transformation', 'quote': None},
    ]})))
    record = generate_simple_followup(llm, 'Later operation fails.', [
        {'question': 'Steps?', 'answer': 'Initial display succeeds.'},
    ], 'replay')
    assert 'Intermediate transformation' in record['question']
    assert record['status'] == 'completed'


def test_evidence_stop_requires_a_real_reporter_excerpt():
    history = [{'question': 'Steps?', 'answer': 'obj.change(1); show(obj) fails.'}]
    llm = SimpleNamespace(completion=Mock(return_value=response({
        'trigger_evidence': 'obj.change(1); show(obj) fails.', 'question': '',
    })))
    result = generate_simple_followup(llm, 'Failure after a state change.', history, 'trigger_evidence')
    assert result['status'] == 'completed' and result['question'] == ''
    assert llm.completion.call_count == 1
    llm.completion.return_value = response({'trigger_evidence': 'invented_call()', 'question': ''})
    assert generate_simple_followup(llm, 'Issue.', history, 'trigger_evidence')['status'] == 'failed'


def test_no_evidence_cannot_silently_stop():
    llm = SimpleNamespace(completion=Mock(return_value=response({'trigger_evidence': None, 'question': ''})))
    result = generate_simple_followup(llm, 'Issue.', [{'question': 'Steps?', 'answer': 'Initial success.'}], 'trigger_evidence')
    assert result['status'] == 'failed'


def test_mixed_fact_and_unknown_answer_is_preserved():
    assert is_unknown_only('I do not have that information in the supplied source.')
    assert not is_unknown_only("I don't have version info, but obj.change(1) triggers the error.")
    assert not is_unknown_only('The source does not provide a model definition. Call obj.change(1).')


def test_unknown_only_reply_is_memory_not_generic_information_exhaustion():
    llm = SimpleNamespace(completion=Mock(return_value=response({
        'trigger_evidence': None, 'question': 'What operation occurs after the successful display?',
    })))
    history = [{'question': 'Reproduction?', 'answer': 'Initial display succeeds.'},
               {'question': 'Can you provide the complete model definition?',
                'answer': 'I do not have that information in the supplied source.'}]
    result = generate_simple_followup(llm, 'Failure occurs later.', history, 'trigger_evidence')
    assert result['question'] and result['closed_questions'] == [history[-1]['question']]
    assert 'Initial display succeeds.' in result['request_messages'][1]['content']
    assert 'I do not have that information' not in result['request_messages'][1]['content']
    assert not repeats_closed_question(result['question'], result['closed_questions'])
    assert repeats_closed_question('What is the model definition?', result['closed_questions'])


def test_evidence_whitespace_tolerance_does_not_accept_changed_words_or_cross_answer_join():
    history = [{'question': 'Steps?', 'answer': 'First call succeeds.\n\nThen another call fails.'}]
    assert has_public_evidence('First call succeeds.\nThen another call fails.', history)
    assert not has_public_evidence('First call fails. Then another call succeeds.', history)
    assert not has_public_evidence('First call succeeds. Then another call fails.', [
        {'answer': 'First call succeeds.'}, {'answer': 'Then another call fails.'},
    ])


def test_composite_evidence_needs_supported_complete_sentence_spans():
    history = [{'answer': 'The input was x.'}, {'answer': 'The output was y.'}]
    assert public_evidence_parts('The input was x. The output was y.', history, True) == [
        'The input was x.', 'The output was y.',
    ]
    assert not public_evidence_parts('The input was y.', history, True)
    assert not public_evidence_parts('The input was x. Therefore the output was y.', history, True)
