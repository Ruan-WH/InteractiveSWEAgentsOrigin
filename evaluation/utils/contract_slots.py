"""One extraction call plus a deterministic question for the first empty slot."""

import json

from evaluation.utils.simple_followup import public_messages, is_unknown_only, repeats_closed_question


SLOT_SYSTEM = """Fill four behavior-contract fields using ONLY quotes from reporter answers.
Do not decide whether to ask a question, solve code, infer omitted facts, or write explanations.
Return JSON with exactly {"target":[],"trigger":[],"actual":[],"expected":[]}.
Each list contains verbatim contiguous excerpts from reporter answers, or [] if unspecified.
target: concrete affected API/function/method/class name, not just a topic or generic "method".
trigger: input/state conditions AND operations that produce the bad behavior. If the summary
describes a success followed by a later failure, this field must include the intervening
operation and the failing re-evaluation. Setup/first success alone does not fill this field.
If a field must be required, its mere presence does not specify the required-field setting.
actual: the actual result. For a misleading-message defect, quote the exact message, not
"the message is confusing". For bytes rejection, the semantic rejection is sufficient;
do not demand unavailable traceback or bytes samples.
expected: the desired behavior. A semantic rule is sufficient; executable tests are optional.
No standalone project/model definitions are needed. Do not fill a field from the summary
alone or invent missing operations/parameters. Copy quotes, even when they are prose.
"""

QUESTIONS = {
    'target': 'Which concrete function, method, or class exhibits the reported behavior?',
    'trigger': 'What exact inputs, required conditions and operations (including arguments and order) lead from the initial observation to the reported failure?',
    'actual': 'What is the exact actual output or exception message for that case?',
    'expected': 'What result or behavior should occur instead for that case?',
}


def decide_slots(llm, summary, exchanges):
    closed = [e['question'] for e in exchanges if is_unknown_only(e['answer'])]
    known = [e for e in exchanges if not is_unknown_only(e['answer'])]
    if not known:
        return {'status': 'completed', 'question': '', 'stop_reason': 'only_unavailable_history',
                'closed_questions': closed, 'request_messages': [], 'response': None}
    messages = public_messages(SLOT_SYSTEM, summary, known)
    response = llm.completion(messages=messages)
    record = {'request_messages': messages, 'response': response.model_dump(), 'closed_questions': closed}
    try:
        message = response.choices[0].message
        if message.tool_calls:
            raise ValueError('Slot extraction has no tools')
        slots = json.loads(message.content)
        if not isinstance(slots, dict) or set(slots) != set(QUESTIONS):
            raise ValueError('Need exactly four contract slots')
        for excerpts in slots.values():
            if not isinstance(excerpts, list) or not all(
                    isinstance(q, str) and q.strip() and any(q in e['answer'] for e in known)
                    for q in excerpts):
                raise ValueError('Each slot needs a list of verbatim public excerpts')
        empty = next((key for key in QUESTIONS if not slots[key]), None)
        question = QUESTIONS[empty] if empty else ''
        record.update(slots=slots, missing_slot=empty, raw_question=question, question=question, status='completed')
        if question and repeats_closed_question(question, closed):
            record.update(question='', stop_reason='closed_question_overlap')
    except (ValueError, TypeError) as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}')
    return record
