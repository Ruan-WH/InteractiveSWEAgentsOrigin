"""Native parser and observation chain must preserve tool IDs and failure codes."""

import json
import os

os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", "/tmp/ambig-mini-test-config")
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx
import litellm
import pytest
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import FormatError, Submitted

from evaluation.benchmarks.swe_bench.mini_swe.agent import AmbigAgent, NATIVE_SYSTEM, SYSTEM
from evaluation.benchmarks.swe_bench.mini_swe.runtime import BenchmarkEnvironment
from evaluation.benchmarks.swe_bench.mini_swe.model import (
    NativeBenchmarkModel,
    OBSERVATION,
)


@pytest.mark.parametrize("tool_choice", ["required", "auto"])
def test_native_tool_failure_roundtrip_and_secret_redaction(tmp_path, tool_choice):
    model = NativeBenchmarkModel(
        request_log_dir=tmp_path / "http",
        tool_choice=tool_choice,
        model_name="openai/test",
        model_kwargs={
            "api_base": "https://example.invalid/v1",
            "api_key": "private-key",
            "timeout": 5,
        },
        cost_tracking="ignore_errors",
        observation_template=OBSERVATION,
    )
    try:
        response = litellm.ModelResponse(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "Preserve this reasoning across the tool turn.",
                        "tool_calls": [
                            {
                                "id": "call_fail",
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": json.dumps({"command": "exit 7"}),
                                },
                            }
                        ],
                    }
                }
            ]
        )
        actions = model._parse_actions(response)
        assert actions == [{"command": "exit 7", "tool_call_id": "call_fail"}]
        assistant = response.choices[0].message.model_dump()
        assistant["extra"] = {"actions": actions}
        output = model.format_observation_messages(
            assistant, [{"output": "failed", "returncode": 7, "exception_info": ""}]
        )[0]
        assert output["role"] == "tool" and output["tool_call_id"] == "call_fail"
        assert "<returncode>7</returncode>" in output["content"]
        prepared = model._prepare_messages_for_api([assistant, output])
        assert prepared[0]["reasoning_content"] == "Preserve this reasoning across the tool turn."
        assert model.config.model_kwargs["tool_choice"] == tool_choice
        assert all("extra" not in message for message in prepared)
        assert prepared[0]["tool_calls"][0]["id"] == prepared[1]["tool_call_id"]
        model._record_request(
            httpx.Request(
                "POST",
                "https://example.invalid/v1/chat/completions",
                headers={"Authorization": "Bearer private-key"},
                json={"messages": prepared},
            )
        )
        assert "private-key" not in (tmp_path / "http/001.request.json").read_text()
        assert "private-key" not in json.dumps(model.serialize())
        response.choices[0].message.tool_calls[
            0
        ].function.arguments = '{"command": ["exit", "7"]}'
        with pytest.raises(FormatError):
            model._parse_actions(response)
    finally:
        model.close()
    assert "mswea_bash_command" not in NATIVE_SYSTEM
    assert (
        SYSTEM[SYSTEM.index("Use noninteractive") :]
        == NATIVE_SYSTEM[NATIVE_SYSTEM.index("Use noninteractive") :]
    )


def native_response(commands=None, content=None):
    return litellm.ModelResponse(
        model="test",
        choices=[{"message": {"role": "assistant", "content": content,
                              "reasoning_content": "keep reasoning",
                              "tool_calls": [
                                  {"id": f"call_{i}", "type": "function", "function": {
                                      "name": "bash", "arguments": json.dumps({"command": command})}}
                                  for i, command in enumerate(commands or [])]}}],
        usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    )


@pytest.fixture
def native_model(tmp_path):
    model = NativeBenchmarkModel(
        request_log_dir=tmp_path / "http", tool_choice="auto", model_name="openai/test",
        model_kwargs={"api_base": "https://example.invalid/v1", "api_key": "private-key", "timeout": 5},
        cost_tracking="ignore_errors", observation_template=OBSERVATION,
    )
    yield model
    model.close()


class ProtocolLocalEnvironment(LocalEnvironment):
    _check_finished = BenchmarkEnvironment._check_finished


def run_native(tmp_path, model, responses, **kwargs):
    captured = []
    iterator = iter(responses)

    def query(messages, **_):
        captured.append(messages)
        return next(iterator)

    model._query = query
    env = ProtocolLocalEnvironment(cwd=str(tmp_path))
    agent = AmbigAgent(model=model, env=env, system_template=NATIVE_SYSTEM, instance_template="{{task}}",
                       step_limit=5, cost_limit=0, output_path=tmp_path / "trace.json", **kwargs)
    result = agent.run("An unrelated temporary file task")
    return agent, result, captured


def test_multiple_tools_execute_in_order_with_ids_and_failures(tmp_path, native_model):
    agent, result, captured = run_native(tmp_path, native_model, [
        native_response(["printf alpha > probe", "cat probe; exit 7", "printf checked >> probe"]),
        native_response(content="Done\nCOMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"),
    ])
    assert result["exit_status"] == "Submitted"
    assert agent.n_calls == 2 and agent.n_tool_calls == 3
    assert (tmp_path / "probe").read_text() == "alphachecked"
    tools = [m for m in agent.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_0", "call_1", "call_2"]
    assert [m["extra"]["returncode"] for m in tools] == [0, 7, 0]
    assert "alpha" in tools[1]["content"]
    assert captured[1][2]["reasoning_content"] == "keep reasoning"
    assert [m["tool_call_id"] for m in captured[1] if m["role"] == "tool"] == ["call_0", "call_1", "call_2"]


def test_batch_stops_at_tool_budget_and_accounts_for_unexecuted_id(tmp_path, native_model):
    agent, result, _ = run_native(tmp_path, native_model, [native_response([
        "touch first", "touch second", "touch over_budget",
    ])], tool_limit=2)
    assert result["exit_status"] == "ToolLimitExceeded"
    assert agent.n_calls == 1 and agent.n_tool_calls == 2
    assert not (tmp_path / "over_budget").exists()
    tools = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tools) == 3 and tools[-1]["tool_call_id"] == "call_2"
    assert "action was not executed" in tools[-1]["content"]


def test_submission_after_other_output_skips_rest_of_batch(tmp_path, native_model):
    agent, result, _ = run_native(tmp_path, native_model, [native_response([
        "printf 'diff summary\\n'; echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "touch after_submission",
    ])])
    assert result["exit_status"] == "Submitted" and agent.n_tool_calls == 1
    assert not (tmp_path / "after_submission").exists()
    tools = [m for m in agent.messages if m["role"] == "tool"]
    assert "diff summary" in tools[0]["content"] and "action was not executed" in tools[1]["content"]


@pytest.mark.parametrize("content,commands", [
    ("No action and no completion marker", None),
    (None, ["touch must_not_execute", ["invalid", "command"]]),
])
def test_invalid_response_stops_once_and_keeps_usage(tmp_path, native_model, content, commands):
    agent, result, _ = run_native(tmp_path, native_model, [native_response(commands, content)])
    assert result["exit_status"] == "RepeatedFormatError" and agent.n_calls == 1
    assert agent.n_tool_calls == 0 and not (tmp_path / "must_not_execute").exists()
    saved = json.loads((tmp_path / "trace.json").read_text())
    billed = [m["extra"]["response"] for m in saved["messages"] if "response" in m.get("extra", {})]
    assert len(billed) == 1 and billed[0]["usage"]["total_tokens"] == 18


def test_completion_marker_requires_standalone_line_and_zero_exit():
    check = BenchmarkEnvironment._check_finished
    check(None, {"returncode": 7, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"})
    check(None, {"returncode": 0, "output": "quoted COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT text\n"})
    with pytest.raises(Submitted):
        check(None, {"returncode": 0, "output": "other output\nCOMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"})
