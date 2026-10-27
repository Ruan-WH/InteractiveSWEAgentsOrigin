import asyncio
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from evaluation.utils.interaction_runtime import (
    close_runtime_bounded,
    extract_local_patch,
    guard_busy_shell,
    run_controller_bounded_cleanup,
)
from openhands.events.action import CmdRunAction
from openhands.events.observation import CmdOutputObservation, ErrorObservation
from openhands.events.observation.commands import CmdOutputMetadata


def observation(exit_code):
    return CmdOutputObservation(content='', command='', metadata=CmdOutputMetadata(exit_code=exit_code))


def test_busy_shell_blocks_new_command_but_accepts_poll():
    runtime = SimpleNamespace(run=Mock(side_effect=[observation(-1), observation(0), observation(0)]))
    original = runtime.run
    guard_busy_shell(runtime)
    runtime.run(CmdRunAction(command='python long.py'))
    rejected = runtime.run(CmdRunAction(command='echo second'))
    assert isinstance(rejected, ErrorObservation)
    assert original.call_count == 1
    runtime.run(CmdRunAction(command=''))
    runtime.run(CmdRunAction(command='echo second'))
    assert original.call_count == 3


def test_busy_shell_accepts_interrupt():
    runtime = SimpleNamespace(run=Mock(side_effect=[observation(-1), observation(0)]))
    original = runtime.run
    guard_busy_shell(runtime)
    runtime.run(CmdRunAction(command='python long.py'))
    runtime.run(CmdRunAction(command='C-c'))
    assert original.call_count == 2


def test_patch_uses_fresh_docker_exec_and_preserves_newline(monkeypatch):
    execute = Mock(return_value=SimpleNamespace(stdout='patch\n'))
    monkeypatch.setattr('evaluation.utils.interaction_runtime.subprocess.run', execute)
    runtime = SimpleNamespace(container=SimpleNamespace(id='only-this-container'), run_action=Mock())
    instance = pd.Series({'repo': 'astropy/astropy', 'version': '5', 'base_commit': 'abc123'})
    assert extract_local_patch(runtime, instance)['git_patch'] == 'patch\n'
    runtime.run_action.assert_not_called()
    argv = execute.call_args.args[0]
    assert argv[:5] == ['docker', 'exec', '--workdir', '/workspace/astropy__astropy__5', 'only-this-container']
    assert execute.call_args.kwargs['timeout'] == 60
    assert execute.call_args.kwargs['check'] is True


def test_patch_timeout_is_explicit(monkeypatch):
    monkeypatch.setattr('evaluation.utils.interaction_runtime.subprocess.run',
                        Mock(side_effect=subprocess.TimeoutExpired(['docker'], 60)))
    with pytest.raises(subprocess.TimeoutExpired):
        extract_local_patch(SimpleNamespace(container=SimpleNamespace(id='one')),
                            pd.Series({'repo': 'a/b', 'version': '1', 'base_commit': 'abc'}))


def test_controller_returns_result_despite_cancellation_resistant_task():
    async def background():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            await asyncio.sleep(100)

    async def controller():
        asyncio.create_task(background())
        await asyncio.sleep(0)
        return 'finished'

    assert run_controller_bounded_cleanup(controller(), cleanup_timeout=0.01) == 'finished'


def test_runtime_close_has_bound_and_targets_one_container(monkeypatch):
    gate = threading.Event()
    remove = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr('evaluation.utils.interaction_runtime.subprocess.run', remove)
    runtime = SimpleNamespace(close=gate.wait, container=SimpleNamespace(id='only-this-one'))
    try:
        assert 'time limit' in close_runtime_bounded(runtime, timeout=0.01)
        assert remove.call_args.args[0] == ['docker', 'rm', '-f', 'only-this-one']
    finally:
        gate.set()


def test_normal_runtime_cleanup_has_no_error():
    runtime = SimpleNamespace(close=Mock())
    assert close_runtime_bounded(runtime) is None
    runtime.close.assert_called_once()
