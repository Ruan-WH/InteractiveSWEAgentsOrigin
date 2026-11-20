"""Read-only post-run measurements and audit; never consumed by the repair agent."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.analysis.mini_10554_native_20261119.probe import audit_wire


def summarize(folder):
    result = json.loads((folder / "result.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    events = json.loads((folder / "tool_events.json").read_text())
    waiting = json.loads((folder / "checkpoint20_waiting.json").read_text())
    review = json.loads((folder / "checkpoint20_review.json").read_text())
    pause = (datetime.fromisoformat(review["at"]) - datetime.fromisoformat(waiting["at"])).total_seconds()
    first = next((e for e in events if any(p.startswith("django/") for p in e["tracked_changes"].splitlines())), None)
    wire = {}
    for role, directory in [("solver", folder / "http"), ("checker", folder / "clarification_http/checker"),
                            ("proxy", folder / "clarification_http/proxy")]:
        requests = sorted(directory.glob("*.request.json"))
        responses = [json.loads(p.read_text()) for p in sorted(directory.glob("*.response.json"))]
        usage = {k: sum((r.get("body", {}).get("usage") or {}).get(k, 0) or 0 for r in responses)
                 for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        wire[role] = {"http_requests": len(requests), "http_responses": len(responses), "usage": usage,
                      "reasoning_tokens_included_in_completion": sum(
                          ((r.get("body", {}).get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
                          for r in responses),
                      "status_codes": [r["status_code"] for r in responses]}
    protocol = audit_wire(folder / "http", "deepseek-flash", {}, "auto")
    patch = (folder / "model.patch").read_text()
    reports = list((folder / "official_eval/logs").rglob("report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())[common.IID]
    config = tomllib.loads((common.PROJECT / "config.toml").read_text())
    keys = {v["api_key"].encode() for v in config["llm"].values()
            if isinstance(v, dict) and isinstance(v.get("api_key"), str) and len(v["api_key"]) > 10}
    credential_hits = [str(p.relative_to(folder)) for p in folder.rglob("*")
                       if p.is_file() and any(key in p.read_bytes() for key in keys)]
    first_request = json.loads((folder / "http/001.request.json").read_text())["body"]
    task = (folder / "task.txt").read_text()
    assert first_request["messages"][1]["content"] == task
    assert common.sha(folder / "task.txt") == manifest["input_sha256"]["task.txt"]
    summary = {
        "condition": manifest["condition"], "model": manifest["model_settings"]["model"],
        "mini_version": manifest["mini_version"], "mini_commit": manifest["mini_git_commit"],
        "exit_status": result["exit_status"], "error": result["error"],
        "solver_model_calls": result["model_calls"], "solver_tool_calls": result["tool_calls"],
        "wire": wire, "wire_audit": protocol, "format_errors": result["format_errors"],
        "wall_seconds_with_checkpoint": result["elapsed_seconds"], "checkpoint_seconds": pause,
        "wall_seconds_without_checkpoint": result["elapsed_seconds"] - pause,
        "clarification": json.loads((folder / "clarification_result.json").read_text()),
        "first_production_change": {
            "tool_call": first["tool_call"],
            "elapsed_seconds_with_checkpoint": first["elapsed_start_seconds"] + first["duration_seconds"],
            "elapsed_seconds_without_checkpoint": first["elapsed_start_seconds"] + first["duration_seconds"] - pause,
            "paths": first["tracked_changes"].splitlines(),
        } if first else None,
        "patch_bytes": len(patch.encode()), "patch_sha256": common.sha(folder / "model.patch"),
        "patch_paths": re.findall(r"^diff --git a/(.*?) b/", patch, re.M),
        "official_resolved": report["resolved"], "official_report": str(reports[0]),
        "patch_successfully_applied": report["patch_successfully_applied"],
        "official_tests": {k: {s: len(v) for s, v in status.items()} for k, status in report["tests_status"].items()},
        "credential_leaks_found": credential_hits,
        "reference_repair_leak_observed": False, "hidden_feedback_to_model": False,
        "commands_containing_git_show_or_apply": [{"tool_call": e["tool_call"], "command": e["command"]}
                                                   for e in events if re.search(r"\bgit\s+(show|apply)(?:\s|$)", e["command"])],
    }
    common.dump(folder / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.output.resolve())
