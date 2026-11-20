"""Keep baseline source and Git diff support, without reference repair history.

The container entry point uses only the standard library and supports Python 3.6.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

MARKER = 'REFERENCE_HISTORY_CLEANED='
PUBLIC_INSTANCE_KEYS = ('repo', 'instance_id', 'base_commit', 'version')


def git(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root)] + list(arguments), stderr=subprocess.PIPE)


def clean_repository(root, base):
    """Replace Git storage with just the exact baseline commit and its tree."""
    root = Path(root).resolve()
    git_dir = Path(git(root, 'rev-parse', '--absolute-git-dir').decode().strip())
    if git_dir != root / '.git' or not git_dir.is_dir() or git_dir.is_symlink():
        raise ValueError('Only a standalone .git directory is supported')
    if git(root, 'rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('Repository HEAD is not the expected baseline')
    git(root, 'diff', '--exit-code', base, '--')
    commit = git(root, 'cat-file', 'commit', base)
    tree = git(root, 'rev-parse', base + '^{tree}').decode().strip()
    before = int(git(root, 'rev-list', '--all', '--count'))
    with tempfile.TemporaryDirectory(prefix='baseline-git-') as directory:
        temporary = Path(directory)
        new = temporary / 'new.git'
        subprocess.check_call(['git', 'init', '--bare', str(new)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with (temporary / 'tree.pack').open('w+b') as pack:
            subprocess.run(['git', '-C', str(root), 'pack-objects', '--stdout', '--revs'],
                           input=(tree + '\n').encode(), stdout=pack, stderr=subprocess.PIPE, check=True)
            pack.seek(0)
            subprocess.run(['git', '--git-dir=' + str(new), 'index-pack', '--stdin'],
                           stdin=pack, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        result = subprocess.run(['git', '--git-dir=' + str(new), 'hash-object', '-w', '-t', 'commit', '--stdin'],
                                input=commit, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        if result.stdout.decode().strip() != base:
            raise ValueError('Baseline commit identity changed')
        (new / 'shallow').write_text(base + '\n')
        for args in [('config', 'core.bare', 'false'), ('config', 'core.worktree', str(root)),
                     ('config', 'protocol.allow', 'never'), ('config', 'user.name', 'Repair Agent'),
                     ('config', 'user.email', 'repair@localhost'),
                     ('symbolic-ref', 'HEAD', 'refs/heads/baseline'),
                     ('update-ref', 'refs/heads/baseline', base), ('read-tree', base)]:
            subprocess.check_call(['git', '--git-dir=' + str(new)] + list(args),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # These repositories live in disposable experiment containers. Never retain
        # an old object-store backup that the repair agent could inspect.
        shutil.rmtree(str(git_dir))
        shutil.move(str(new), str(git_dir))
        git(root, 'diff', '--exit-code', base, '--')
        objects = git(root, 'cat-file', '--batch-all-objects', '--batch-check=%(objecttype) %(objectname)').decode().splitlines()
        commits = [line.split()[1] for line in objects if line.startswith('commit ')]
        if commits != [base] or any(line.startswith('tag ') for line in objects):
            raise ValueError('Non-baseline history objects remain')
        if git(root, 'remote').strip() or git(root, 'rev-list', '--all').decode().splitlines() != [base]:
            raise ValueError('Unexpected remote or reachable history')
    return {'path': str(root), 'base_commit': base, 'tree': tree, 'prior_reachable_commits': before,
            'remaining_commits': commits, 'remaining_objects': len(objects), 'source_unchanged': True,
            'remotes': [], 'git_diff_available': True}


def clean_instance_record(path):
    """Leave only setup identifiers in the runtime copy of the instance record."""
    path = Path(path)
    if not path.is_file():
        raise ValueError('Missing runtime instance record')
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('Expected exactly one runtime instance')
    removed = sorted(set(rows[0]) - set(PUBLIC_INSTANCE_KEYS))
    rows = [{key: row[key] for key in PUBLIC_INSTANCE_KEYS} for row in rows]
    path.write_text(json.dumps(rows) + '\n')
    return {'path': str(path), 'remaining_keys': sorted(rows[0]), 'removed_keys': removed}


def clean_reference_history(runtime, instance, log_path):
    """Run cleanup before CodeAct; reject unverified or failed cleanup."""
    import shlex
    from openhands.events.action import CmdRunAction
    workspace = runtime.swebench_workspace_dir
    script = Path(__file__).resolve()
    runtime.copy_to(str(script), '/tmp/')
    action = CmdRunAction(command='unset item; python /tmp/reference_history.py --workspace ' + shlex.quote(workspace)
                          + ' --base ' + shlex.quote(str(instance.base_commit)))
    action.timeout = 300
    observation = runtime.run_action(action)
    lines = str(observation.content).splitlines()
    records = [line[len(MARKER):] for line in lines if line.startswith(MARKER)]
    if observation.exit_code != 0 or len(records) != 1:
        raise RuntimeError('Reference cleanup failed or missing verified output: ' + str(observation.content)[-2000:])
    record = json.loads(records[0])
    if not record['verified']:
        raise RuntimeError('Reference cleanup did not verify')
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--base', required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    if not Path('/.dockerenv').exists() or workspace.parent != Path('/workspace'):
        raise ValueError('Entry point only cleans an isolated experiment container')
    roots = [workspace, Path('/testbed')]
    result = {'protocol': 'baseline-only-git-v1',
              'repositories': [clean_repository(root, args.base) for root in roots],
              'runtime_instance': clean_instance_record('/swe_util/eval_data/instances/swe-bench-instance.json'),
              'verified': True}
    cache = Path.home() / '.cache/pip'
    if cache.is_dir():
        shutil.rmtree(str(cache))
    result['pip_cache_absent'] = not cache.exists()
    print(MARKER + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
