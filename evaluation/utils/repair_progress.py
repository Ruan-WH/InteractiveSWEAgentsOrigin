"""Opt-in repair-stage reminders derived solely from completed tool history."""

import json
import shlex
from types import MethodType

from openhands.core.message import Message, TextContent


def is_source_read(command):
    """Conservatively recognize text-reading shell commands, never execution."""
    if any(token in command for token in ('>', '<', '$', '`', '\n')):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
        lexer.whitespace_split = True
        chunks = [[]]
        for token in lexer:
            if token in ('&&', '|', ';'):
                chunks.append([])
            else:
                chunks[-1].append(token)
    except ValueError:
        return False
    allowed = {'cd', 'pwd', 'ls', 'find', 'rg', 'grep', 'sed', 'head', 'tail', 'cat', 'wc'}
    for chunk in chunks:
        if not chunk:
            return False
        program = chunk[0].rsplit('/', 1)[-1]
        if program == 'git':
            if len(chunk) < 2 or chunk[1] not in ('show', 'log', 'diff', 'status', 'ls-files'):
                return False
        elif program == 'sed':
            if '-i' in chunk or any(c.startswith('-i') for c in chunk[1:]):
                return False
        elif program not in allowed:
            return False
    return True


def read_streak(messages):
    """Count consecutive read actions, resetting on any other completed tool."""
    streak = total = 0
    for message in messages:
        if message.role != 'assistant':
            continue
        for call in message.tool_calls or []:
            total += 1
            try:
                args = json.loads(call.function.arguments)
            except (TypeError, ValueError):
                streak = 0
                continue
            reading = (
                call.function.name == 'str_replace_editor' and args.get('command') == 'view'
            ) or (
                call.function.name == 'execute_bash' and is_source_read(args.get('command', ''))
            )
            streak = streak + 1 if reading else 0
    return total, streak


def install_progress_reminders(agent, threshold=8):
    """Append an ephemeral process reminder; leave task/Q&A and tools intact.

    No additional model calls, commands, generated tests, or reference data.
    Existing pending tool batches finish before the next model request.
    Every actual intervention is recorded for attribution and auditing.
    """
    if threshold < 1:
        raise ValueError('threshold must be positive')
    audit = {'policy': 'read-streak-v1', 'threshold': threshold, 'interventions': []}
    original = agent._get_messages
    last_total = 0

    def get_messages(self, state):
        nonlocal last_total
        messages = original(state)
        total, streak = read_streak(messages)
        if streak >= threshold and total - last_total >= threshold:
            reminder = (
                '<repair_progress_checkpoint>\n'
                f'The last {streak} completed tool actions only read text. '
                'You have not obtained new execution evidence in this interval. '
                'Pause source browsing. Make the next action run a focused check using the public '
                'issue and Q/A, or an existing relevant repository test. '
                'If the reported sequence already passes on this baseline, record that fact and '
                'test a hypothesis that distinguishes a remaining allowed operation combination. '
                'Use actual execution output to choose the next source edit; do not fabricate '
                'a failure, alter backend capabilities, discard ordering, or fetch upstream fixes. '
                'Keep budget for a production change and verification. '
                'This is a process reminder, not additional issue evidence.\n'
                '</repair_progress_checkpoint>'
            )
            messages.append(Message(role='user', content=[TextContent(text=reminder)]))
            audit['interventions'].append({'completed_tools': total, 'consecutive_reads': streak, 'text': reminder})
            last_total = total
        return messages

    agent._get_messages = MethodType(get_messages, agent)
    return audit
