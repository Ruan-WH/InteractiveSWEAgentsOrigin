"""Offline checks for Qwen user-proxy routing and question logging."""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.setenv('API_KEY', 'offline-key')
    monkeypatch.setenv('INTERACTION_USER_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('INTERACTION_USER_MODEL', 'qwen3-coder-flash')
    return importlib.import_module('evaluation.benchmarks.swe_bench.interact_run_infer')


def test_proxy_uses_qwen_and_stops_after_three_answers(module, monkeypatch, tmp_path):
    create = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='The missing column is time.'))],
        model='qwen3-coder-flash', usage=None,
    ))
    monkeypatch.setattr(module.client.chat.completions, 'create', create)
    path = tmp_path / 'dialogues' / 'instance.json'
    proxy = module.FakeUser('full issue', 'developer hints', ['core.py'], log_path=path)
    for number in range(3):
        assert proxy.generate_reply(SimpleNamespace(content=f'Question {number}')) == 'The missing column is time.'
    assert 'Do NOT ask' in proxy.generate_reply(SimpleNamespace(content='Question 4'))
    assert create.call_count == 3
    assert create.call_args.kwargs['model'] == 'qwen3-coder-flash'
    assert [entry['turn'] for entry in json.loads(path.read_text())] == [1, 2, 3]
    assert 'full issue' in proxy.system_message
    assert 'developer hints' in proxy.system_message
    assert 'core.py' in proxy.system_message


def test_empty_proxy_answer_is_not_counted(module, monkeypatch):
    create = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=''))],
    ))
    monkeypatch.setattr(module.client.chat.completions, 'create', create)
    proxy = module.FakeUser('issue', '', '')
    with pytest.raises(RuntimeError, match='empty answer'):
        proxy.generate_reply(SimpleNamespace(content='Question'))
    assert proxy.turns == 0
    assert proxy.interactions == []


def test_check_only_never_starts_evaluator(monkeypatch, capsys):
    scripts = Path(__file__).resolve().parents[2] / 'evaluation/benchmarks/swe_bench/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    runner = importlib.import_module('run_interact_qwen')
    monkeypatch.setattr(sys, 'argv', ['run_interact_qwen.py', '--samples', '5', '--check-only'])
    check = Mock()
    monkeypatch.setattr(runner, 'check_images', check)
    evaluate = Mock()
    monkeypatch.setattr(runner.runpy, 'run_module', evaluate)
    runner.main()
    assert len(check.call_args.args[0]) == 5
    evaluate.assert_not_called()
    assert 'No containers' in capsys.readouterr().out


def test_finalization_failure_keeps_history_and_questions(module, monkeypatch, tmp_path):
    state = SimpleNamespace(last_error='', history=[], metrics=None)
    runtime = SimpleNamespace(container=SimpleNamespace(id='offline-container'), connect=Mock())
    monkeypatch.setattr(module, 'get_config', Mock(return_value=SimpleNamespace()))
    monkeypatch.setattr(module, 'create_runtime', Mock(return_value=runtime))
    monkeypatch.setattr(module, 'call_async_from_sync', Mock())
    monkeypatch.setattr(module, 'initialize_runtime', Mock())
    monkeypatch.setattr(module, 'guard_busy_shell', Mock())
    monkeypatch.setattr(module, 'get_instruction', Mock(return_value='summarized issue'))
    monkeypatch.setattr(module, 'run_controller', Mock(return_value='offline-coroutine'))
    monkeypatch.setattr(module, 'run_controller_bounded_cleanup', Mock(return_value=state))
    monkeypatch.setattr(module, 'extract_local_patch', Mock(side_effect=TimeoutError('offline timeout')))
    monkeypatch.setattr(module, 'close_runtime_bounded', Mock(return_value=None))
    import pandas as pd
    instance = pd.Series({'instance_id': 'offline-instance', 'original_issue': 'full issue',
                          'hints_text': '', 'files': ''})
    from openhands.core.config import LLMConfig
    metadata = module.EvalMetadata(
        agent_class='CodeActAgent', llm_config=LLMConfig(), max_iterations=70,
        eval_output_dir=str(tmp_path), start_time='offline', git_commit='offline',
    )
    result = module.process_instance(instance, metadata, reset_logger=False)
    assert 'Patch extraction failed' in result.error
    assert result.test_result['git_patch'] is None
    assert result.instruction == 'summarized issue'
    assert result.test_result['interactions'] == []
    module.close_runtime_bounded.assert_called_once_with(runtime)
