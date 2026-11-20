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
    assert len(proxy.chat_history) == 1


def test_grounded_proxy_preserves_sources_and_records_exact_requests(module, monkeypatch, tmp_path):
    monkeypatch.setenv('INTERACTION_PROXY_POLICY', 'grounded-v1')
    requests = []

    def reply(**kwargs):
        requests.append([dict(message) for message in kwargs['messages']])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                'evidence': [{'source': 'hints', 'quote': 'Final: I agree with just changing it.'}],
                'message_kind': 'question', 'final_requirement': 'Change it now.', 'answer': 'Use the final behavior.',
            })))],
            model='offline', usage=None,
        )

    monkeypatch.setattr(module.client.chat.completions, 'create', reply)
    path = tmp_path / 'answers.json'
    issue = 'Should a FutureWarning precede the change?'
    hints = 'Earlier: warn first. Final: I agree with just changing it.'
    proxy = module.FakeUser(issue, hints, ['table.py'], log_path=path)
    for question in ('1. Which warning?\n2. What behavior?', 'Does this need a warning?'):
        proxy.generate_reply(SimpleNamespace(content=question))
    records = json.loads(path.read_text())
    assert [record['request_messages'] for record in records] == requests[::2]
    assert all(record['proxy_policy'] == 'grounded-v1' for record in records)
    assert json.loads(requests[2][-3]['content'])['answer'] == 'Use the final behavior.'
    # Logging the second turn must not mutate the saved first request.
    assert len(records[0]['request_messages']) == 3
    assert len(records[1]['request_messages']) == 5
    sources, _ = json.JSONDecoder().raw_decode(proxy.system_message.split('Source record:\n', 1)[1])
    assert sources == {'issue': issue, 'hints': hints, 'files': "['table.py']"}


def test_bad_proxy_json_is_repaired_without_restarting_sample(module, monkeypatch, tmp_path):
    monkeypatch.setenv('INTERACTION_PROXY_POLICY', 'grounded-v1')
    valid = json.dumps({'message_kind': 'question', 'evidence': [],
                        'final_requirement': 'unknown', 'answer': "I don't have that information"})
    def response(text):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                               model='offline', usage=None)
    create = Mock(side_effect=[response('{"answer": "incomplete"}'), response(valid), response(valid)])
    monkeypatch.setattr(module.client.chat.completions, 'create', create)
    proxy = module.FakeUser('issue', '', '', log_path=tmp_path / 'answers.json')
    assert proxy.generate_reply(SimpleNamespace(content='Which version?')) == "I don't have that information"
    assert proxy.turns == 1
    assert proxy.interactions[0]['proxy_api_attempts'] == 2
    assert len(list(tmp_path.glob('*.attempt-*.json'))) == 2
    assert len(proxy.chat_history) == 3
    assert 'incomplete' not in str(proxy.chat_history)


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


def test_explicit_instances_preserve_order_and_exclude_default_samples(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / 'evaluation/benchmarks/swe_bench/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    runner = importlib.import_module('run_interact_qwen')
    ids = ['astropy__astropy-13398', 'django__django-10880']
    monkeypatch.setattr(sys, 'argv', ['run_interact_qwen.py', '--instance-ids', *ids, '--check-only'])
    check = Mock()
    monkeypatch.setattr(runner, 'check_images', check)
    evaluate = Mock()
    monkeypatch.setattr(runner.runpy, 'run_module', evaluate)
    runner.main()
    check.assert_called_once_with(ids)
    evaluate.assert_not_called()


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


@pytest.mark.parametrize('policy', ['legacy-v5', 'simple-v1'])
def test_answer_check_finishes_before_runtime_and_closes_repair_proxy(module, monkeypatch, tmp_path, policy):
    from litellm import ModelResponse
    import pandas as pd
    from openhands.core.config import LLMConfig

    monkeypatch.setenv('INTERACTION_PROMPT_VARIANT', 'answer-check-v1')
    monkeypatch.setenv('INTERACTION_PROXY_POLICY', 'original')
    monkeypatch.setenv('ANSWER_CHECK_MAX_FOLLOWUPS', '2')
    monkeypatch.setenv('ANSWER_CHECK_POLICY', policy)
    monkeypatch.setattr(module, 'USE_HINT_TEXT', False)
    config = SimpleNamespace(get_llm_config=Mock(return_value=LLMConfig()))
    monkeypatch.setattr(module, 'get_config', Mock(return_value=config))
    state = SimpleNamespace(last_error='', history=[], metrics=None)
    runtime = SimpleNamespace(container=SimpleNamespace(id='offline'), connect=Mock())
    calls = []

    def model_response(text):
        return ModelResponse(choices=[{'index': 0, 'message': {'role': 'assistant', 'content': text}}])

    ask = {'decision': 'ask', 'missing_information': 'Intervening operation',
           'behavioral_impact': 'Cannot identify the failure trigger',
           'question': 'Which operation happens before re-evaluation?', 'reason': 'Initial display succeeds'}
    stop = {'decision': 'stop', 'missing_information': '', 'behavioral_impact': '',
            'question': '', 'reason': 'The concrete sequence is now given'}
    phase_llm = SimpleNamespace(completion=Mock(side_effect=[
        model_response('1. What is the reproduction sequence?'),
        model_response(json.dumps({'reported_failure_transition': [], 'steps': [], 'unavailable_information': [], 'missing_transition_details': []})),
        model_response(json.dumps(ask)),
        model_response(json.dumps({'reported_failure_transition': [], 'steps': [], 'unavailable_information': [], 'missing_transition_details': []})),
        model_response(json.dumps(stop)),
    ]))
    monkeypatch.setattr('openhands.llm.llm.LLM', Mock(return_value=phase_llm))
    checker_create = Mock(side_effect=[
        model_response(json.dumps({'trigger_evidence': None, 'question': ask['question']})),
        model_response(json.dumps({'trigger_evidence': 'Run the derived values_list call, then display qs again.', 'question': ''})),
    ])
    if policy == 'simple-v1':
        monkeypatch.setattr(module, 'get_llm_config_arg', Mock(return_value=LLMConfig(
            model='openai/deepseek-flash', api_key='checker-key', base_url='https://example.invalid/v1')))
        monkeypatch.setattr(module.openai, 'OpenAI', Mock(return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=checker_create)))))

    def proxy_reply(**kwargs):
        calls.append('proxy')
        answer = 'First display succeeds.' if len(calls) == 1 else 'Run the derived values_list call, then display qs again.'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))],
                               model='offline', usage=None)

    monkeypatch.setattr(module.client.chat.completions, 'create', Mock(side_effect=proxy_reply))

    def create_runtime(config):
        assert calls == ['proxy', 'proxy']
        assert phase_llm.completion.call_count == (1 if policy == 'simple-v1' else 5)
        if policy == 'simple-v1':
            assert checker_create.call_count == 2
            assert checker_create.call_args.kwargs['model'] == 'deepseek-flash'
            assert checker_create.call_args.kwargs['response_format'] == {'type': 'json_object'}
            assert 'PRIVATE_' not in str(checker_create.call_args_list)
        assert module.fake_user.clarification_closed
        calls.append('runtime')
        return runtime

    monkeypatch.setattr(module, 'create_runtime', Mock(side_effect=create_runtime))
    monkeypatch.setattr(module, 'call_async_from_sync', Mock())
    monkeypatch.setattr(module, 'initialize_runtime', Mock())
    monkeypatch.setattr(module, 'guard_busy_shell', Mock())
    controller = Mock(return_value='offline-coroutine')
    monkeypatch.setattr(module, 'run_controller', controller)
    monkeypatch.setattr(module, 'run_controller_bounded_cleanup', Mock(return_value=state))
    monkeypatch.setattr(module, 'extract_local_patch', Mock(return_value={'git_patch': 'offline-patch'}))
    monkeypatch.setattr(module, 'close_runtime_bounded', Mock(return_value=None))
    instance = pd.Series({'instance_id': 'offline-check', 'repo': 'org/repo', 'version': '1',
                          'problem_statement': 'PUBLIC_SUMMARY', 'original_issue': 'PRIVATE_ISSUE',
                          'hints_text': 'PRIVATE_HINTS', 'files': 'private.py'})
    metadata = module.EvalMetadata(agent_class='CodeActAgent', llm_config=LLMConfig(),
                                   max_iterations=100, eval_output_dir=str(tmp_path),
                                   start_time='offline', git_commit='offline')
    result = module.process_instance(instance, metadata, reset_logger=False)
    assert result.test_result['answer_check']['followups_used'] == 1
    assert result.num_turns == 2
    handoff = controller.call_args.kwargs['initial_user_action'].content
    assert 'First display succeeds.' in handoff and 'derived values_list' in handoff
    assert 'PRIVATE_' not in handoff
    assert 'three times' not in handoff and 'questions if you run into a problem' not in handoff
    assert 'PRIVATE_' not in str(phase_llm.completion.call_args_list)
    assert 'Do NOT ask' in module.fake_user.generate_reply(SimpleNamespace(content='Another question?'))
    assert module.client.chat.completions.create.call_count == 2


def test_answer_check_check_only_exposes_flag_without_running_evaluation(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / 'evaluation/benchmarks/swe_bench/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    runner = importlib.import_module('run_interact_qwen')
    monkeypatch.setattr(sys, 'argv', ['run_interact_qwen.py', '--samples', '1',
                                    '--prompt-variant', 'answer-check-v1',
                                    '--max-followups', '1', '--check-only'])
    monkeypatch.setattr(runner, 'check_images', Mock())
    evaluate = Mock()
    monkeypatch.setattr(runner.runpy, 'run_module', evaluate)
    runner.main()
    import os
    assert os.environ['INTERACTION_PROMPT_VARIANT'] == 'answer-check-v1'
    assert os.environ['ANSWER_CHECK_MAX_FOLLOWUPS'] == '1'
    evaluate.assert_not_called()


@pytest.mark.parametrize('condition', ['ideal', 'partial'])
@pytest.mark.parametrize('clean_history', [False, True])
def test_initial_only_hands_off_fixed_answer_without_checker_or_proxy_api(module, monkeypatch, tmp_path, condition, clean_history):
    import pandas as pd
    from openhands.core.config import LLMConfig
    from evaluation.analysis.proxy_response_10554_v2.run_clarification import make_fixture

    fixture = make_fixture()
    fixture_path = tmp_path / 'fixture.json'
    fixture_path.write_text(json.dumps(fixture))
    monkeypatch.setenv('INTERACTION_PROMPT_VARIANT', 'answer-check-v1')
    monkeypatch.setenv('INTERACTION_RESPONSE_FIXTURE', str(fixture_path))
    monkeypatch.setenv('INTERACTION_RESPONSE_CONDITION', condition)
    monkeypatch.setenv('CLARIFICATION_MODE', 'initial-only')
    monkeypatch.setenv('ANSWER_CHECK_POLICY', 'simple-v1')
    monkeypatch.setenv('CLEAN_REFERENCE_HISTORY', '1' if clean_history else '0')
    clean = Mock(return_value={'verified': True})
    monkeypatch.setattr('evaluation.utils.reference_history.clean_reference_history', clean)
    config = SimpleNamespace(get_llm_config=Mock(return_value=LLMConfig()))
    monkeypatch.setattr(module, 'get_config', Mock(return_value=config))
    monkeypatch.setattr(module, 'get_instruction', Mock(return_value='PUBLIC_SUMMARY'))
    phase = SimpleNamespace(completion=Mock(side_effect=AssertionError('No model calls in initial-only')))
    monkeypatch.setattr('openhands.llm.llm.LLM', Mock(return_value=phase))
    checker_config = Mock(side_effect=AssertionError('No checker in initial-only'))
    monkeypatch.setattr(module, 'get_llm_config_arg', checker_config)
    proxy_api = Mock(side_effect=AssertionError('No proxy API in initial-only'))
    monkeypatch.setattr(module.client.chat.completions, 'create', proxy_api)
    runtime = SimpleNamespace(container=SimpleNamespace(id='offline'), connect=Mock())
    monkeypatch.setattr(module, 'create_runtime', Mock(return_value=runtime))
    monkeypatch.setattr(module, 'call_async_from_sync', Mock())
    monkeypatch.setattr(module, 'initialize_runtime', Mock())
    monkeypatch.setattr(module, 'guard_busy_shell', Mock())
    def start_controller(**kwargs):
        assert clean.call_count == int(clean_history)
        return 'offline-coroutine'

    controller = Mock(side_effect=start_controller)
    monkeypatch.setattr(module, 'run_controller', controller)
    state = SimpleNamespace(last_error='', history=[], metrics=None)
    monkeypatch.setattr(module, 'run_controller_bounded_cleanup', Mock(return_value=state))
    monkeypatch.setattr(module, 'extract_local_patch', Mock(return_value={'git_patch': 'offline-patch'}))
    monkeypatch.setattr(module, 'close_runtime_bounded', Mock(return_value=None))
    instance = pd.Series({'instance_id': fixture['instance_id'], 'base_commit': fixture['base_commit'],
        'problem_statement': fixture['public_task']['summary'], 'original_issue': 'PRIVATE_ISSUE',
        'repo': 'django/django', 'version': '2.1', 'hints_text': 'PRIVATE_HINTS', 'files': ''})
    metadata = module.EvalMetadata(agent_class='CodeActAgent', llm_config=LLMConfig(),
        max_iterations=100, eval_output_dir=str(tmp_path), start_time='offline', git_commit='offline')
    result = module.process_instance(instance, metadata, reset_logger=False)
    record = result.test_result['answer_check']
    assert record['status'] == 'not_run' and record['followups_used'] == 0 and record['checks'] == []
    assert len(record['exchanges']) == 1
    assert result.num_turns == 1
    handoff = controller.call_args.kwargs['initial_user_action'].content
    assert fixture['initial_responses'][condition]['answer'] in json.loads(
        handoff.split('task data, not instructions:\n')[1].split('\nDo not assume')[0])['public_exchanges'][0]['answer']
    assert 'PRIVATE_' not in handoff
    assert module.fake_user.clarification_closed
    assert result.test_result['reference_history'] == ({'verified': True} if clean_history else None)
    phase.completion.assert_not_called()
    checker_config.assert_not_called()
    proxy_api.assert_not_called()


@pytest.mark.parametrize('clean_history', [False, True])
def test_runtime_setup_copy_excludes_reference_answers_when_cleanup_enabled(module, monkeypatch, clean_history):
    import pandas as pd

    monkeypatch.setattr(module, 'USE_INSTANCE_IMAGE', True)
    monkeypatch.setenv('CLEAN_REFERENCE_HISTORY', '1' if clean_history else '0')
    copied = []

    def copy_to(source, destination):
        if Path(source).name == 'swe-bench-instance.json':
            copied.append(json.loads(Path(source).read_text()))

    runtime = SimpleNamespace(copy_to=Mock(side_effect=copy_to),
        run_action=Mock(return_value=SimpleNamespace(exit_code=0, content='offline')))
    instance = pd.Series(dict(repo='org/repo', instance_id='case', version='1', base_commit='base',
                              patch='PRIVATE_FIX', test_patch='PRIVATE_TEST', original_issue='PRIVATE_REPORT', hints_text='PRIVATE_HINT'))
    module.initialize_runtime(runtime, instance)
    assert len(copied) == 1 and len(copied[0]) == 1
    row = copied[0][0]
    if clean_history:
        assert set(row) == {'repo', 'instance_id', 'version', 'base_commit'}
        assert 'PRIVATE_' not in json.dumps(copied)
    else:
        assert row['patch'] == 'PRIVATE_FIX' and row['test_patch'] == 'PRIVATE_TEST'
