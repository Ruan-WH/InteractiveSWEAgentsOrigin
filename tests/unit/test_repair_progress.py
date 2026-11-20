"""Progress reminders must use observed behavior, without changing task evidence."""
import json
from types import SimpleNamespace

from litellm import ChatCompletionMessageToolCall
import pytest

from evaluation.utils.repair_progress import install_progress_reminders, is_source_read
from openhands.core.message import Message, TextContent


@pytest.mark.parametrize('command,expected', [
    ('cd /testbed && sed -n 30,80p file.py', True),
    ('grep -n order file.py | head -20', True),
    ('python runtests.py queries --parallel 1', False),
    ('cat > /tmp/repro.py <<EOF\nprint(1)\nEOF', False),
    ('sed -i s/old/new/ source.py', False),
    ('git reset --hard HEAD', False),
    ('git diff HEAD', True),
    ('python -c "print(open(\'source.py\').read())"', False),
])
def test_only_conservative_text_reads_are_counted(command, expected):
    assert is_source_read(command) is expected


def action(number, command):
    call = ChatCompletionMessageToolCall(id=f'call-{number}', type='function', function={
        'name': 'execute_bash', 'arguments': json.dumps({'command': command})})
    return Message(role='assistant', content=[TextContent(text='')], tool_calls=[call])


def test_interventions_preserve_task_and_reset_after_execution():
    task = Message(role='user', content=[TextContent(text='issue and literal public Q/A')])
    history = [task, action(1, 'grep -n order compiler.py'), action(2, 'sed -n 1,20p compiler.py')]
    agent = SimpleNamespace(_get_messages=lambda state: list(history))
    audit = install_progress_reminders(agent, threshold=2)
    first = agent._get_messages(None)
    assert first[0] is task and len(history) == 3
    assert 'process reminder, not additional issue evidence' in first[-1].content[0].text
    assert len(audit['interventions']) == 1
    assert len(agent._get_messages(None)) == 3  # no repeated nudge at same history
    history.append(action(3, 'python /tmp/repro.py'))
    assert len(agent._get_messages(None)) == 4
    history.append(action(4, 'grep -n order compiler.py'))
    assert len(agent._get_messages(None)) == 5
    history.append(action(5, 'sed -n 20,40p compiler.py'))
    assert len(agent._get_messages(None)) == 7
    assert len(audit['interventions']) == 2
