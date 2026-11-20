"""One new partial pair after protocol fixes; no issue-specific prompt changes."""

import argparse
import json
from pathlib import Path
import shutil

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.analysis.mini_10554_partial_20261119 import run as original
from evaluation.analysis.mini_10554_protocol_v2_20261119 import probe


def prepare(folder, followup, probe_dir, replay_dir):
    assert json.loads((probe_dir / "results.json").read_text())["passed"]
    assert json.loads((replay_dir / "results.json").read_text())["passed"]
    for case in ("read", "edit", "failure", "batch", "prefixed_submit", "text_submit"):
        probe.audit_wire(probe_dir / case / "http")
    original.prepare(folder, followup)
    manifest = json.loads((folder / "manifest.json").read_text())
    for source in (Path(__file__), Path(probe.__file__), common.PROJECT / "tests/unit/test_mini_swe_native.py"):
        relative = source.relative_to(common.PROJECT)
        target = folder / "frozen_source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest["source_sha256"][str(relative)] = common.sha(source)
    manifest.update(
        protocol="native-sequential-bash-explicit-submission-v2",
        protocol_probes=str(probe_dir), offline_replays=str(replay_dir),
        protocol_probes_sha256=common.sha(probe_dir / "results.json"),
        offline_replays_sha256=common.sha(replay_dir / "results.json"),
        native_multi_call_policy="Validate whole response, execute sequentially, preserve every tool ID and return code",
        tool_budget_policy="At most100 executed actions, including every command in multi-call replies; skipped actions receive explicit unexecuted observations",
        termination_policy="Standalone marker in successful command output at any line, or explicitly marked no-tool final response",
        invalid_response_policy="Stop on first FormatError; persist the billed raw response and usage",
        differences=[
            "Only local protocol adapter/agent/environment changed; upstream mini2.4.6 commit unchanged",
            "Multiple native calls accepted sequentially despite keeping parallel_tool_calls=false and same ONE-call system prompt",
            "Explicit terminal marker recognized after other command output or in marked no-tool final response",
            "Tool budget enforced per action, and invalid replies retain usage before stopping",
            "Same DeepSeek parameters, baseline, image, dependencies, issue and partial answer, original simple-v1 controlled clarification",
        ],
    )
    common.dump(folder / "manifest.json", manifest)


def run(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    for key, hashkey in (("protocol_probes", "protocol_probes_sha256"), ("offline_replays", "offline_replays_sha256")):
        assert common.sha(Path(manifest[key]) / "results.json") == manifest[hashkey]
    original.run(folder)
    prediction = json.loads((folder / "predictions.json").read_text())
    prediction["model_name_or_path"] = "mini-deepseek-native-v2-" + manifest["condition"]
    common.dump(folder / "predictions.json", prediction)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "clarify", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--followup", action="store_true")
    parser.add_argument("--probe-dir", type=Path)
    parser.add_argument("--replay-dir", type=Path)
    args = parser.parse_args()
    folder = args.output.resolve()
    if args.stage == "prepare":
        prepare(folder, args.followup, args.probe_dir.resolve(), args.replay_dir.resolve())
    else:
        (original.clarify if args.stage == "clarify" else run)(folder)
