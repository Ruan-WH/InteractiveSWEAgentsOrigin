"""Align editable imports with the agent workspace in disposable containers.

The container entry point supports Python 3.6 and uses no network or packages.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

MARKER = 'REPAIR_WORKSPACE_ALIGNED='


def align_workspace(workspace, installed, base):
    """Replace an identical baseline installation with a workspace symlink."""
    workspace, installed = Path(workspace).resolve(), Path(installed)
    if installed.is_symlink():
        raise ValueError('Expected a separate installed baseline repository')
    for root in (workspace, installed):
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
        if head != base:
            raise ValueError('Repository is not the expected baseline')
        subprocess.check_call(['git', '-C', str(root), 'diff', '--exit-code', base, '--'],
                              stdout=subprocess.DEVNULL)
    # A symlink covers editable-install paths and already-started Python kernels,
    # which may still hold /testbed in sys.path after changing an egg-link.
    shutil.rmtree(str(installed))
    installed.symlink_to(workspace, target_is_directory=True)
    if not os.path.samefile(str(installed / '.git'), str(workspace / '.git')):
        raise RuntimeError('Installed and edited repositories differ')
    return {'workspace': str(workspace), 'installed_path': str(installed),
            'single_source_tree': True, 'base_commit': base}


def prepare_repair_workspace(runtime, instance, log_path):
    """Align paths before the agent starts and require an audited success marker."""
    import shlex
    from openhands.events.action import CmdRunAction
    runtime.copy_to(str(Path(__file__).resolve()), '/tmp/')
    action = CmdRunAction(command='python /tmp/repair_workspace.py --workspace '
                          + shlex.quote(runtime.swebench_workspace_dir)
                          + ' --base ' + shlex.quote(str(instance.base_commit)))
    action.timeout = 120
    observation = runtime.run_action(action)
    records = [line[len(MARKER):] for line in str(observation.content).splitlines()
               if line.startswith(MARKER)]
    if observation.exit_code != 0 or len(records) != 1:
        raise RuntimeError('Workspace alignment failed: ' + str(observation.content)[-2000:])
    record = json.loads(records[0])
    if not record.get('single_source_tree'):
        raise RuntimeError('Workspace alignment not verified')
    Path(log_path).write_text(json.dumps(record, indent=2) + '\n')
    return record


def repair_guidance(instruction, workspace, workflow, answer_check=None):
    """Add environment facts and optional generic evidence-based repair guidance."""
    if workflow in ('composition-readable-v1', 'retrieved-v1', 'contrastive-v1'):
        from evaluation.utils.public_repair_handoff import readable_handoff
        if answer_check is None:
            raise ValueError('Readable handoff requires the recorded public clarification')
        instruction = readable_handoff(instruction, answer_check)
    instruction = instruction.replace('in the /repo directory', 'in the ' + workspace + ' directory')
    instruction += (
        '\n<repair_environment>\n'
        'The repository to edit is ' + workspace + '. /testbed points to this SAME directory; '
        'Python imports execute your edits. The activated Python environment already contains dependencies. '
        'No network access is available. Do not search for or download upstream fixes. '
        'For Django, run repository tests from the tests directory using '
        '`python runtests.py <test label> --settings=test_sqlite --parallel 1`. '
        'Use existing test labels found in the baseline source; new evaluation tests are not installed. '
        'Keep test failures and exit status visible (use pipefail if piping output).\n'
        '</repair_environment>\n')
    if workflow in ('evidence-v1', 'composition-v1', 'composition-readable-v1', 'retrieved-v1', 'contrastive-v1'):
        instruction += (
            '\n<repair_workflow>\n'
            'Use the issue and all recorded Q/A as evidence. First identify the reported operation '
            'sequence, the expected result, and which setup details are unknown. Test the sequence '
            'with real evaluation, taking queryset caching into account. Record what actually fails '
            'on the baseline instead of assuming the reported diagnosis is correct. '
            'If a plausible reproduction succeeds, do not spend the whole budget repeating variants '
            'of the same hypothesis. After at most two unproductive variants, examine related '
            'operations allowed by the stated requirements and existing tests, looking for a concrete '
            'failure with an explicit expected result. A report can describe a symptom whose '
            'original trigger has already been partly addressed in the baseline. '
            'Use evidence to select a minimal source change; do not merely suppress an error or '
            'discard requested behavior. Verify the change against the failing example, the '
            'reported sequence, and relevant existing tests. Leave time for implementation and '
            'verification; do not repeatedly run broad passing suites or keep expanding setup. '
            'Finish when the source fix is implemented and verified; be explicit about limitations.\n'
            '</repair_workflow>\n')
    if workflow in ('composition-v1', 'composition-readable-v1'):
        instruction = instruction.replace(
            "I've already taken care of all changes to any of the test files described in the <pr_description>. "
            "This means you DON'T have to modify the testing logic or any of the tests in any way!",
            'The repository contains baseline tests; additional grader tests are held out. '
            'Make production changes and use your own scratch checks without changing existing test expectations.')
        instruction += (
            '\n<operation_composition>\n'
            'Use the operations named in the public issue and Q/A to check their interaction, '
            'not only whether they share internal objects. For queryset tasks involving combination, '
            'ordering, and projection, distinguish these requirements: the reported original/derived '
            'evaluation sequence remains stable; clearing ordering affects only the derived query; '
            'projecting the returned columns of an explicitly ordered combined query preserves its '
            'requested ordering and output shape. Check these operation compositions using existing '
            'repository models and actual result values. A passing cleared-ordering reproduction '
            'does not establish that preserving ordering during projection works. Do not assume '
            'object cloning is the required fix, and do not drop ordering to avoid an exception. '
            'Find the concrete failed composition before choosing the implementation.\n'
            '</operation_composition>\n')
    if workflow == 'contrastive-v1':
        instruction = (
            '<execution_priority>\n'
            'Begin with executable behavior checks using the provided public Q/A and existing '
            'baseline test fixtures, before speculating about an internal fix. Preserve the '
            'construction and identity of every named original/component/derived object. '
            'Run the exact reported sequence with real evaluation, avoiding cached-result shortcuts. '
            'When a reported operation clears or disables an existing property, also run a paired '
            'variant that retains that property while holding the original object and the remaining '
            'operations fixed. Check actual returned values, ordering, and output shape in both '
            'branches. Observe which succeeds or fails; do not assume either outcome. '
            'A successful cleared-property branch does not establish that the retained-property '
            'branch works. Use the actual installed test backend without monkeypatching backend '
            'feature flags or inventing unavailable reporter setup. If the historical sequence '
            'already works, keep it as an invariant and investigate the paired behavior. '
            'Do not infer a bug merely because internal objects share identity. Only after these '
            'checks inspect the implementation needed to explain a concrete failed behavior.\n'
            '</execution_priority>\n\n' + instruction)
    return instruction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--base', required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    if not Path('/.dockerenv').exists() or workspace.parent != Path('/workspace'):
        raise ValueError('Only an isolated experiment container is supported')
    record = align_workspace(workspace, '/testbed', args.base)
    code = 'import django, json; print(json.dumps({"module_path": django.__file__}))'
    record['import_probe'] = json.loads(subprocess.check_output(
        ['python', '-c', code], cwd='/tmp').decode())
    module_path = Path(record['import_probe']['module_path']).resolve()
    if workspace not in module_path.parents:
        raise RuntimeError('Python does not import the agent workspace')
    record['python_version'] = subprocess.check_output(['python', '--version'], stderr=subprocess.STDOUT).decode().strip()
    print(MARKER + json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
