"""Host-only official grading after the model has stopped; never model feedback."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from evaluation.analysis.mini_10554_ideal_20261119 import run as common


def grade(folder):
    assert (folder / "result.json").exists()
    assert not (folder / "official_eval").exists(), "Preserve earlier evaluation"
    prediction = json.loads((folder / "predictions.json").read_text())
    assert prediction["model_patch"] == (folder / "model.patch").read_text()
    target = folder / "official_eval"
    target.mkdir()
    ref = folder.parent / "deepseek-10554-ideal-thinking-native-20261119-v2/official_eval"
    for name in ("dataset.json", "grader_provenance.json"):
        shutil.copy2(ref / name, target / name)
    (target / "predictions.jsonl").write_text(json.dumps(prediction) + "\n")
    command = ["/home/rwh/miniconda3/envs/ambig-swe-grader/bin/python", "-B", "-u",
               str(common.PROJECT / "evaluation/analysis/model_swap_10554_diagnosis_20261119/offline_requirements_grader.py"),
               "--dataset_name", "dataset.json", "--predictions_path", "predictions.jsonl",
               "--max_workers", "1", "--timeout", "1200", "--cache_level", "instance",
               "--clean", "False", "--run_id", "mini-" + folder.name]
    if not prediction["model_patch"]:
        reference = folder.parent / "deepseek-10554-ideal-native-20261119-v1/official_eval/empty_patch_single_instance.py"
        script = reference.read_text().replace("mini-deepseek-10554-ideal-native-20261119-v1", "mini-" + folder.name)
        (target / "empty_patch_single_instance.py").write_text(script)
        command = command[:3] + [str(target / "empty_patch_single_instance.py")]
    env = dict(os.environ, SWE_BENCH_BUILD_NETWORK_MODE="host", NO_PROXY="localhost,127.0.0.1,::1",
               no_proxy="localhost,127.0.0.1,::1")
    common.dump(target / "command.json", {"command": command, "started_at": common.now(),
                "model_has_stopped": True, "feedback_to_model": False})
    with (target / "console.log").open("w") as log:
        result = subprocess.run(command, cwd=target, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
    common.dump(target / "completed.json", {"exit_code": result.returncode, "finished_at": common.now()})
    print("GRADE EXIT", result.returncode, folder, flush=True)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    grade(args.output.resolve())
