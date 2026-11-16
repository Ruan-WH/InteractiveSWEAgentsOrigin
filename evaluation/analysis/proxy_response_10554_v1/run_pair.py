"""Run and record one controlled Ideal/Partial pair, then grade raw patches."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
IID = 'django__django-10554'
GRADER = '/home/rwh/miniconda3/envs/ambig-swe-grader/bin/python'


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stage', choices=('all', 'infer', 'grade'), default='all')
    parser.add_argument('--conditions', nargs='+', choices=('ideal', 'partial'), default=['ideal', 'partial'])
    parser.add_argument('--answer-check-policy', choices=('legacy-v5', 'simple-v1'), default='legacy-v5')
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest['condition_order'] != args.conditions or
                manifest.get('answer_check_policy', 'legacy-v5') != args.answer_check_policy):
            raise ValueError('Existing run has different conditions or checker policy')
    else:
        import tomllib
        settings = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_qwen_coder']
        manifest = {
            'protocol': '10554-ideal-partial-answer-check-v1', 'started_at': now(),
            'instance_id': IID, 'max_solver_iterations': 100, 'max_followups': 2,
            'fixed_initial_question_only': 'Q2; no other initial questions generated',
            'model': settings['model'], 'temperature': settings.get('temperature', 0),
            'top_p': settings.get('top_p', 1), 'condition_order': args.conditions,
            'answer_check_policy': args.answer_check_policy,
            'checker_model': (tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_deepseek_flash']['model']
                              if args.answer_check_policy == 'simple-v1' else settings['model']),
            'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT, text=True).strip(),
            'conditions': {}, 'source_sha256': {}, 'automatic_instance_retries': 0,
        }
        snapshot = root / 'source_snapshot'
        snapshot.mkdir(exist_ok=True)
        for rel in [
            'evaluation/utils/answer_check.py', 'evaluation/utils/controlled_proxy.py',
            'evaluation/utils/simple_clarification.py', 'evaluation/utils/simple_followup.py',
            'evaluation/analysis/proxy_response_10554_v2/run_clarification.py',
            'evaluation/benchmarks/swe_bench/interact_run_infer.py',
            'evaluation/benchmarks/swe_bench/scripts/run_interact_qwen.py',
            'evaluation/analysis/proxy_response_10554_v1/fixture.json',
            'evaluation/analysis/proxy_response_10554_v1/run_pair.py',
        ]:
            path = PROJECT / rel
            manifest['source_sha256'][rel] = sha(path)
            shutil.copy2(path, snapshot / path.name)
        if args.answer_check_policy == 'simple-v1':
            from evaluation.analysis.proxy_response_10554_v2.run_clarification import make_fixture
            dump(snapshot / 'fixture.json', make_fixture())
            manifest['protocol'] = '10554-simple-focused-answer-full-v1'
        manifest['frozen_fixture_sha256'] = sha(snapshot / 'fixture.json')
        (root / 'implementation.diff').write_text(subprocess.check_output(['git', 'diff'], cwd=PROJECT, text=True))
        dump(manifest_path, manifest)
    fixture_path = root / 'source_snapshot/fixture.json'

    if args.stage in ('all', 'infer'):
        for condition in manifest['condition_order']:
            folder = root / condition
            folder.mkdir(exist_ok=True)
            stable_output = folder / 'output.jsonl'
            if stable_output.exists():
                print(f'{condition}: existing inference output preserved; no rerun', flush=True)
                continue
            if manifest['conditions'].get(condition, {}).get('inference_started_at'):
                print(f'{condition}: previous attempt has no final output; preserving it for diagnosis, not rerolling', flush=True)
                continue
            entry = manifest['conditions'].setdefault(condition, {})
            cmd = [sys.executable, '-B', str(PROJECT / 'evaluation/benchmarks/swe_bench/scripts/run_interact_qwen.py'),
                   '--instance-ids', IID, '--prompt-variant', 'answer-check-v1', '--max-followups', '2',
                   '--response-fixture', str(fixture_path), '--response-condition', condition,
                   '--answer-check-policy', args.answer_check_policy,
                   '--eval-output-dir', str(folder / 'inference'), '--eval-note', 'controlled-10554-v1']
            entry.update({'inference_started_at': now(), 'inference_command': cmd, 'status': 'inference_running'})
            manifest['running_condition'] = condition
            dump(manifest_path, manifest)
            print(f'{condition}: inference started at {now()}', flush=True)
            with (folder / 'inference.console.log').open('w') as log:
                outcome = subprocess.run(cmd, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, timeout=7500)
            entry.update({'inference_finished_at': now(), 'inference_exit_code': outcome.returncode})
            outputs = list((folder / 'inference').rglob('output.jsonl'))
            valid = [p for p in outputs if p.stat().st_size]
            if len(valid) == 1:
                shutil.copy2(valid[0], stable_output)
                rows = [json.loads(line) for line in stable_output.read_text().splitlines() if line.strip()]
                if len(rows) != 1 or rows[0]['instance_id'] != IID:
                    raise ValueError('Unexpected controlled inference output')
                row = rows[0]
                entry['raw_output_path'] = str(valid[0])
                entry['output_sha256'] = sha(stable_output)
                entry['solver_error'] = row.get('error')
                entry['status'] = 'inference_completed'
                check = row.get('test_result', {}).get('answer_check')
                dump(folder / 'answer_check.json', check)
                dump(folder / 'interactions.json', row.get('test_result', {}).get('interactions', []))
                dump(folder / 'solver_metrics.json', row.get('metrics'))
                (folder / 'solver_instruction.txt').write_text(row.get('instruction') or '')
                patch = row.get('test_result', {}).get('git_patch')
                if patch is not None:
                    (folder / 'model.patch').write_text(patch)
                    entry['patch_sha256'] = sha(folder / 'model.patch')
            else:
                entry['status'] = 'inference_failed_no_output'
            dump(manifest_path, manifest)
            print(f'{condition}: {entry["status"]} at {now()}', flush=True)

    if args.stage in ('all', 'grade'):
        dataset_path = PROJECT / 'evaluation/evaluation_outputs/qwen-new7-grounded-v1-20261101/official_eval/dataset.verified.json'
        data = json.loads(dataset_path.read_text())
        subset = [row for row in data if row['instance_id'] == IID]
        if len(subset) != 1:
            raise ValueError('Expected one official Verified grading record')
        fixture = json.loads(fixture_path.read_text())
        if subset[0]['base_commit'] != fixture['base_commit']:
            raise ValueError('Official grading base differs from fixture')
        for condition in manifest['condition_order']:
            folder = root / condition
            entry = manifest['conditions'].get(condition, {})
            if not (folder / 'model.patch').exists():
                continue
            grade = folder / 'official_eval'
            grade.mkdir(exist_ok=True)
            model_name = 'qwen3-coder-flash-' + condition
            run_id = root.name + '-' + condition
            report = grade / 'logs/run_evaluation' / run_id / model_name / IID / 'report.json'
            if report.exists():
                print(f'{condition}: existing official report preserved', flush=True)
                continue
            dump(grade / 'dataset.json', subset)
            dump(grade / 'prediction.json', [{'instance_id': IID, 'model_name_or_path': model_name,
                                             'model_patch': (folder / 'model.patch').read_text()}])
            # The harness expects JSONL predictions.
            (grade / 'predictions.jsonl').write_text(json.dumps({
                'instance_id': IID, 'model_name_or_path': model_name,
                'model_patch': (folder / 'model.patch').read_text(),
            }) + '\n')
            cmd = [GRADER, '-B', '-u', '-m', 'swebench.harness.run_evaluation',
                   '--dataset_name', str(grade / 'dataset.json'),
                   '--predictions_path', str(grade / 'predictions.jsonl'),
                   '--max_workers', '1', '--timeout', '1200', '--cache_level', 'instance',
                   '--clean', 'False', '--run_id', run_id]
            entry.update({'grading_started_at': now(), 'grading_command': cmd, 'status': 'grading_running'})
            dump(manifest_path, manifest)
            print(f'{condition}: official grading started at {now()}', flush=True)
            env = dict(os.environ, SWE_BENCH_BUILD_NETWORK_MODE='host', NO_PROXY='localhost,127.0.0.1,::1',
                       no_proxy='localhost,127.0.0.1,::1')
            with (grade / 'grader.console.log').open('a') as log:
                outcome = subprocess.run(cmd, cwd=grade, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
            entry.update({'grading_finished_at': now(), 'grading_exit_code': outcome.returncode,
                          'official_report': str(report),
                          'status': 'completed' if report.exists() else 'grading_failed_no_report'})
            dump(manifest_path, manifest)
            print(f'{condition}: {entry["status"]} at {now()}', flush=True)
    manifest['running_condition'] = None
    manifest['last_finished_at'] = now()
    dump(manifest_path, manifest)
    print('Pair stages finished. Records: ' + str(root), flush=True)


if __name__ == '__main__':
    main()
