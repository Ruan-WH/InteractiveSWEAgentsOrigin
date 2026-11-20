"""Exercise the actual upstream loop, parser, and independent patch extraction."""

import json
import os
from pathlib import Path
import subprocess

os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", "/tmp/ambig-mini-test-config")
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

import pytest
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output
from minisweagent.models.utils.actions_text import parse_regex_actions
from minisweagent.exceptions import FormatError

from evaluation.benchmarks.swe_bench.mini_swe.agent import AmbigAgent, SYSTEM
from evaluation.benchmarks.swe_bench.mini_swe.model import make_model
from evaluation.benchmarks.swe_bench.mini_swe.reporter import Reporter
from evaluation.benchmarks.swe_bench.mini_swe.run import build_task, load_instances
from evaluation.benchmarks.swe_bench.mini_swe.runtime import extract_patch


def test_loop_edits_and_budget_saves_patch(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    (tmp_path / "source.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@local",
            "commit",
            "-m",
            "baseline",
        ],
        capture_output=True,
        check=True,
    )
    base = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    model = DeterministicModel(
        outputs=[
            make_output(
                "edit",
                [
                    {
                        "command": "printf 'value = 2\\n' > source.py; printf 'new = True\\n' > new.py"
                    }
                ],
            )
        ]
    )
    env = LocalEnvironment(cwd=str(tmp_path))
    agent = AmbigAgent(
        model,
        env,
        system_template=SYSTEM,
        instance_template="{{task}}",
        step_limit=1,
        cost_limit=0,
        output_path=tmp_path / "trace.json",
    )
    assert agent.run("fix")["exit_status"] == "LimitsExceeded"
    assert agent.n_calls == 1
    assert (
        json.loads((tmp_path / "trace.json").read_text())["info"]["exit_status"]
        == "LimitsExceeded"
    )
    (tmp_path / "trace.json").unlink()

    class CheckedLocal:
        def checked(self, command):
            return subprocess.check_output(
                [
                    "bash",
                    "-c",
                    command.replace(
                        "/tmp/ambig-mini-patch.index",
                        str(tmp_path / ".git/patch.index"),
                    ),
                ],
                cwd=tmp_path,
                text=True,
            )

    patch = extract_patch(CheckedLocal(), base)
    assert "+value = 2" in patch and "new file mode" in patch and "+new = True" in patch
    assert not subprocess.check_output(
        ["git", "-C", str(tmp_path), "diff", "--cached"], text=True
    )


def test_interaction_cap_and_no_private_data_in_agent(tmp_path):
    row = {
        "instance_id": "example__repo-1",
        "base_commit": "abc",
        "problem_statement": "public summary",
        "original_issue": "private issue",
        "hints_text": "private hint",
    }
    fixture = {
        "instance_id": row["instance_id"],
        "base_commit": "abc",
        "public_task": {"summary": row["problem_statement"]},
        "initial_responses": {
            "question": "clarify?",
            "ideal": {"answer": "public answer", "disclosed_fact_ids": []},
        },
    }
    reporter = Reporter(
        row,
        {"api_key": "test-key", "model": "openai/test"},
        tmp_path / "reporter.json",
        fixture,
        "ideal",
    )
    reporter.controlled.clarification_closed = True
    outputs = [
        make_output("ask", [{"command": "ask_user 'followup?'"}]),
        make_output(
            "done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]
        ),
    ]
    agent = AmbigAgent(
        DeterministicModel(outputs=outputs),
        LocalEnvironment(cwd=str(tmp_path)),
        reporter=reporter,
        system_template=SYSTEM,
        instance_template="{{task}}",
        cost_limit=0,
    )
    assert agent.run(build_task(row, "interaction"))["exit_status"] == "Submitted"
    transcript = json.dumps(agent.messages)
    assert "Do NOT ask for more help" in transcript
    assert "private issue" not in transcript and "private hint" not in transcript
    assert len(reporter.interactions) == 1
    reporter.interactions.extend([{}, {}])
    assert "Do NOT ask" in reporter.reply("another?")
    assert "private issue" in build_task(row, "full") and "private hint" in build_task(
        row, "full"
    )
    assert build_task(row, "hidden") == "public summary"


def test_parser_and_credentials(tmp_path):
    model = make_model(
        {
            "model": "openai/test",
            "api_key": "must-not-be-saved",
            "extra_body": {"enable_thinking": False},
        }
    )
    assert "must-not-be-saved" not in json.dumps(model.serialize())
    assert model.config.model_kwargs["extra_body"] == {"enable_thinking": False}
    assert parse_regex_actions(
        "```mswea_bash_command\necho hello\n```",
        action_regex=model.config.action_regex,
        format_error_template=model.config.format_error_template,
    ) == [{"command": "echo hello"}]
    with pytest.raises(FormatError):
        parse_regex_actions(
            "I am still thinking.",
            action_regex=model.config.action_regex,
            format_error_template=model.config.format_error_template,
        )
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps({"instance_id": iid}) for iid in ("a", "b")))
    assert [r["instance_id"] for r in load_instances(path, ["b", "a"])] == ["b", "a"]
    with pytest.raises(ValueError):
        load_instances(path, ["missing"])
