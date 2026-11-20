"""Qwen model swap with successful DeepSeek 10554 settings held fixed."""

import argparse
from functools import partial
import json
from pathlib import Path

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.analysis.mini_10554_native_20261119 import probe

CONFIG = "hidden_qwen38_flash"
OVERRIDES = {
    "max_output_tokens": 16384,
    "extra_body": {"enable_thinking": True, "preserve_thinking": True},
}
REFERENCE = common.PROJECT / "evaluation/evaluation_outputs/mini_swe/deepseek-10554-ideal-thinking-native-20261119-v2"


def prepare(folder, probe_dir):
    assert json.loads((probe_dir / "results.json").read_text())["passed"]
    for name in ("read", "edit", "failure"):
        probe.audit_wire(probe_dir / name / "http", "qwen3.8-flash", OVERRIDES["extra_body"], "auto")
    common.prepare(folder, extra_sources=(Path(__file__), Path(probe.__file__)),
                   solver_config=CONFIG, compare_old_model=False, settings_override=OVERRIDES)
    manifest = json.loads((folder / "manifest.json").read_text())
    reference = json.loads((REFERENCE / "manifest.json").read_text())
    assert manifest["image_id"] == reference["image_id"]
    for key in ("temperature", "top_p", "max_output_tokens", "timeout"):
        assert manifest["model_settings"][key] == reference["model_settings"][key]
    for name in ("issue.txt", "ideal_question.txt", "ideal_answer.txt", "task.txt", "dependencies.txt"):
        assert (folder / name).read_bytes() == (REFERENCE / name).read_bytes()
    assert json.loads((folder / "container.json").read_text())["config"]["interpreter"] == ["timeout", "-s", "KILL", "120", "bash", "-o", "pipefail", "-c"]
    manifest.update(
        protocol="native-structured-bash-v2", tool_choice="auto", api_retry_override=2,
        whole_request_attempts=1, protocol_probe=str(probe_dir),
        protocol_probe_sha256=common.sha(probe_dir / "results.json"), comparison_run=str(REFERENCE),
        differences=[
            "Model/endpoint/credentials changed to qwen3.8-flash; service alias is not a pinned snapshot",
            "Equivalent thinking enabled with Qwen enable_thinking/preserve_thinking true; no explicit thinking_budget",
            "Qwen max_input_tokens metadata 131072 versus DeepSeek 1000000; neither is sent as an API input limit",
            "Same generation temperature0/top_p1/max_tokens16384/timeout180, tool_choice auto, pipefail and 100-call budget",
            "Identical system/task/ideal QA/image/baseline/dependencies; no proxy/checker/followups/CONTRA or old repair context",
        ],
    )
    common.dump(folder / "manifest.json", manifest)


def run(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel

    manifest = json.loads((folder / "manifest.json").read_text())
    assert common.sha(Path(manifest["protocol_probe"]) / "results.json") == manifest["protocol_probe_sha256"]
    common.run(folder, model_class=partial(NativeBenchmarkModel, request_log_dir=folder / "http", tool_choice="auto"),
               system_template=NATIVE_SYSTEM, stop_on_protocol_fault=True, solver_config=CONFIG,
               api_retries=2, settings_override=OVERRIDES)
    pred = json.loads((folder / "predictions.json").read_text())
    pred["model_name_or_path"] = "mini-qwen38-matched-thinking-native-ideal-no-followup"
    common.dump(folder / "predictions.json", pred)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("probe", "prepare", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path)
    args = parser.parse_args()
    if args.stage == "probe":
        probe.probe(args.output.resolve(), CONFIG, "auto", OVERRIDES)
    elif args.stage == "prepare":
        prepare(args.output.resolve(), args.probe_dir.resolve())
    else:
        run(args.output.resolve())
