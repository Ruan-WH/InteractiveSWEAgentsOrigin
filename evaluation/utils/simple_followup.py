"""Single-call followup policies: no evidence extractor or separate reviewer."""

import json
import re


DIRECT_EN = """Complete the reporter's answer before code repair.
Read the task summary and the whole conversation. What ONE task-critical detail is still
missing from the answer? Ask specifically for that detail. If nothing critical can usefully
be obtained, return an empty question. Use all previous answers; do not ask them again.
An explicitly unavailable fact cannot be obtained. Treat existing objects/setup as given;
ask about missing actions, arguments, sequence, or expected results, not a standalone project.
Do not solve the bug, guess missing code, or request a diagnosis.
Return only JSON: {"question":"one followup, or empty string to stop"}.
"""

DIRECT_ZH = """你只负责补全用户刚才的回答，暂不修复代码。
结合任务摘要和全部问答，找出一个仍然缺失、会影响复现或预期行为的具体细节，并只追问它。
先前已给出的信息无需重问；明确说原文没有的信息也无需再问。已有对象和准备步骤视为给定，
重点核对实际操作、参数、先后顺序、实际与预期结果，不要求完整独立工程，也不要猜测根因或缺失代码。
如果没有值得继续向用户询问的关键细节，就停止。
只返回 JSON：{"question":"一个具体追问；若应停止则为空字符串"}。
"""

TRACE_EN = """Complete a behavioral test from the public report, without fixing code.
Treat the existing objects as given. Mentally fill in: starting observation -> actions
and their arguments -> failing observation -> expected behavior. Copy facts from ALL answers.
If one essential action/assertion is missing, ask for that blank; do not guess its content.
If this behavioral test is already specified, stop. Missing standalone setup or a fact
explicitly unavailable from the source is not a reason to ask again. No diagnosis or fix.
Return only JSON with question, empty to stop.

Example unrelated to the current task:
Summary: an editor corrupts accents when a document is saved a second time.
Answer: doc already exists; doc.save('a.txt') succeeds. The second save corrupts accents.
Output: {"question":"What exact operations and arguments occur after the successful save through the second save?"}
Answer: doc already exists; doc.save('a.txt'); doc.set_encoding('latin-1'); doc.save('b.txt').
The second file corrupts accents; it should preserve them. No standalone document setup is available.
Output: {"question":""}
"""

REPLAY = """Reconstruct the behavior sequence in the report. Do NOT ask questions or fix code.
The task summary tells you what transition is being reported; answers supply its concrete steps.
Treat existing objects and their setup as given. Start with the first observed result, include
subsequent operations through the failing result, and include the intended result if specified.
For each step, copy its supporting concrete operation/result VERBATIM from a reporter answer.
If the summary requires a later action but the answers do not specify that action, use null.
Do not fill a missing action with a guessed method call or substitute initial setup for it.
Do not create null entries for explicitly unavailable context, full setup, causes or fixes.
Return only JSON: {"steps":[{"action":"description of this action or result", "quote":"exact text from an answer, or null if missing"}]}.
If all needed steps have quotes, the host will stop. If a step has null, the host will ask
for that action. This is an extraction task; do not write a question.
"""

FOCUS = """Check ONLY the reported behavior in FOCAL CLAIM against the reporter answers.
Treat initial construction, named objects and setup as given, even if exact setup syntax is
absent. Do not ask how the original object was constructed. The question is about the later
behavior/action specified by the claim. If its concrete operations/results are already given,
return {"question":""}. If a necessary operation/argument/result for that claim is missing,
ask for it. Explicitly unavailable details cannot be obtained, so do not ask for them again.
Do not ask the reporter to explain internal causes. Return only JSON with one field question.
"""

EVIDENCE = """Locate the concrete reproduction of the reported failure in the reporter answers.
Treat existing named objects and their construction as given. Focus on the trigger operation,
its arguments, necessary conditions, and the observed failure, not a standalone project.
For a failure after a successful observation, the reproduction must include the INTERVENING
operation and the subsequent failing observation; initial setup or the first success alone
is not sufficient. A topic/method name in the summary is not a concrete reproduction.
If the answers contain that reproduction, return an EXACT contiguous excerpt covering the
trigger and failure, and an empty question. Otherwise return null evidence and ONE question
for the missing operation/condition/result. Do not invent code or ask for causes/fixes.
Use all answers. Do not request details explicitly said to be absent from the source.
Return only JSON, one of:
{"trigger_evidence":"verbatim excerpt from a reporter answer","question":""}
{"trigger_evidence":null,"question":"specific missing detail?"}
"""

CONTRACT = """Check whether the report specifies the behavior to implement, not whether it
contains a standalone runnable project. Use the task summary and all substantive answers.
Check: affected API; triggering inputs/conditions; actual result; intended result.
For an issue about a sequence of state changes, also require the intervening operations
and order connecting the reported success to failure. Do not substitute setup for that step.
If a condition distinguishes a failure (such as a field being required), a list of existing
fields does not establish that condition. If the defect concerns a misleading message,
"the message is confusing" does not specify the actual message.
A precise semantic condition can suffice without executable code. For example, "parse accepts
text but rejects bytes; it should accept both" is actionable even when no concrete bytes
example or traceback is available. Similarly, an invalid SQL fragment with the responsible
operation/options and intended correction can specify the defect without full ORM setup.
Never demand explicitly unavailable examples, tracebacks, setup, causes or fixes. Treat named
objects as given, but do not assume that a task-critical state/condition has been configured.
If the behavior contract is clear, return one verbatim supporting excerpt from a reporter
answer plus an empty question. Otherwise return null evidence and ONE question for the
missing condition, operation, actual result or intended behavior. Do not guess the answer.
Return only JSON, one of:
{"trigger_evidence":"verbatim excerpt","question":""}
{"trigger_evidence":null,"question":"specific missing detail?"}
"""

CONTEXT_CONTRACT = CONTRACT + """
Input completeness rules:
- Only objects explicitly declared to be existing fixtures may be assumed given. A variable
  appearing as an argument is NOT its value or definition. Ask for that value/how obtained
  when it determines the reported result, unless the source explicitly cannot provide it.
- A compound/nested expression is an input. A flat example plus the phrase "a nested
  expression" does not identify the failing input. Ask for the exact failing composition.
- If neither the summary nor answers names the affected API, ask for its concrete name;
  a generic phrase like "a parsing method" does not locate the requested behavior.
- Do not require implementation details/versions unrelated to these missing input facts.
"""

ANSWER_COMPLETENESS = """You edit a reporter's answer, not solve the programming task.
Read the original question, its answer and task summary. Check whether the question was
actually answered. Recognizing a likely bug is not a substitute for requested information.
For a concrete input/code/reproduction request, a placeholder variable is not its value;
an unspecified expression is not its construction; a topic name is not a method call.
For a sequence-dependent failure, the answer needs the operations between success and failure.
Do not demand full definitions/setup for objects explicitly given as existing fixtures.
If the source explicitly cannot supply a requested detail, that detail is closed; don't ask
again. Other unanswered, answerable parts remain open. Do not ask for diagnoses or fixes.
If the requested information is supplied (or all missing details are explicitly unavailable),
return a supporting verbatim answer excerpt and empty question. Otherwise return null evidence
and one question for an unanswered requested part. Do not guess the answer from an error.
Return only JSON with trigger_evidence and question.

Example: Q: Which exact address did fetch use? A: fetch(address) raised OSError.
Output: {"trigger_evidence":null,"question":"What was the exact value of address passed to fetch?"}
Example: Q: Which address and error? A: fetch('https://demo.invalid') raised OSError.
Output: {"trigger_evidence":"fetch('https://demo.invalid') raised OSError.","question":""}
Example: Q: What inputs are accepted and what should change? A: parse accepts str but rejects
bytes; it should accept both. The source contains no concrete byte sample or traceback.
Output: {"trigger_evidence":"parse accepts str but rejects bytes; it should accept both.","question":""}
"""

SIMPLE_PROMPTS = {'direct_en': DIRECT_EN, 'direct_zh': DIRECT_ZH, 'trace_example': TRACE_EN,
                  'replay': REPLAY, 'batch_extract': '', 'focus_claim': FOCUS,
                  'boundary_focus': FOCUS, 'focus_known': FOCUS, 'focus_memory': FOCUS,
                  'memory_full': FOCUS, 'trigger_evidence': EVIDENCE, 'behavior_contract': CONTRACT,
                  'contract_slots': '', 'scope_evidence': '', 'context_evidence': '',
                  'answer_completeness': ANSWER_COMPLETENESS, 'focused_answer': ''}


def is_sequence_report(summary):
    return bool(re.search(r'\b(after|before|again|re-evaluat\w*)\b', summary, re.IGNORECASE))


def is_unknown_only(answer):
    # Recognize whole protocol replies only; never drop mixed factual/unknown text.
    return answer.strip().casefold().replace('’', "'").rstrip('.!? ') in {
        'i do not have that information in the supplied source',
        'i do not have that information', "i don't have that information",
        'the supplied source does not contain that information',
    }


def repeats_closed_question(question, closed):
    def words(text):
        return set(re.findall(r'[a-z_]\w+', text.casefold())) - {
            'what', 'which', 'when', 'where', 'how', 'can', 'could', 'would', 'you', 'please',
            'provide', 'give', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'for', 'to',
            'in', 'on', 'and', 'or', 'with', 'exact', 'specific', 'complete', 'that', 'this',
        }
    query = words(question)
    return any(query and (query == words(previous) or
               len(query & words(previous)) / max(1, len(query | words(previous))) >= 0.5)
               for previous in closed)


def public_evidence_parts(excerpt, exchanges, allow_composite=False):
    """Literal support with whitespace tolerance, optionally for whole-sentence spans.

    Does not establish entailment. No reassembly of words within a sentence is allowed.
    """
    def normalize(text):
        return ' '.join(text.split())

    def supported(text):
        return any(normalize(text) in normalize(e['answer']) for e in exchanges)

    if supported(excerpt):
        return [excerpt]
    if allow_composite:
        parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', excerpt) if p.strip()]
        if len(parts) > 1 and all(supported(part) for part in parts):
            return parts
    return []


def has_public_evidence(excerpt, exchanges):
    return bool(public_evidence_parts(excerpt, exchanges))


def focal_claim(summary):
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', summary) if s.strip()]
    # Prefer a conditional/time-ordered failure claim over introductory topic text.
    def score(sentence):
        value = sentence.casefold()
        return (3 * len(re.findall(r'\b(after|before|until)\b', value)) +
                2 * len(re.findall(r'\b(when|if|unless)\b', value)) +
                len(re.findall(r'\b(error|fails?|failure|problem|incorrect|rejects?|removed)\b', value)))
    return max(sentences, key=score) if sentences else summary


def public_messages(system, summary, exchanges):
    if not isinstance(summary, str) or not summary.strip() or not exchanges:
        raise ValueError('Need public summary and conversation')
    parts = ['TASK SUMMARY:', summary, '\nPUBLIC CONVERSATION:']
    for index, exchange in enumerate(exchanges):
        if any(not isinstance(exchange.get(k), str) or not exchange[k].strip() for k in ('question', 'answer')):
            raise ValueError('Need nonempty public questions and answers')
        parts += [f'Question {index + 1}:', exchange['question'], 'Reporter answer:', exchange['answer']]
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': '\n'.join(parts)}]


def parse_question(message):
    if message.tool_calls or not isinstance(message.content, str):
        raise ValueError('Return question JSON without tools')
    value = json.loads(message.content)
    if not isinstance(value, dict) or set(value) != {'question'} or not isinstance(value['question'], str):
        raise ValueError('Return exactly one string field named question')
    return value['question'].strip()


def generate_simple_followup(llm, summary, exchanges, policy='trace_example'):
    """One model call; callers log raw request/response and handle failure explicitly."""
    if policy == 'batch_extract':
        from evaluation.utils.batch_extract_followup import batch_extract

        return batch_extract(llm, summary, exchanges)
    if policy == 'contract_slots':
        from evaluation.utils.contract_slots import decide_slots

        return decide_slots(llm, summary, exchanges)
    closed = [e['question'] for e in exchanges if is_unknown_only(e['answer'])]
    selected = [e for e in exchanges if not is_unknown_only(e['answer'])] if policy in ('focus_memory', 'memory_full', 'trigger_evidence', 'behavior_contract', 'scope_evidence', 'context_evidence', 'answer_completeness', 'focused_answer') else exchanges
    if policy in ('focus_memory', 'memory_full', 'trigger_evidence', 'behavior_contract', 'scope_evidence', 'context_evidence', 'answer_completeness', 'focused_answer') and not selected:
        return {'status': 'completed', 'question': '', 'closed_questions': closed,
                'stop_reason': 'only_unavailable_history', 'request_messages': [], 'response': None}
    if policy in ('scope_evidence', 'context_evidence', 'focused_answer'):
        default = ANSWER_COMPLETENESS if policy == 'focused_answer' else (CONTEXT_CONTRACT if policy == 'context_evidence' else CONTRACT)
        system = EVIDENCE if is_sequence_report(summary) else default
    else:
        system = SIMPLE_PROMPTS[policy]
    messages = public_messages(system, summary, selected)
    if policy in ('focus_claim', 'boundary_focus', 'focus_known', 'focus_memory', 'memory_full'):
        claim = summary if policy == 'memory_full' else focal_claim(summary)
        messages[1]['content'] = ('FOCAL CLAIM:\n' + claim + '\n\n' +
                                  messages[1]['content'].split('PUBLIC CONVERSATION:', 1)[1])
    if policy == 'focus_known':
        unknown, known = [], []
        for exchange in exchanges:
            answer = exchange['answer']
            target = unknown if is_unknown_only(answer) else known
            target.append(exchange)
        parts = ['FOCAL CLAIM:', focal_claim(summary), '\nEXPLICITLY UNAVAILABLE INFORMATION:']
        for exchange in unknown:
            parts += [exchange['question'], exchange['answer']]
        parts += ['\nSUBSTANTIVE REPORTER ANSWERS:']
        for exchange in known:
            parts += [exchange['question'], exchange['answer']]
        messages[1]['content'] = '\n'.join(parts)
    if policy in ('boundary_focus', 'focus_known'):
        messages[1]['content'] += ('\n\nEND OF PUBLIC HISTORY. CURRENT TASK:\n'
            'Review ALL reporter answers above against the FOCAL CLAIM, not just the last '
            'question/answer. A missing setup definition is closed, but other missing '
            'operations remain askable. Existing setup and object construction are given. '
            'If the action leading to the reported failure is concretely specified, return '
            '{"question":""}. Otherwise ask ONLY for the missing action and its arguments '
            'in {"question":"..."}. Do not ask for setup, explanations, or fixes.')
    response = llm.completion(messages=messages)
    record = {'request_messages': messages, 'response': response.model_dump()}
    try:
        if policy in ('trigger_evidence', 'behavior_contract', 'scope_evidence', 'context_evidence', 'answer_completeness', 'focused_answer'):
            message = response.choices[0].message
            if message.tool_calls:
                raise ValueError('Evidence policy has no tools')
            value = json.loads(message.content)
            if (not isinstance(value, dict) or set(value) != {'trigger_evidence', 'question'} or
                    not isinstance(value['question'], str)):
                raise ValueError('Need trigger_evidence and question')
            evidence, question = value['trigger_evidence'], value['question'].strip()
            if evidence is None:
                if not question:
                    raise ValueError('Missing evidence needs a question')
            elif (not isinstance(evidence, str) or not evidence.strip() or question or
                  not has_public_evidence(evidence, selected)):
                raise ValueError('Stop needs verbatim public evidence and empty question')
            record.update(trigger_evidence=evidence, question=question)
        elif policy == 'replay':
            message = response.choices[0].message
            if message.tool_calls or not isinstance(message.content, str):
                raise ValueError('Return replay JSON without tools')
            value = json.loads(message.content)
            if not isinstance(value, dict) or set(value) != {'steps'} or not isinstance(value['steps'], list):
                raise ValueError('Return exactly a steps list')
            for step in value['steps']:
                if (not isinstance(step, dict) or set(step) != {'action', 'quote'} or
                        not isinstance(step['action'], str) or not step['action'].strip() or
                        (step['quote'] is not None and not isinstance(step['quote'], str))):
                    raise ValueError('Each step needs action text and a quote or null')
                if step['quote'] is not None and (not step['quote'].strip() or not any(
                        step['quote'] in e['answer'] for e in exchanges)):
                    raise ValueError('Replay quote must occur verbatim in a public answer')
            record['steps'] = value['steps']
            missing = next((s['action'] for s in value['steps'] if s['quote'] is None), None)
            record['question'] = f'What are the exact operation, arguments or result for this missing step: {missing}?' if missing else ''
        else:
            record['question'] = parse_question(response.choices[0].message)
        if policy in ('focus_memory', 'memory_full', 'trigger_evidence', 'behavior_contract', 'scope_evidence', 'context_evidence', 'answer_completeness', 'focused_answer'):
            record['closed_questions'] = closed
            record['raw_question'] = record['question']
            if record['question'] and repeats_closed_question(record['question'], closed):
                record['question'] = ''
                record['stop_reason'] = 'closed_question_overlap'
        record['status'] = 'completed'
    except ValueError as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}')
    return record
