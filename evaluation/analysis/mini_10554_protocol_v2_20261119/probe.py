"""Offline old-response replays and live issue-independent native protocol checks."""

import argparse
import json
from pathlib import Path
import secrets
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common


def audit_wire(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.protocol import submission_text

    requests = sorted(folder.glob("*.request.json"))
    responses = sorted(folder.glob("*.response.json"))
    assert requests and len(requests) == len(responses)
    reasoning = {}
    returned, batches, finals = 0, 0, 0
    for qpath, rpath in zip(requests, responses):
        q = json.loads(qpath.read_text())["body"]
        r = json.loads(rpath.read_text())
        assert r["status_code"] == 200
        assert q["model"] == "deepseek-flash" and q["temperature"] == 0 and q["top_p"] == 1
        assert q["max_tokens"] == 16384 and q["tool_choice"] == "auto" and q["parallel_tool_calls"] is False
        assert [t["function"]["name"] for t in q["tools"]] == ["bash"]
        assert "thinking" not in q and "extra_body" not in q
        pending = set()
        for message in q["messages"]:
            if message["role"] == "assistant":
                assert not pending
                pending = {call["id"] for call in message.get("tool_calls", [])}
                for call_id in pending:
                    if reasoning.get(call_id):
                        assert message.get("reasoning_content") == reasoning[call_id]
            elif message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
                assert "<returncode>" in message["content"]
            else:
                assert not pending
        assert not pending
        message = r["body"]["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            assert submission_text(message.get("content")) is not None and rpath == responses[-1]
            finals += 1
        ids = [call["id"] for call in calls]
        assert len(ids) == len(set(ids))
        for call in calls:
            assert call["function"]["name"] == "bash"
            command = json.loads(call["function"]["arguments"])["command"]
            assert isinstance(command, str) and command.strip()
            assert "</parameter>" not in command and "mswea_bash_command" not in command
            reasoning[call["id"]] = message.get("reasoning_content")
        returned += len(calls)
        batches += len(calls) > 1
    return {"passed": True, "http_requests": len(requests), "http_responses": len(responses),
            "returned_tool_calls": returned, "multi_tool_responses": batches, "explicit_final_responses": finals}


def offline(folder):
    import litellm
    from minisweagent.exceptions import Submitted
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel, OBSERVATION
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import BenchmarkEnvironment

    folder.mkdir(parents=True, exist_ok=False)
    base = common.PROJECT / "evaluation/evaluation_outputs/mini_swe"
    results = []
    model = NativeBenchmarkModel(request_log_dir=folder / "unused_http", tool_choice="auto",
                                 model_name="openai/test", model_kwargs={"api_key": "offline-placeholder",
                                 "api_base": "https://example.invalid/v1", "timeout": 5},
                                 cost_tracking="ignore_errors", observation_template=OBSERVATION)
    try:
        for version, number in ((3, 1), (4, 3)):
            source = base / f"deepseek-10554-partial-no-followup-20261119-v{version}/http/{number:03}.response.json"
            response = litellm.ModelResponse(**json.loads(source.read_text())["body"])
            actions = model._parse_actions(response)
            assert len(actions) == 2
            message = response.choices[0].message.model_dump()
            message["extra"] = {"actions": actions}
            outputs = model.format_observation_messages(message, [
                {"output": "synthetic failure", "returncode": 7, "exception_info": ""},
                {"output": "synthetic success", "returncode": 0, "exception_info": ""},
            ])
            assert [o["tool_call_id"] for o in outputs] == [a["tool_call_id"] for a in actions]
            assert "<returncode>7</returncode>" in outputs[0]["content"]
            results.append({"source": str(source), "sha256": common.sha(source),
                            "parsed_tools": 2, "outputs_preserve_ids_and_returncodes": True})
        old = base / "deepseek-10554-partial-followup-20261119-v3"
        response = litellm.ModelResponse(**json.loads((old / "http/051.response.json").read_text())["body"])
        assert model._parse_actions(response) == []
        event = json.loads((old / "tool_events.json").read_text())[-1]
        try:
            BenchmarkEnvironment._check_finished(None, event["result"])
        except Submitted:
            results.append({"source": str(old), "prefixed_command_marker_recognized": True,
                            "explicit_no_tool_final_recognized": True})
        else:
            raise AssertionError("Old terminal marker still unrecognized")
    finally:
        model.close()
    common.dump(folder / "results.json", {"passed": True, "api_calls": 0, "replays": results})
    print("OFFLINE REPLAYS PASSED", flush=True)


def live(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import AmbigAgent, NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel, model_options, OBSERVATION
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import start_environment, initialize

    folder.mkdir(parents=True, exist_ok=False)
    settings = tomllib.loads((common.PROJECT / "config.toml").read_text())["llm"]["hidden_deepseek_flash"]
    token = secrets.token_hex(8)
    root = "/tmp/native-protocol-v2-probes"
    env = start_environment(common.IMAGE, 120, "none")
    results = []
    try:
        initialize(env, {"base_commit": "14d026cccb144c6877294ba4cd4e03ebf0842498"})
        env.config.interpreter = ["timeout", "-s", "KILL", "120", "bash", "-o", "pipefail", "-c"]
        common.shell(env, f"mkdir -p {root}; printf '%s\\n' '{token}' > {root}/read; printf 'keep\\nORIGINAL\\nend\\n' > {root}/edit")
        multi_system = NATIVE_SYSTEM.replace(
            "Explain your next action briefly, then use exactly ONE native bash tool call with a command argument.",
            "Use native Bash tools. Multiple calls in one response run sequentially in the listed order.",
        )
        cases = [
            ("read", f"Read {root}/read and copy its exact content to {root}/copy. Verify it, then submit.",
             NATIVE_SYSTEM, f"cat {root}/copy", token + "\n"),
            ("edit", f"Replace only ORIGINAL with CHANGED in {root}/edit. Verify it, then submit.",
             NATIVE_SYSTEM, f"cat {root}/edit", "keep\nCHANGED\nend\n"),
            ("failure", f"First execute exactly: bash -c 'printf intentional_failure; exit 7'\nThen write the observed exit code followed by a newline into {root}/exit and submit.",
             NATIVE_SYSTEM, f"cat {root}/exit", "7\n"),
            ("batch", f"In your FIRST response make two SEPARATE Bash tool calls, in this order: first printf alpha into {root}/batch; second verify that file contains alpha and append beta to it. Do not combine them into one call. Then submit.",
             multi_system, f"cat {root}/batch", "alphabeta"),
            ("prefixed_submit", "First execute exactly: printf 'summary before completion\\n'; echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
             NATIVE_SYSTEM, None, None),
            ("text_submit", "Do not use tools. Reply with just COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT on its own line.",
             "This is a protocol-only check. Follow the user's output instructions; tools are optional.", None, None),
        ]
        for name, task, system, verify, expected in cases:
            case = folder / name
            case.mkdir()
            options = model_options(settings)
            options["num_retries"] = 0
            model = NativeBenchmarkModel(request_log_dir=case / "http", tool_choice="auto",
                                         model_name=settings["model"], model_kwargs=options,
                                         cost_tracking="ignore_errors", observation_template=OBSERVATION)
            try:
                agent = AmbigAgent(model, env, system_template=system, instance_template="{{task}}",
                                   step_limit=8, tool_limit=20, cost_limit=0, output_path=case / "trajectory.json")
                result = agent.run(task)
                assert result["exit_status"] == "Submitted"
                if verify:
                    assert common.shell(env, verify) == expected
                wire = audit_wire(case / "http")
                if name == "batch":
                    assert wire["multi_tool_responses"] >= 1
                if name == "failure":
                    assert next(m for m in agent.messages if m["role"] == "tool")["extra"]["returncode"] == 7
                if name == "text_submit":
                    assert agent.n_tool_calls == 0
                results.append({"name": name, "passed": True, "model_calls": agent.n_calls,
                                "executed_tool_calls": agent.n_tool_calls, "wire": wire})
                common.dump(folder / "results.json", {"passed": len(results) == len(cases), "cases": results})
                print("PROBE PASSED", name, agent.n_calls, agent.n_tool_calls, flush=True)
            finally:
                model.close()
    finally:
        env.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("offline", "live"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (offline if args.stage == "offline" else live)(args.output.resolve())
