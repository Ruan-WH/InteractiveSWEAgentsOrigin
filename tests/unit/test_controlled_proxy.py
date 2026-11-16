"""Offline safeguards for the fixed Ideal/Partial pilot adapter."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from litellm import ModelResponse

from evaluation.utils.controlled_proxy import ControlledProxy


@pytest.fixture
def fixture():
    path = Path(__file__).resolve().parents[2] / 'evaluation/analysis/proxy_response_10554_v1/fixture.json'
    return json.loads(path.read_text())


def make_proxy(tmp_path, fixture, condition, replies=()):
    create = Mock(side_effect=[ModelResponse(choices=[{'index': 0, 'message': {
        'role': 'assistant', 'content': json.dumps(r),
    }}]) for r in replies])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    proxy = ControlledProxy(fixture, condition, client, 'offline', tmp_path / 'proxy.json')
    return proxy, create


@pytest.mark.parametrize('condition', ['ideal', 'partial'])
def test_initial_answer_is_exact_and_does_not_call_model(tmp_path, fixture, condition):
    proxy, create = make_proxy(tmp_path, fixture, condition)
    exchange = proxy.initial_exchange()
    assert exchange['answer'] == fixture['initial_responses'][condition]['answer']
    assert set(exchange) == {'question', 'answer'}
    assert proxy.turns == 1
    create.assert_not_called()


def test_targeted_followup_records_public_answer_without_private_ids(tmp_path, fixture):
    answer = "Evaluate qs.order_by().values_list('pk', flat=True), then show the original qs again."
    proxy, create = make_proxy(tmp_path, fixture, 'partial', [{
        'message_kind': 'question', 'matched_rule_ids': ['R1'],
        'fact_ids': ['F2', 'F3', 'F4'], 'answer': answer,
    }])
    proxy.initial_exchange()
    result = proxy.generate_reply(SimpleNamespace(content='What happens between success and failure?'))
    assert result == answer
    assert proxy.public_history[-1] == {'question': 'What happens between success and failure?', 'answer': answer}
    assert proxy.interactions[-1]['matched_rule_ids'] == ['R1']
    assert '{{' not in create.call_args.kwargs['messages'][0]['content']
    proxy.clarification_closed = True
    assert 'Do NOT ask' in proxy.generate_reply(SimpleNamespace(content='More?'))
    assert create.call_count == 1


def test_partial_cannot_claim_f3_under_error_only_rule(tmp_path, fixture):
    bad = {'message_kind': 'question', 'matched_rule_ids': ['R4'],
           'fact_ids': ['F3'], 'answer': "qs.order_by().values_list('pk', flat=True)"}
    proxy, create = make_proxy(tmp_path, fixture, 'partial', [bad, bad, bad])
    proxy.initial_exchange()
    with pytest.raises(ValueError, match='outside its matched rules'):
        proxy.generate_reply(SimpleNamespace(content='What is the exact error?'))
    assert create.call_count == 3 and proxy.turns == 1
    assert len(proxy.public_history) == 1


def test_v2_derives_text_and_fact_ids_from_reviewed_rules(tmp_path, fixture):
    fixture['followup_answer_mode'] = 'rule_templates_v2'
    proxy, create = make_proxy(tmp_path, fixture, 'ideal', [{
        'message_kind': 'question', 'matched_rule_ids': ['R5'],
    }])
    proxy.initial_exchange()
    result = proxy.generate_reply(SimpleNamespace(content='What are the model fields?'))
    assert result == 'I do not have that information in the supplied source.'
    assert proxy.interactions[-1]['fact_ids'] == []
    assert 'PRIVATE_SOURCE' not in str(create.call_args.kwargs['messages'])


def test_v2_rejects_free_generated_answer_then_renders_template(tmp_path, fixture):
    fixture['followup_answer_mode'] = 'rule_templates_v2'
    proxy, create = make_proxy(tmp_path, fixture, 'ideal', [
        {'message_kind': 'question', 'matched_rule_ids': ['R5'], 'answer': 'Unasked diagnosis'},
        {'message_kind': 'question', 'matched_rule_ids': ['R5']},
    ])
    proxy.initial_exchange()
    result = proxy.generate_reply(SimpleNamespace(content='What are the model fields?'))
    assert 'Unasked diagnosis' not in result
    assert create.call_count == 2 and proxy.turns == 2
    assert len(proxy.public_history) == 2


@pytest.mark.parametrize('condition,discloses_f3', [('ideal', True), ('partial', False)])
def test_v2_generic_request_preserves_condition_difference(tmp_path, fixture, condition, discloses_f3):
    from evaluation.analysis.proxy_response_10554_v2.run_clarification import make_fixture

    fixture = make_fixture()
    proxy, _ = make_proxy(tmp_path, fixture, condition, [{
        'message_kind': 'question', 'matched_rule_ids': ['R7'],
    }])
    proxy.initial_exchange()
    result = proxy.generate_reply(SimpleNamespace(content='Can you repeat the minimal example?'))
    assert ('values_list' in result) == discloses_f3
    assert ('F3' in proxy.interactions[-1]['fact_ids']) == discloses_f3


def test_v2_diagnosis_keeps_source_uncertainty(tmp_path, fixture):
    fixture['followup_answer_mode'] = 'rule_templates_v2'
    proxy, _ = make_proxy(tmp_path, fixture, 'partial', [{
        'message_kind': 'question', 'matched_rule_ids': ['R8'],
    }])
    proxy.initial_exchange()
    result = proxy.generate_reply(SimpleNamespace(content='Was copying confirmed as the fix?'))
    assert 'tentatively' in result and 'not establish' in result
    assert proxy.interactions[-1]['fact_ids'] == ['F6']
