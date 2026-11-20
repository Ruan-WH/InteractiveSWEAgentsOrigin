"""Validate baseline/gold only in separate grader containers; never expose controls to agents."""
import argparse
import json
import os
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[3]
ROOT = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-diagnosis-20261119/grader_controls'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--name', default='focused-model')
    parser.add_argument('--offline-requirements', action='store_true')
    args = parser.parse_args()
    rows = json.loads((PROJECT / 'evaluation/evaluation_outputs/qwen-new7-grounded-v1-20261101/official_eval/dataset.verified.json').read_text())
    row, = [r for r in rows if r['instance_id'] == 'django__django-10554']
    patches = {'gold': row['patch'], 'baseline': 'diff --git a/evaluation_baseline_marker.txt b/evaluation_baseline_marker.txt\nnew file mode 100644\nindex 0000000..b5d9b65\n--- /dev/null\n+++ b/evaluation_baseline_marker.txt\n@@ -0,0 +1 @@\n+Baseline control: no production code changed.\n'}
    if args.candidate:
        patches = {args.name: args.candidate.read_text()}
    for name, patch in patches.items():
        folder = ROOT / name
        folder.mkdir(parents=True, exist_ok=True)
        run_id = '10554-diagnosis-controls-20261119-' + name
        report = folder / 'logs/run_evaluation' / run_id / name / row['instance_id'] / 'report.json'
        if report.exists():
            print(name, report.read_text(), flush=True)
            continue
        (folder / 'dataset.json').write_text(json.dumps([row]))
        (folder / 'predictions.jsonl').write_text(json.dumps({'instance_id': row['instance_id'], 'model_name_or_path': name, 'model_patch': patch})+'\n')
        cmd = ['/home/rwh/miniconda3/envs/ambig-swe-grader/bin/python', '-B', '-u', '-m', 'swebench.harness.run_evaluation',
               '--dataset_name', str(folder / 'dataset.json'), '--predictions_path', str(folder / 'predictions.jsonl'),
               '--max_workers', '1', '--timeout', '1200', '--cache_level', 'instance', '--clean', 'False', '--run_id', run_id]
        if args.offline_requirements:
            wrapper = Path(__file__).resolve().with_name('offline_requirements_grader.py')
            cmd[3:5] = [str(wrapper)]
        env = dict(os.environ, SWE_BENCH_BUILD_NETWORK_MODE='host', NO_PROXY='localhost,127.0.0.1,::1', no_proxy='localhost,127.0.0.1,::1')
        with (folder / 'console.log').open('w') as log:
            result = subprocess.run(cmd, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
        print(name, result.returncode, report.read_text() if report.exists() else 'missing report', flush=True)


if __name__ == '__main__':
    main()
