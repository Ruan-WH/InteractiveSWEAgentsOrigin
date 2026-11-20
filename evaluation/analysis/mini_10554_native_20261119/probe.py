"""Real endpoint probes unrelated to the benchmark issue."""

import argparse
import json
from pathlib import Path
import secrets
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common


def audit_wire(folder, expected_model="qwen3.8-flash", expected_extra_body=None, expected_tool_choice="required"):
    expected_extra_body = (
        {"enable_thinking": False}
        if expected_extra_body is None
        else expected_extra_body
    )
    requests = sorted(folder.glob("*.request.json"))
    responses = sorted(folder.glob("*.response.json"))
    assert requests and len(requests) == len(responses)
    tool_messages = 0
    reasoning_by_call = {}
    for request_path, response_path in zip(requests, responses):
        request = json.loads(request_path.read_text())["body"]
        response = json.loads(response_path.read_text())
        assert response["status_code"] == 200
        assert request["model"] == expected_model
        assert all(request[key] == value for key, value in expected_extra_body.items())
        assert request["temperature"] == 0 and request["top_p"] == 1
        assert (
            request["tool_choice"] == expected_tool_choice
            and request["parallel_tool_calls"] is False
        )
        assert [tool["function"]["name"] for tool in request["tools"]] == ["bash"]
        assert "mswea_bash_command" not in request["messages"][0]["content"]
        pending = []
        for message in request["messages"]:
            if message["role"] == "assistant":
                assert not pending
                pending = [call["id"] for call in message.get("tool_calls", [])]
                for call_id in pending:
                    if reasoning_by_call.get(call_id):
                        assert message.get("reasoning_content") == reasoning_by_call[call_id]
            elif message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
                assert "<returncode>" in message["content"]
                tool_messages += 1
            else:
                assert not pending
        assert not pending
        calls = response["body"]["choices"][0]["message"].get("tool_calls", [])
        assert len(calls) == 1 and calls[0]["function"]["name"] == "bash"
        command = json.loads(calls[0]["function"]["arguments"])["command"]
        assert isinstance(command, str) and command.strip()
        assert "</parameter>" not in command and "mswea_bash_command" not in command
        reasoning_by_call[calls[0]["id"]] = response["body"]["choices"][0]["message"].get("reasoning_content")
    return {
        "http_requests": len(requests),
        "http_responses": len(responses),
        "tool_messages_across_requests": tool_messages,
        "passed": True,
    }


def probe(folder, solver_config=common.CONFIG, tool_choice="required", settings_override=None):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import AmbigAgent, NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import (
        NativeBenchmarkModel,
        model_options,
        OBSERVATION,
    )
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import (
        initialize,
        start_environment,
    )

    folder.mkdir(parents=True, exist_ok=False)
    settings = tomllib.loads((common.PROJECT / "config.toml").read_text())["llm"][
        solver_config
    ]
    settings.update(settings_override or {})
    old = json.loads((common.OLD / "manifest.json").read_text())
    if solver_config == common.CONFIG and not settings_override:
        assert all(
            settings[key] == value for key, value in old["solver_config"].items()
        )
    env = start_environment(common.IMAGE, 120, "none")
    results = []
    token = secrets.token_hex(10)
    root = "/tmp/mini-native-probe"
    try:
        initialize(env, {"base_commit": "14d026cccb144c6877294ba4cd4e03ebf0842498"})
        env.config.interpreter = [
            "timeout",
            "-s",
            "KILL",
            "120",
            "bash",
            "-o",
            "pipefail",
            "-c",
        ]
        common.shell(
            env,
            f"mkdir -p {root}; printf '%s\\n' '{token}' > {root}/read.txt; printf 'keep\\nORIGINAL\\nend\\n' > {root}/edit.txt",
        )
        tasks = [
            (
                "read",
                f"Read {root}/read.txt and copy its exact contents to {root}/copy.txt. Verify that the copy matches. Then submit.",
                f"cat {root}/copy.txt",
                token + "\n",
            ),
            (
                "edit",
                f"In {root}/edit.txt replace the line ORIGINAL with CHANGED and preserve all other lines. Verify the resulting contents. Then submit.",
                f"cat {root}/edit.txt",
                "keep\nCHANGED\nend\n",
            ),
            (
                "failure",
                f"As your first Bash tool call, execute exactly: bash -c 'printf intentional_failure; exit 7'\n"
                f"After reading its tool result, write the observed numeric return code followed by a newline to {root}/exit.txt. Then submit.",
                f"cat {root}/exit.txt",
                "7\n",
            ),
        ]
        for name, task, verify, expected in tasks:
            case = folder / name
            case.mkdir()
            options = model_options(settings)
            options["num_retries"] = 0
            model = NativeBenchmarkModel(
                request_log_dir=case / "http",
                tool_choice=tool_choice,
                model_name=settings["model"],
                model_kwargs=options,
                cost_tracking="ignore_errors",
                observation_template=OBSERVATION,
            )
            agent = AmbigAgent(
                model,
                env,
                reporter=None,
                system_template=NATIVE_SYSTEM,
                instance_template="{{task}}",
                step_limit=8,
                cost_limit=0,
                max_consecutive_format_errors=1,
                output_path=case / "trajectory.json",
            )
            agent.instance_id = "protocol-" + name
            try:
                result = agent.run(task)
                assert result["exit_status"] == "Submitted"
                assert not any(
                    m.get("extra", {}).get("interrupt_type") == "FormatError"
                    for m in agent.messages
                )
                assert common.shell(env, verify) == expected
                wire = audit_wire(
                    case / "http",
                    settings["model"].removeprefix("openai/"),
                    settings.get("extra_body", {}),
                    tool_choice,
                )
                if name == "failure":
                    first = json.loads((case / "http/001.response.json").read_text())[
                        "body"
                    ]
                    command = json.loads(
                        first["choices"][0]["message"]["tool_calls"][0]["function"][
                            "arguments"
                        ]
                    )["command"]
                    # The environment already invokes bash; both forms have the same failure behavior.
                    assert command in (
                        "bash -c 'printf intentional_failure; exit 7'",
                        "printf intentional_failure; exit 7",
                    )
                    second = json.loads((case / "http/002.request.json").read_text())[
                        "body"
                    ]
                    assert second["messages"][-1]["role"] == "tool"
                    assert (
                        "<returncode>7</returncode>"
                        in second["messages"][-1]["content"]
                    )
                    assert "intentional_failure" in second["messages"][-1]["content"]
                results.append(
                    {
                        "case": name,
                        "model_calls": agent.n_calls,
                        "verification_passed": True,
                        "wire_audit": wire,
                    }
                )
                common.dump(
                    folder / "results.json", {"passed": False, "completed": results}
                )
            finally:
                model.close()
        common.dump(
            folder / "results.json",
            {
                "passed": True,
                "model": settings["model"],
                "sampling": {
                    k: settings[k] for k in ("temperature", "top_p", "extra_body") if k in settings
                },
                "completed": results,
            },
        )
    finally:
        env.cleanup()
    print(json.dumps({"passed": True, "completed": results}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solver-config", default=common.CONFIG)
    parser.add_argument("--tool-choice", choices=("required", "auto"), default="required")
    args = parser.parse_args()
    probe(args.output.resolve(), args.solver_config, args.tool_choice)
