"""DeepSeek-only independent 10554 ideal/no-followup native run."""

import argparse
from functools import partial
import json
from pathlib import Path

from evaluation.analysis.mini_10554_ideal_20261119 import run as common

CONFIG = "interaction_deepseek_flash"


def prepare(folder, probe_dir):
    assert json.loads((probe_dir / "results.json").read_text())["passed"]
    common.prepare(
        folder,
        extra_sources=(Path(__file__), Path(__file__).with_name("probe.py")),
        solver_config=CONFIG,
        compare_old_model=False,
    )
    previous = (
        common.PROJECT
        / "evaluation/evaluation_outputs/mini_swe/qwen38-10554-ideal-native-20261119-v1"
    )
    for name in (
        "issue.txt",
        "ideal_question.txt",
        "ideal_answer.txt",
        "task.txt",
        "dependencies.txt",
    ):
        assert (folder / name).read_bytes() == (previous / name).read_bytes()
    manifest = json.loads((folder / "manifest.json").read_text())
    assert (
        manifest["image_id"]
        == json.loads((previous / "manifest.json").read_text())["image_id"]
    )
    manifest.update(
        protocol="native-structured-bash-v1",
        protocol_probe=str(probe_dir),
        protocol_probe_sha256=common.sha(probe_dir / "results.json"),
        comparison_run=str(previous),
        change="DeepSeek model/endpoint with existing native configuration; thinking disabled, sampling and output limit matched to Qwen native run; identical task, system and environment.",
    )
    common.dump(folder / "manifest.json", manifest)
    common.dump(
        folder / "input_verification.json",
        {
            "issue_and_ideal_QA_exact_match": True,
            "task_exact_match": True,
            "dependencies_exact_match": True,
            "image_id_exact_match": True,
            "new_trajectory": True,
            "proxy_calls": 0,
            "checker_calls": 0,
            "followups": 0,
        },
    )


def run(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel

    manifest = json.loads((folder / "manifest.json").read_text())
    assert (
        common.sha(Path(manifest["protocol_probe"]) / "results.json")
        == manifest["protocol_probe_sha256"]
    )
    common.run(
        folder,
        model_class=partial(NativeBenchmarkModel, request_log_dir=folder / "http"),
        system_template=NATIVE_SYSTEM,
        stop_on_protocol_fault=True,
        solver_config=CONFIG,
    )
    prediction = json.loads((folder / "predictions.json").read_text())
    prediction["model_name_or_path"] = "mini-deepseek-native-ideal-no-followup"
    common.dump(folder / "predictions.json", prediction)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        assert args.probe_dir is not None
        prepare(args.output.resolve(), args.probe_dir.resolve())
    else:
        run(args.output.resolve())
