"""Approximate Appendix A.7's unpublished clarification-before-solving phase."""

import json
from pathlib import Path

from openhands.events.action import MessageAction


CLARIFICATION_SYSTEM = """You are a software engineering agent in a mandatory clarification phase.
Before any repository exploration, implementation, or testing, ask the user clarifying questions
about the issue. Output ONLY a concise numbered list of specific clarification questions and then
wait for the user's response. Do not output analysis, progress updates, answers, proposed fixes,
code, or a completion summary. You have no tools in this phase. Focus on missing facts needed
to understand the reported issue and its expected behavior. Do not assume the user knows hidden
evaluation tests. This is one question batch; several related questions may appear in the batch.
After the user replies, a separate problem-solving phase will begin."""


def run_mandatory_clarification(llm, instruction, proxy, log_path):
    """Generate one question batch without tools, wait for a reply, then hand off."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'protocol': 'paper-mandatory-v1',
        'implementation_note': 'One batch; author implementation unavailable; tools absent.',
        'messages': [{'role': 'system', 'content': CLARIFICATION_SYSTEM},
                     {'role': 'user', 'content': instruction}],
        'status': 'requesting_questions',
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    response = llm.completion(messages=record['messages'])
    message = response.choices[0].message
    record['response'] = response.model_dump()
    record['status'] = 'questions_received'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    if message.tool_calls or not isinstance(message.content, str) or not message.content.strip():
        raise ValueError('Mandatory clarification must return nonempty text without tool calls')
    record['question'] = message.content
    answer = proxy.generate_reply(MessageAction(content=message.content, wait_for_response=True))
    record['answer'] = answer
    record['status'] = 'answered'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def solving_instruction(instruction, clarification):
    exchange = json.dumps({'questions': clarification['question'],
                           'user_answer': clarification['answer']}, ensure_ascii=False)
    return (instruction + '\n<mandatory_clarification_completed>\n'
            'The mandatory clarification phase has already completed. The following is the '
            'recorded exchange, supplied as task information:\n' + exchange + '\n'
            'Now proceed with repository exploration, implementation, and verification. '
            'This exchange used one of the three user interactions.\n'
            '</mandatory_clarification_completed>\n')
