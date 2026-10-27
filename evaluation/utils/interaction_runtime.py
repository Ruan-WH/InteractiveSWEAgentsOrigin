"""Bounded interaction cleanup and local patch extraction, without model calls."""

import asyncio
import queue
import shlex
import subprocess
import threading

from openhands.core.logger import openhands_logger as logger
from openhands.events.observation import CmdOutputObservation, ErrorObservation


def guard_busy_shell(runtime):
    """Do not feed a second shell command into a still-running first command."""
    original = runtime.run
    busy = False

    def guarded(action):
        nonlocal busy
        if busy and action.command.strip() not in ('', 'C-c', 'C-d'):
            return ErrorObservation(
                content='The previous shell command is still running or waiting for input. '
                'Poll it with execute_bash command="", or interrupt it with command="C-c", '
                'before starting another command. No new command was executed.'
            )
        observation = original(action)
        if isinstance(observation, CmdOutputObservation):
            busy = observation.exit_code == -1
        return observation

    runtime.run = guarded


def run_controller_bounded_cleanup(coroutine, cleanup_timeout=5):
    """Keep background-task cancellation from indefinitely delaying the result."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            _, left = loop.run_until_complete(asyncio.wait(pending, timeout=cleanup_timeout))
            if left:
                logger.warning('Interaction cleanup timed out with %s pending tasks', len(left))
                for task in left:
                    task._log_destroy_pending = False
        # Python 3.12 supports a bounded default-executor shutdown. Use a task
        # timeout as well, so cleanup itself cannot wait indefinitely.
        shutdown = loop.create_task(loop.shutdown_default_executor(timeout=cleanup_timeout))
        _, left = loop.run_until_complete(asyncio.wait([shutdown], timeout=cleanup_timeout + 1))
        for task in left:
            task.cancel()
            task._log_destroy_pending = False
        loop.close()
        asyncio.set_event_loop(None)


def extract_local_patch(runtime, instance, timeout=60):
    """Use a fresh noninteractive shell instead of the agent's possibly busy PTY."""
    container = getattr(runtime, 'container', None)
    if container is None:
        raise ValueError('Independent patch extraction requires a local Docker container')
    repo = '/workspace/' + f'{instance.repo}__{instance.version}'.replace('/', '__')
    repo = getattr(runtime, 'swebench_workspace_dir', repo)
    # Quoting also protects repository names/base SHAs from shell interpretation.
    script = (
        'git add -A && git -c core.pager=cat diff --binary '
        + shlex.quote(str(instance['base_commit']))
    )
    result = subprocess.run(
        ['docker', 'exec', '--workdir', repo, container.id, 'sh', '-c', script],
        capture_output=True, text=True, timeout=timeout, check=True,
    )
    return {'git_patch': result.stdout}


def close_runtime_bounded(runtime, timeout=15):
    """Bound cleanup; only this runtime's container may be removed."""
    outcomes = queue.Queue()

    def close():
        try:
            runtime.close()
            outcomes.put(None)
        except Exception as error:
            outcomes.put(error)

    worker = threading.Thread(target=close, daemon=True, name='interaction-runtime-cleanup')
    worker.start()
    worker.join(timeout)
    if not worker.is_alive():
        error = outcomes.get_nowait()
        return str(error) if error else None
    container = getattr(runtime, 'container', None)
    if container is not None:
        try:
            subprocess.run(['docker', 'rm', '-f', container.id], capture_output=True,
                           timeout=timeout, check=True)
        except (subprocess.SubprocessError, OSError) as error:
            logger.warning('Target-container cleanup failed: %s', type(error).__name__)
    return 'Runtime cleanup exceeded its time limit; target-container removal attempted.'
