"""Independent 10554 rerun: only the model/tool protocol changes."""

import argparse
from functools import partial
import json
from pathlib import Path

from evaluation.analysis.mini_10554_ideal_20261119 import run as common


def prepare(folder, probe_dir):
    probe = json.loads((probe_dir / "results.json").read_text())
    assert probe["passed"] and len(probe["completed"]) == 3
    common.prepare(
        folder, extra_sources=(Path(__file__), Path(__file__).with_name("probe.py"))
    )
    previous = (
        common.PROJECT
        / "evaluation/evaluation_outputs/mini_swe/qwen38-10554-ideal-no-followup-20261119-v1"
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
        previous_text_experiment=str(previous),
        previous_classification="protocol-fault-interrupted; official unresolved; not a complete ability comparison",
        protocol_probe=str(probe_dir),
        protocol_probe_sha256=common.sha(probe_dir / "results.json"),
        change_from_text_run="Native tool schema, required single bash call, tool role/call-ID observations, protocol-only system instruction replacement; task input unchanged.",
    )
    common.dump(folder / "manifest.json", manifest)
    common.dump(
        folder / "input_verification.json",
        {
            "issue_exact_match": True,
            "ideal_question_exact_match": True,
            "ideal_answer_exact_match": True,
            "task_exact_match": True,
            "dependencies_exact_match": True,
            "image_id_exact_match": True,
            "new_trajectory": True,
            "extra_task_evidence": False,
        },
    )


def run(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel

    manifest = json.loads((folder / "manifest.json").read_text())
    probe_dir = Path(manifest["protocol_probe"])
    assert common.sha(probe_dir / "results.json") == manifest["protocol_probe_sha256"]
    common.run(
        folder,
        model_class=partial(NativeBenchmarkModel, request_log_dir=folder / "http"),
        system_template=NATIVE_SYSTEM,
        stop_on_protocol_fault=True,
    )


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
