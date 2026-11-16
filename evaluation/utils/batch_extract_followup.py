"""Two small tasks: propose questions, then locate their answers in public history."""

import json

from evaluation.utils.simple_followup import public_messages


QUESTION_SYSTEM = """Before code repair, list at most THREE specific questions needed to
understand the reported behavior. Start with the exact trigger operations and their order,
then inputs/conditions, then actual versus expected results. Existing named objects can be
treated as given; do not ask for their full definitions, repository locations, causes or fixes.
Only output questions, no analysis. Return JSON {"questions":["...","...","..."]}.
"""

EXTRACT_SYSTEM = """Answer each numbered question by extracting text from the reporter's
existing answers, not by solving the bug. Do not guess or add missing facts.
Return JSON {"answers":[{"index":0,"quote":"exact contiguous text from a reporter answer, or null if the answer is not given","unavailable":false}]}.
One entry per question. If a reporter explicitly says the requested information is absent,
quote that statement and set unavailable=true. Otherwise unavailable=false.
A mention of the topic does not answer a request for exact operations/arguments. Null means
the question remains unanswered; it does NOT mean it was already answered. An exact answer
anywhere in the conversation is sufficient, even if not directly addressed to this question.
No verdict, relevance judgment, question rewriting, diagnosis or explanation.
"""


def batch_extract(llm, summary, exchanges):
    """Return a selected unanswered question; caller records errors and raw attempts."""
    record = {'attempts': []}
    try:
        messages = public_messages(QUESTION_SYSTEM, summary, exchanges)
        response = llm.completion(messages=messages)
        record['attempts'].append({'request_messages': messages, 'response': response.model_dump()})
        message = response.choices[0].message
        if message.tool_calls:
            raise ValueError('Question phase has no tools')
        questions = json.loads(message.content)
        if not isinstance(questions, dict) or set(questions) != {'questions'}:
            raise ValueError('Expected questions list')
        questions = questions['questions']
        if (not isinstance(questions, list) or not 1 <= len(questions) <= 3 or
                not all(isinstance(q, str) and q.strip() for q in questions)):
            raise ValueError('Expected one to three nonempty questions')
        record['questions'] = questions
        messages = public_messages(EXTRACT_SYSTEM, summary, exchanges)
        messages[1]['content'] += '\nQUESTIONS:\n' + json.dumps(list(enumerate(questions)))
        response = llm.completion(messages=messages)
        record['attempts'].append({'request_messages': messages, 'response': response.model_dump()})
        message = response.choices[0].message
        if message.tool_calls:
            raise ValueError('Answer extraction has no tools')
        answers = json.loads(message.content)
        if not isinstance(answers, dict) or set(answers) != {'answers'} or not isinstance(answers['answers'], list):
            raise ValueError('Expected answers list')
        answers = answers['answers']
        if len(answers) != len(questions):
            raise ValueError('Need one answer per question')
        indices = set()
        for answer in answers:
            if not isinstance(answer, dict) or set(answer) != {'index', 'quote', 'unavailable'}:
                raise ValueError('Invalid extraction shape')
            index, quote = answer['index'], answer['quote']
            if type(index) is not int or not 0 <= index < len(questions) or index in indices:
                raise ValueError('Invalid or duplicate question index')
            indices.add(index)
            if type(answer['unavailable']) is not bool:
                raise ValueError('unavailable must be boolean')
            if quote is not None and (not isinstance(quote, str) or not quote.strip() or not any(
                    quote in e['answer'] for e in exchanges)):
                raise ValueError('Quote must occur verbatim in a public answer')
            if answer['unavailable'] and quote is None:
                raise ValueError('Unavailable requires a quoted absence statement')
        record['answers'] = sorted(answers, key=lambda a: a['index'])
        missing = next((a['index'] for a in record['answers'] if a['quote'] is None), None)
        record['question'] = questions[missing] if missing is not None else ''
        record['status'] = 'completed'
    except (ValueError, TypeError) as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}')
    return record
