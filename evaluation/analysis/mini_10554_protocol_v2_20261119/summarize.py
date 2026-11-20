"""Wire-based measurements for sequential native-tool experiments."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.analysis.mini_10554_protocol_v2_20261119.probe import audit_wire


def summarize(folder):
    result = json.loads((folder / "result.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    trajectory = json.loads((folder / "trajectory.json").read_text())
    events = json.loads((folder / "tool_events.json").read_text()) if (folder / "tool_events.json").exists() else []
    pause = 0
    if (folder / "checkpoint20_waiting.json").exists():
        waiting = json.loads((folder / "checkpoint20_waiting.json").read_text())
        review = json.loads((folder / "checkpoint20_review.json").read_text())
        pause = (datetime.fromisoformat(review["at"]) - datetime.fromisoformat(waiting["at"])).total_seconds()
    wire = {}
    for role, path in (("solver", folder / "http"), ("checker", folder / "clarification_http/checker"),
                       ("proxy", folder / "clarification_http/proxy")):
        responses = [json.loads(p.read_text()) for p in sorted(path.glob("*.response.json"))]
        wire[role] = {
            "http_requests": len(list(path.glob("*.request.json"))), "http_responses": len(responses),
            "usage": {key: sum((r.get("body", {}).get("usage") or {}).get(key, 0) or 0 for r in responses)
                      for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "reasoning_tokens_included_in_completion": sum(
                ((r.get("body", {}).get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
                for r in responses),
            "status_codes": [r["status_code"] for r in responses],
        }
    try:
        audit = audit_wire(folder / "http")
    except Exception as error:
        audit = {"passed": False, "error": f"{type(error).__name__}: {error}"}
    tool_messages = [m for m in trajectory["messages"] if m["role"] == "tool"]
    skipped = sum(m.get("extra", {}).get("exception_info") == "action was not executed" for m in tool_messages)
    assert trajectory["info"]["tool_execution"]["executed"] == result["tool_calls"] == len(events)
    assert result["usage"] == wire["solver"]["usage"], "Response accounting differs from wire usage"
    first = next((e for e in events if any(p.startswith("django/") for p in e["tracked_changes"].splitlines())), None)
    first_time = first["elapsed_start_seconds"] + first["duration_seconds"] if first else None
    report_path, = (folder / "official_eval/logs").rglob("report.json")
    report = json.loads(report_path.read_text())[common.IID]
    patch = (folder / "model.patch").read_text()
    config = tomllib.loads((common.PROJECT / "config.toml").read_text())
    keys = {v["api_key"].encode() for v in config["llm"].values()
            if isinstance(v, dict) and isinstance(v.get("api_key"), str) and len(v["api_key"]) > 10}
    hits = [str(p.relative_to(folder)) for p in folder.rglob("*")
            if p.is_file() and any(key in p.read_bytes() for key in keys)]
    summary = {
        "condition": manifest["condition"], "protocol": manifest["protocol"],
        "model": manifest["model_settings"]["model"], "mini_version": manifest["mini_version"],
        "mini_commit": manifest["mini_git_commit"], "exit_status": result["exit_status"], "error": result["error"],
        "solver_model_calls": result["model_calls"], "solver_tool_calls": result["tool_calls"],
        "wire": wire, "wire_audit": audit, "format_errors": result["format_errors"],
        "skipped_tool_calls": skipped, "wall_seconds_with_checkpoint": result["elapsed_seconds"],
        "checkpoint_seconds": pause, "wall_seconds_without_checkpoint": result["elapsed_seconds"] - pause,
        "clarification": json.loads((folder / "clarification_result.json").read_text()),
        "first_production_change": {
            "tool_call": first["tool_call"], "elapsed_seconds_with_checkpoint": first_time,
            "elapsed_seconds_without_checkpoint": first_time - (pause if first["tool_call"] > 20 else 0),
            "paths": first["tracked_changes"].splitlines(),
        } if first else None,
        "patch_bytes": len(patch.encode()), "patch_sha256": common.sha(folder / "model.patch"),
        "patch_paths": re.findall(r"^diff --git a/(.*?) b/", patch, re.M),
        "official_resolved": report["resolved"], "official_report": str(report_path),
        "official_tests": {k: {s: len(v) for s, v in group.items()} for k, group in report["tests_status"].items()},
        "credential_leaks_found": hits, "hidden_feedback_to_model": False, "reference_repair_leak_observed": False,
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
