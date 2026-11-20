"""Protocol-corrected independent run retaining screenshot DeepSeek parameters."""

import argparse
from functools import partial
import json
from pathlib import Path

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.analysis.mini_10554_native_20261119 import probe

CONFIG = "hidden_deepseek_flash"


def prepare(folder, probe_dir):
    assert json.loads((probe_dir / "results.json").read_text())["passed"]
    for name in ("read", "edit", "failure"):
        probe.audit_wire(probe_dir / name / "http", "deepseek-flash", {}, "auto")
    common.prepare(folder, extra_sources=(Path(__file__), Path(probe.__file__)),
                   solver_config=CONFIG, compare_old_model=False)
    manifest = json.loads((folder / "manifest.json").read_text())
    reference = folder.parent / "deepseek-10554-ideal-screenshot-20261119-v1"
    old = json.loads((reference / "manifest.json").read_text())
    assert manifest["model_settings"] == old["model_settings"]
    assert manifest["image_id"] == old["image_id"]
    for name in ("issue.txt", "ideal_question.txt", "ideal_answer.txt", "task.txt", "dependencies.txt"):
        assert (folder / name).read_bytes() == (reference / name).read_bytes()
    state = json.loads((folder / "container.json").read_text())
    assert state["config"]["interpreter"] == ["timeout", "-s", "KILL", "120", "bash", "-o", "pipefail", "-c"]
    manifest.update(
        protocol="native-structured-bash-v2", tool_choice="auto",
        api_retry_override=2, whole_request_attempts=1,
        protocol_probe=str(probe_dir), protocol_probe_sha256=common.sha(probe_dir / "results.json"),
        comparison_run=str(reference),
        differences=[
            "Native structured tools replace text Bash blocks; tool_choice auto is required by endpoint default thinking mode",
            "Model/endpoint/sampling/16384 output cap/default thinking unchanged from screenshot configuration",
            "Enable bash pipefail so a failed test in a pipeline cannot return success solely because tail/grep succeeds",
            "Same 10554 task/ideal QA/image/baseline/dependencies, no further replies or additional issue context",
            "Same safety audit and 20-tool checkpoint; whole-request retries disabled and account errors stop",
        ],
    )
    common.dump(folder / "manifest.json", manifest)


def run(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel

    manifest = json.loads((folder / "manifest.json").read_text())
    assert common.sha(Path(manifest["protocol_probe"]) / "results.json") == manifest["protocol_probe_sha256"]
    common.run(folder, model_class=partial(NativeBenchmarkModel, request_log_dir=folder / "http", tool_choice="auto"),
               system_template=NATIVE_SYSTEM, stop_on_protocol_fault=True, solver_config=CONFIG, api_retries=2)
    pred = json.loads((folder / "predictions.json").read_text())
    pred["model_name_or_path"] = "mini-deepseek-thinking-native-ideal-no-followup"
    common.dump(folder / "predictions.json", pred)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare(args.output.resolve(), args.probe_dir.resolve())
    else:
        run(args.output.resolve())
