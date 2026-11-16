"""A bounded clarification loop with one scope-specific model call per check."""

import json
from pathlib import Path

from openhands.events.action import MessageAction

from evaluation.utils.simple_followup import (
    EVIDENCE, ANSWER_COMPLETENESS, public_messages, is_sequence_report,
    is_unknown_only, repeats_closed_question, public_evidence_parts,
)
from evaluation.utils.answer_check import _normalized


class SimpleJSONChecker:
    """JSON-mode transport matching the frozen clarification pilot."""

    def __init__(self, client, model):
        self.client = client
        self.model = model.removeprefix('openai/')

    def completion(self, messages):
        return self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, top_p=1,
            response_format={'type': 'json_object'},
        )


def decide_simple_followup(llm, summary, exchanges):
    """One call, two-field output. No independent reviewer or private expected answer."""
    closed = [e['question'] for e in exchanges if is_unknown_only(e['answer'])]
    known = [e for e in exchanges if not is_unknown_only(e['answer'])]
    if not known:
        return {'status': 'completed', 'question': '', 'closed_questions': closed,
                'stop_reason': 'only_unavailable_history', 'request_messages': [], 'response': None}
    system = EVIDENCE if is_sequence_report(summary) else ANSWER_COMPLETENESS
    messages = public_messages(system, summary, known)
    record = {'request_messages': messages, 'closed_questions': closed}
    try:
        response = llm.completion(messages=messages)
    except Exception as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}')
        return record
    record['response'] = response.model_dump()
    try:
        message = response.choices[0].message
        if message.tool_calls:
            raise ValueError('Followup check has no tools')
        value = json.loads(message.content)
        if (not isinstance(value, dict) or set(value) != {'trigger_evidence', 'question'} or
                not isinstance(value['question'], str)):
            raise ValueError('Need evidence and question fields')
        evidence, question = value['trigger_evidence'], value['question'].strip()
        if evidence is None:
            if not question:
                raise ValueError('Missing evidence needs a question')
        elif (not isinstance(evidence, str) or not evidence.strip() or question or
              not public_evidence_parts(evidence, known, allow_composite=True)):
            raise ValueError('Stop needs public evidence and empty question')
        record.update(trigger_evidence=evidence, raw_question=question, question=question, status='completed')
        if evidence is not None:
            record['evidence_parts'] = public_evidence_parts(evidence, known, allow_composite=True)
        if question and repeats_closed_question(question, closed):
            record.update(question='', stop_reason='closed_question_overlap')
    except (ValueError, TypeError) as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}')
    return record


def run_simple_clarification(llm, task_summary, exchanges, proxy, log_path, max_followups=2):
    """Pilot: scope selection + answer evidence/question; no separate reviewer.

    Validated with hidden_deepseek_flash. Existing evaluation defaults are untouched.
    Every public answer reaches the handoff, including source-unavailable replies.
    """
    if type(max_followups) is not int or not 0 <= max_followups <= 2:
        raise ValueError('max_followups must be 0, 1 or 2')
    public = []
    for exchange in exchanges:
        if any(not isinstance(exchange.get(k), str) or not exchange[k].strip() for k in ('question', 'answer')):
            raise ValueError('Need nonempty public question and answer')
        public.append({k: exchange[k] for k in ('question', 'answer')})
    if not public or not isinstance(task_summary, str) or not task_summary.strip():
        raise ValueError('Need summary and at least one exchange')
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'protocol': 'simple-focused-answer-v1', 'status': 'checking',
              'task_summary': task_summary, 'exchanges': public, 'checks': [],
              'max_followups': max_followups, 'followups_used': 0,
              'stop_reason': None, 'unresolved_information': '', 'error': None,
              'unavailable_information': []}

    def save():
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')

    save()
    try:
        while True:
            check = decide_simple_followup(llm, task_summary, public)
            record['checks'].append(check)
            save()
            if check['status'] != 'completed':
                raise ValueError(check.get('error', 'Invalid check'))
            for question in check.get('closed_questions', []):
                if question not in record['unavailable_information']:
                    record['unavailable_information'].append(question)
            question = check['question']
            record['unresolved_information'] = question or (
                check.get('raw_question', '') if check.get('stop_reason') else '')
            if not question:
                record['stop_reason'] = check.get('stop_reason') or 'evidence_supplied'
                break
            if record['followups_used'] >= max_followups:
                record['stop_reason'] = 'followup_budget_exhausted'
                break
            if any(_normalized(question) == _normalized(e['question']) for e in public):
                record['stop_reason'] = 'repeated_question'
                break
            record['status'] = 'awaiting_proxy'
            save()
            answer = proxy.generate_reply(MessageAction(content=question, wait_for_response=True))
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError('Proxy returned empty answer')
            previous_answer = public[-1]['answer']
            public.append({'question': question, 'answer': answer})
            record['followups_used'] += 1
            record['status'] = 'checking'
            save()
            if _normalized(answer) == _normalized(previous_answer):
                record['stop_reason'] = 'repeated_answer_no_progress'
                break
        record['status'] = 'completed'
    except Exception as error:
        record.update(status='failed', stop_reason='check_or_proxy_error',
                      error=f'{type(error).__name__}: {error}')
    if hasattr(proxy, 'clarification_closed'):
        proxy.clarification_closed = True
    save()
    return record
