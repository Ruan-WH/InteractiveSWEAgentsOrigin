"""Three-arm model-swap development experiment with unchanged clarification modules."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ROOT = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-diagnosis-20261119'
IID = 'django__django-10554'
ARMS = {}
SOURCES = ['evaluation/utils/' + name + '.py' for name in
           ('simple_followup', 'simple_clarification', 'answer_check', 'mandatory_clarification', 'controlled_proxy')]
SOURCES += ['evaluation/utils/reference_history.py',
            'evaluation/analysis/model_swap_10554_offline_20261119/network_preflight.py',
            'evaluation/benchmarks/swe_bench/interact_run_infer.py',
            'evaluation/benchmarks/swe_bench/scripts/run_interact_qwen.py',
            str(Path(__file__).relative_to(PROJECT))]


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('all', 'prepare', 'infer', 'grade'), default='all')
    parser.add_argument('--name', required=True)
    parser.add_argument('--workflow', choices=('environment-v1', 'evidence-v1', 'composition-v1'), required=True)
    parser.add_argument('--solver-config', default='hidden_qwen38_flash')
    args = parser.parse_args()
    global ROOT, ARMS
    ROOT = ROOT / args.name
    ARMS = {'partial_followup': ('partial', 'adaptive')}
    SOURCES.extend(['evaluation/utils/repair_workspace.py', 'evaluation/utils/interaction_runtime.py', 'openhands/llm/llm.py', 'openhands/llm/qwen_compatible.py', 'openhands/core/message.py', 'openhands/agenthub/codeact_agent/codeact_agent.py'])
    import docker
    from evaluation.analysis.model_swap_10554_offline_20261119 import network_preflight
    network_preflight.NETWORK = 'ambig-swe-diagnosis-composition-20261119'
    network, address, runtime_kwargs = network_preflight.runtime_network_config(docker.from_env())
    os.environ['DOCKER_RUNTIME_KWARGS'] = json.dumps(runtime_kwargs)
    os.environ['EVAL_LOCAL_RUNTIME_URL'] = 'http://' + address
    os.environ['EVAL_INTERNAL_RUNTIME_IP'] = address
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / 'manifest.json'
    fixture_path = ROOT / 'frozen/fixture.json'
    if not manifest_path.exists():
        dump(fixture_path, json.loads((PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-20261119/frozen/fixture.json').read_text()))
        cfg = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']
        safe = {k: cfg[args.solver_config][k] for k in
                ('model', 'temperature', 'top_p', 'max_input_tokens', 'max_output_tokens', 'num_retries', 'extra_body')}
        manifest = {'protocol': '10554-repair-diagnosis-offline-v2', 'exit_cleanup': 'owned-container-only', 'workflow': args.workflow, 'diagnostic_name': args.name, 'started_at': now(),
            'instance_id': IID, 'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT, text=True).strip(),
            'solver_config': safe, 'proxy_model': cfg['hidden_qwen_coder']['model'],
            'checker_model': cfg['hidden_deepseek_flash']['model'],
            'max_solver_iterations': 100, 'max_followups': 2,
            'handoff': 'Unchanged checked_solving_instruction: public issue summary + literal Q/A transcript + uncertainty. No generated test component.',
            'development_guidance': 'Operation-composition prompt informed by prior failure trajectories and independent public-API probes; not an automatic generalization result. No reference patch or hidden test supplied.',
            'scope': 'Single development case; gold previously inspected by researcher but never supplied to solver; diagnostic prompt adjustments informed by past trajectories, not held-out evaluation.',
            'history_policy': 'baseline-only Git objects in workspace and testbed; runtime instance metadata stripped',
            'previous_run_preserved': 'model-swap-10554-20261119',
            'interrupted_diagnostic': 'model-swap-10554-clean-20261119',
            'runtime_network': {'name': network.name, 'internal': network.attrs['Internal'], 'address': address},
            'whole_instance_retries': 0, 'arms': {}, 'source_sha256': {p: sha(PROJECT / p) for p in SOURCES},
            'fixture_sha256': sha(fixture_path)}
        for source in SOURCES:
            target = ROOT / 'frozen/source' / source
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT / source, target)
        (ROOT / 'implementation.diff').write_text(subprocess.check_output(['git', 'diff'], cwd=PROJECT, text=True))
        dump(manifest_path, manifest)
    manifest = json.loads(manifest_path.read_text())
    assert sha(fixture_path) == manifest['fixture_sha256']
    assert all(sha(PROJECT / p) == h for p, h in manifest['source_sha256'].items()), 'Sources changed after freeze'
    if args.stage in ('all', 'infer'):
        for name, (condition, mode) in ARMS.items():
            folder = ROOT / name
            folder.mkdir(exist_ok=True)
            entry = manifest['arms'].setdefault(name, {})
            output = folder / 'output.jsonl'
            if output.exists() or entry.get('inference_started_at'):
                print(name + ': preserve previous inference attempt', flush=True)
                continue
            cmd = [sys.executable, '-B', str(PROJECT / 'evaluation/benchmarks/swe_bench/scripts/run_interact_qwen.py'),
                '--instance-ids', IID, '--agent-llm-config', args.solver_config,
                '--proxy-llm-config', 'hidden_qwen_coder', '--checker-llm-config', 'hidden_deepseek_flash',
                '--prompt-variant', 'answer-check-v1', '--answer-check-policy', 'simple-v1',
                '--max-followups', '2', '--response-fixture', str(fixture_path), '--response-condition', condition,
                '--clarification-mode', mode, '--clean-reference-history', '--repair-workflow', args.workflow, '--eval-output-dir', str(folder / 'inference'),
                '--eval-note', 'qwen38-diagnosis-' + args.name]
            entry.update(inference_started_at=now(), command=cmd, response_condition=condition, clarification_mode=mode)
            dump(manifest_path, manifest)
            print(name + ': inference started ' + now(), flush=True)
            try:
                with (folder / 'inference.console.log').open('w') as log:
                    process = subprocess.run(cmd, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, timeout=7500)
                entry['inference_exit'] = process.returncode
            except subprocess.TimeoutExpired:
                entry['inference_timeout'] = True
            outputs = [p for p in (folder / 'inference').rglob('output.jsonl') if p.stat().st_size]
            if len(outputs) == 1:
                shutil.copy2(outputs[0], output)
                row = json.loads(output.read_text())
                assert row['instance_id'] == IID
                entry.update(raw_output=str(outputs[0]), solver_error=row.get('error'), output_sha256=sha(output))
                for key in ('answer_check', 'interactions', 'reference_history', 'repair_workspace'):
                    dump(folder / (key + '.json'), row['test_result'].get(key))
                (folder / 'solver_instruction.txt').write_text(row.get('instruction') or '')
                patch = row['test_result'].get('git_patch')
                if patch is not None:
                    (folder / 'model.patch').write_text(patch)
            else:
                entry['no_final_output'] = True
            entry['inference_finished_at'] = now()
            dump(manifest_path, manifest)
            print(name + ': inference finished ' + now(), flush=True)
    if args.stage in ('all', 'grade'):
        rows = json.loads((PROJECT / 'evaluation/evaluation_outputs/qwen-new7-grounded-v1-20261101/official_eval/dataset.verified.json').read_text())
        subset = [r for r in rows if r['instance_id'] == IID]
        assert len(subset) == 1 and subset[0]['base_commit'] == json.loads(fixture_path.read_text())['base_commit']
        for name in ARMS:
            folder = ROOT / name
            if not (folder / 'model.patch').exists():
                continue
            grade = folder / 'official_eval'
            grade.mkdir(exist_ok=True)
            run_id = 'model-swap-10554-diagnosis-20261119-' + args.name + '-' + name
            model_name = 'qwen3.8-flash-' + name
            report_path = grade / 'logs/run_evaluation' / run_id / model_name / IID / 'report.json'
            if report_path.exists():
                continue
            dump(grade / 'dataset.json', subset)
            prediction = {'instance_id': IID, 'model_name_or_path': model_name,
                          'model_patch': (folder / 'model.patch').read_text()}
            (grade / 'predictions.jsonl').write_text(json.dumps(prediction) + '\n')
            cmd = ['/home/rwh/miniconda3/envs/ambig-swe-grader/bin/python', '-B', '-u', '-m', 'swebench.harness.run_evaluation',
                '--dataset_name', str(grade / 'dataset.json'), '--predictions_path', str(grade / 'predictions.jsonl'),
                '--max_workers', '1', '--timeout', '1200', '--cache_level', 'instance', '--clean', 'False', '--run_id', run_id]
            print(name + ': official grading started', flush=True)
            env = dict(os.environ, SWE_BENCH_BUILD_NETWORK_MODE='host', NO_PROXY='localhost,127.0.0.1,::1', no_proxy='localhost,127.0.0.1,::1')
            with (grade / 'console.log').open('w') as log:
                process = subprocess.run(cmd, cwd=grade, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
            manifest['arms'][name].update(grading_exit=process.returncode, report_path=str(report_path), grading_finished_at=now())
            dump(manifest_path, manifest)
            print(name + ': official grading finished', flush=True)
    dump(HERE / (args.name + '.manifest.json'), manifest)
    print('Recorded at ' + str(ROOT), flush=True)


if __name__ == '__main__':
    main()
