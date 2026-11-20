"""Public transcript formatting must not add private facts or lose evidence."""
import json

import pytest

from evaluation.utils.answer_check import checked_solving_instruction
from evaluation.utils.public_repair_handoff import readable_handoff, minimal_handoff


def test_preserves_full_multiline_answers_and_status_without_private_fields():
    exchanges = [{'question': 'Which object?', 'answer': 'The object is combined.\n```python\nx = combine(a, b)\n```'},
                 {'question': 'What happens next?', 'answer': 'Evaluate a derived copy. Then display x again.'}]
    record = {'exchanges': exchanges, 'status': 'completed', 'stop_reason': 'evidence_supplied',
              'unresolved_information': 'Database setup unavailable.', 'unavailable_information': [],
              'error': None, 'private_source': 'PRIVATE_FIX', 'checks': [{'private_audit': 'PRIVATE_HINT'}]}
    instruction = checked_solving_instruction('PUBLIC ISSUE', record)
    rendered = readable_handoff(instruction, record)
    assert rendered.startswith('PUBLIC ISSUE')
    for exchange in exchanges:
        assert exchange['question'] in rendered and exchange['answer'] in rendered
    assert rendered.index(exchanges[0]['answer']) < rendered.index(exchanges[1]['answer'])
    assert 'PRIVATE_' not in rendered
    assert 'Database setup unavailable.' in rendered
    assert 'do not redefine named objects' in rendered
    assert 'No further user replies' in rendered


def test_rejects_mismatched_transcript():
    record = {'exchanges': [{'question': 'Q', 'answer': 'A'}], 'status': 'completed',
              'stop_reason': 'evidence_supplied', 'unresolved_information': '', 'error': None}
    instruction = checked_solving_instruction('PUBLIC ISSUE', record)
    changed = dict(record, exchanges=[{'question': 'Q', 'answer': 'Different'}])
    with pytest.raises(ValueError, match='exchanges differ'):
        readable_handoff(instruction, changed)


def test_keeps_metadata_as_data():
    record = {'exchanges': [{'question': 'Q', 'answer': 'A'}], 'status': 'failed',
              'stop_reason': 'check_or_proxy_error', 'unresolved_information': 'Unknown', 'error': 'Timeout'}
    rendered = readable_handoff(checked_solving_instruction('PUBLIC ISSUE', record), record)
    status = json.loads(rendered.split('<clarification_status>\n')[1].split('\n</clarification_status>')[0])
    assert status['check_status'] == 'failed' and status['checker_error'] == 'Timeout'


def test_minimal_packet_has_one_workflow_without_legacy_claims_or_private_facts():
    record = {'task_summary': 'PUBLIC REPORTED ISSUE', 'exchanges': [
        {'question': 'Which object?', 'answer': 'The object is combined.\nUse the same object later.'}],
        'status': 'completed', 'stop_reason': 'evidence_supplied', 'unresolved_information': '',
        'error': None, 'private_source': 'PRIVATE_FIX'}
    context = {'files': [{'path': 'tests/test_public.py', 'matched_api_names': ['combine'],
                          'source': 'def test_public():\n    pass\n'}]}
    instruction = minimal_handoff('/workspace/repo', record, context, max_actions=70)
    assert record['task_summary'] in instruction and record['exchanges'][0]['answer'] in instruction
    assert context['files'][0]['source'] in instruction
    assert instruction.count('<repair_workflow>') == 1
    assert instruction.index('<repair_workflow>') > instruction.index('</existing_baseline_test_context>')
    assert '70 actions' in instruction
    assert '/repo directory' not in instruction and 'PRIVATE_FIX' not in instruction
    assert "I've already taken care" not in instruction and 'fine if it\'s very long' not in instruction
