import json

import pytest

from evaluation.utils.proxy_prompt import parse_grounded_reply


@pytest.mark.parametrize('evidence', [
    [{'source': 'hints', 'quote': 'warn first'}],
    [{'source': 'patch', 'quote': 'change it now'}],
    [{'source': 'hints', 'quote': ''}],
    'change it now',
])
def test_reject_unsupported_evidence(evidence):
    with pytest.raises(ValueError):
        parse_grounded_reply(json.dumps({
            'message_kind': 'question', 'evidence': evidence, 'final_requirement': 'Change now', 'answer': 'Change now',
        }), 'Original proposal: warn first', 'change it now')


def test_quoted_question_mark_is_preserved_in_answer():
    reply = {'message_kind': 'question', 'evidence': [{'source': 'issue', 'quote': 'Got "what?"'}],
             'final_requirement': 'unknown', 'answer': 'The observed error is `what?`.'}
    assert parse_grounded_reply(json.dumps(reply), 'Got "what?"', '') == reply


def test_progress_acknowledges_without_endorsing_or_reanswering():
    reply = {'message_kind': 'progress', 'evidence': [],
             'final_requirement': 'unknown', 'answer': 'Your implementation is correct.'}
    assert parse_grounded_reply(json.dumps(reply), 'issue', '')['answer'] == (
        'Acknowledged. Please continue working on the task.'
    )


def test_excel_and_double_escaped_newlines_do_not_reject_supported_quote():
    reply = {'message_kind': 'question', 'evidence': [
        {'source': 'issue', 'quote': 'line one_x000D_\\nline two'},
    ], 'final_requirement': 'unknown', 'answer': "I don't have that information"}
    assert parse_grounded_reply(json.dumps(reply), 'line one_x000D_\nline two', '') == reply
