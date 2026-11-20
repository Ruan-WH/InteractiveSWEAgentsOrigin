"""Preserve a native protocol interruption with wire-based usage."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common


def summarize(folder):
    result = json.loads((folder / "result.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    requests = sorted((folder / "http").glob("*.request.json"))
    responses = sorted((folder / "http").glob("*.response.json"))
    count = len(requests)
    assert count == len(responses) == result["model_calls"]
    assert result["tool_calls"] == count - 1
    patch = (folder / "model.patch").read_text()
    events = json.loads((folder / "tool_events.json").read_text()) if (folder / "tool_events.json").exists() else []
    request = json.loads(requests[0].read_text())["body"]
    records = [json.loads(p.read_text()) for p in responses]
    assert all(r["status_code"] == 200 for r in records)
    for path in requests:
        body = json.loads(path.read_text())["body"]
        assert body["parallel_tool_calls"] is False and body["tool_choice"] == "auto"
    assert request["messages"][1]["content"] == (folder / "task.txt").read_text()
    message = records[-1]["body"]["choices"][0]["message"]
    returned = message.get("tool_calls", [])
    assert len(returned) in (0, 2)
    terminal_miss = len(returned) == 0
    if terminal_miss:
        assert events and "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in events[-1]["result"]["output"]
        assert events[-1]["result"]["output"].splitlines()[0].strip() != "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        classification = "terminal submission-marker recognition failure followed by no-tool final response"
        fault = "Marker printed after git diff output was not recognized; following pure final message rejected for no tools"
    else:
        assert not patch
        classification = "early native multi-tool protocol interruption; not a completed repair-capability trial"
        fault = "Two native Bash tool calls returned despite parallel_tool_calls=false and exactly-one prompt"
    pause = 0
    if (folder / "checkpoint20_waiting.json").exists():
        waiting = json.loads((folder / "checkpoint20_waiting.json").read_text())
        review = json.loads((folder / "checkpoint20_review.json").read_text())
        pause = (datetime.fromisoformat(review["at"]) - datetime.fromisoformat(waiting["at"])).total_seconds()
    first = next((e for e in events if any(p.startswith("django/") for p in e["tracked_changes"].splitlines())), None)
    usage = {k: sum(r["body"]["usage"][k] for r in records)
             for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    reasoning = sum(r["body"]["usage"]["completion_tokens_details"]["reasoning_tokens"] for r in records)
    report_path, = (folder / "official_eval/logs").rglob("report.json")
    report = json.loads(report_path.read_text())[common.IID]
    config = tomllib.loads((common.PROJECT / "config.toml").read_text())
    keys = {v["api_key"].encode() for v in config["llm"].values()
            if isinstance(v, dict) and isinstance(v.get("api_key"), str) and len(v["api_key"]) > 10}
    hits = [str(p.relative_to(folder)) for p in folder.rglob("*")
            if p.is_file() and any(key in p.read_bytes() for key in keys)]
    wire = {"solver": {"http_requests": count, "http_responses": count,
                       "usage": {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
                       "reasoning_tokens_included_in_completion": reasoning,
                       "status_codes": [200] * count}}
    for role in ("checker", "proxy"):
        wire[role] = {"http_requests": 0, "http_responses": 0,
                      "usage": {k: 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
                      "reasoning_tokens_included_in_completion": 0, "status_codes": []}
    summary = {
        "condition": manifest["condition"], "model": manifest["model_settings"]["model"],
        "mini_version": manifest["mini_version"], "mini_commit": manifest["mini_git_commit"],
        "exit_status": result["exit_status"], "error": result["error"],
        "classification": classification,
        "solver_model_calls": count, "solver_tool_calls": result["tool_calls"], "wire": wire,
        "wire_audit": {"passed": False, "http_requests": count, "http_responses": count,
                       "reason": fault},
        "format_errors": result["format_errors"], "protocol_faults": 1,
        "usage_source": "All actual HTTP responses; unchanged result.json omits the last response because native parsing failed before storing its assistant message",
        "reported_result_usage": result["usage"],
        "wall_seconds_with_checkpoint": result["elapsed_seconds"], "checkpoint_seconds": pause,
        "wall_seconds_without_checkpoint": result["elapsed_seconds"] - pause,
        "clarification": json.loads((folder / "clarification_result.json").read_text()),
        "first_production_change": {
            "tool_call": first["tool_call"],
            "elapsed_seconds_with_checkpoint": first["elapsed_start_seconds"] + first["duration_seconds"],
            "elapsed_seconds_without_checkpoint": first["elapsed_start_seconds"] + first["duration_seconds"] - pause,
            "paths": first["tracked_changes"].splitlines(),
        } if first else None,
        "patch_bytes": len(patch.encode()),
        "patch_sha256": common.sha(folder / "model.patch"), "patch_paths": re.findall(r"^diff --git a/(.*?) b/", patch, re.M),
        "official_resolved": report["resolved"], "official_report": str(report_path),
        "patch_successfully_applied": report["patch_successfully_applied"],
        "official_tests": {k: {s: len(v) for s, v in status.items()} for k, status in report["tests_status"].items()},
        "official_interpretation": "Unmodified patch at interruption scored independently" if patch else "Empty raw patch evaluated through the same official run_instance; baseline score only",
        "credential_leaks_found": hits, "reference_repair_leak_observed": False,
        "hidden_feedback_to_model": False,
        "commands_containing_git_show_or_apply": [{"tool_call": e["tool_call"], "command": e["command"]}
                                                   for e in events if re.search(r"\bgit\s+(show|apply)(?:\s|$)", e["command"])],
        "returned_but_unexecuted_tool_calls": returned,
        "model_attempted_submission_before_terminal_fault": terminal_miss,
    }
    common.dump(folder / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.output.resolve())
