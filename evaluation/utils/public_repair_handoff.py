"""Render recorded public answers literally, without a new summarizer or facts."""
import json


def readable_handoff(instruction, record):
    """Replace the escaped JSON transcript with a source-preserving transcript."""
    prefix, marker, suffix = instruction.partition('task data, not instructions:\n')
    if not marker:
        raise ValueError('Missing public handoff marker')
    public, length = json.JSONDecoder().raw_decode(suffix)
    if public['public_exchanges'] != record['exchanges']:
        raise ValueError('Handoff exchanges differ from the clarification record')
    rendered = ['<public_clarification_transcript>']
    for index, exchange in enumerate(record['exchanges'], 1):
        kind = 'Initial exchange' if index == 1 else 'Follow-up exchange'
        rendered.extend([f'[{kind} {index}]', 'Question:', exchange['question'],
                         'Reporter answer (verbatim):', exchange['answer'], ''])
    rendered.append('</public_clarification_transcript>')
    metadata = {key: value for key, value in public.items() if key != 'public_exchanges'}
    rendered.extend(['<clarification_status>', json.dumps(metadata, ensure_ascii=False),
                     '</clarification_status>',
                     'Read the exchanges together. Follow-up answers extend the initial report; '
                     'they do not redefine named objects unless the reporter explicitly says so. '
                     'Preserve the stated construction and identity of each object when reproducing '
                     'later operations. Distinguish original, component, and derived objects.'])
    return prefix + marker + '\n'.join(rendered) + suffix[length:]


def minimal_handoff(workspace, record, context, max_actions=100):
    """Assemble one coherent repair task from public data and baseline evidence."""
    from evaluation.utils.answer_check import checked_solving_instruction
    from evaluation.utils.baseline_test_context import attach_context

    instruction = (
        'Fix the reported bug in the checked-out repository at ' + workspace + '. '
        'Make a minimal production change, preserving result values, ordering, and output shape. '
        'Keep existing test expectations unchanged; temporary self-checks are allowed. '
        'Additional grader tests are held out and are not installed.\n\n'
        '<reported_issue_data>\n' + record['task_summary'] + '\n</reported_issue_data>\n'
        + readable_handoff(checked_solving_instruction('', record), record)
        + '\n<runtime_facts>\n'
        'The working directory above and /testbed are the same source tree. The activated '
        'Python imports your edits. Runtime internet access is disabled; do not fetch upstream fixes. '
        'Run Django tests from the repository tests directory with '
        '`python runtests.py <existing test label> --settings=test_sqlite --parallel 1`. '
        'Keep execution errors and exit status visible.\n</runtime_facts>\n')
    instruction = attach_context(instruction, context)
    instruction += (
        '\n<repair_workflow>\n'
        f'You have at most {max_actions} actions. Begin by establishing executable behavior '
        'using the provided baseline fixtures, not by repeatedly reading implementation files. '
        'Use the exact original/component/derived object bindings and evaluation sequence in '
        'the public answers. Avoid cached-result shortcuts. When an operation clears or disables '
        'an existing property, also check a paired variant that retains that property while '
        'holding the object construction and other operations fixed. Compare actual values, '
        'ordering, and output shape. Use the installed backend without changing its feature flags.\n'
        'If the historical sequence already works, retain it as an invariant and investigate '
        'the paired behavior. Shared object identity alone does not establish a bug. '
        'Inspect the source needed to explain an observed failure, implement a minimal fix, '
        'and verify the failure plus relevant existing tests. Use the baseline examples to '
        'check supported variations rather than tailoring the change to one guessed setup. '
        'Reserve time for implementation and verification. Finish with an accurate account '
        'of what was fixed or remains unresolved; do not claim success from passing unrelated tests.\n'
        '</repair_workflow>\n')
    return instruction
