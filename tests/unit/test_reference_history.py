"""Check reference removal and baseline preservation in disposable repositories."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evaluation.utils.reference_history import clean_repository, clean_instance_record, clean_reference_history


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()


def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init')
    git(root, 'config', 'user.name', 'Offline')
    git(root, 'config', 'user.email', 'offline@localhost')
    (root / 'source.py').write_text('def value():\n    return 1\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'baseline')
    base = git(root, 'rev-parse', 'HEAD')
    (root / 'source.py').write_text('def value():\n    return 2\n')
    (root / 'reference_test.py').write_text('assert value() == 2\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'future repair')
    future = git(root, 'rev-parse', 'HEAD')
    git(root, 'tag', '-a', 'repair', '-m', 'reference')
    git(root, 'remote', 'add', 'origin', 'https://example.invalid/reference.git')
    git(root, 'reset', '--hard', base)
    return root, base, future


def test_removes_all_history_objects_but_preserves_base_and_diff(tmp_path):
    root, base, future = repository(tmp_path)
    source = (root / 'source.py').read_bytes()
    audit = clean_repository(root, base)
    assert audit['prior_reachable_commits'] == 2
    assert audit['remaining_commits'] == [base]
    assert git(root, 'rev-parse', 'HEAD') == base
    assert (root / 'source.py').read_bytes() == source
    assert git(root, 'rev-list', '--all') == base
    assert not git(root, 'remote')
    assert subprocess.run(['git', '-C', str(root), 'cat-file', '-e', future], capture_output=True).returncode != 0
    assert not (root / 'reference_test.py').exists()
    assert not (root / '.git/objects/info/alternates').exists()
    (root / 'source.py').write_text('def value():\n    return 3\n')
    assert '+    return 3' in git(root, 'diff', base)


def test_dirty_source_rejected_before_removing_git(tmp_path):
    root, base, future = repository(tmp_path)
    (root / 'source.py').write_text('dirty\n')
    with pytest.raises(subprocess.CalledProcessError):
        clean_repository(root, base)
    assert git(root, 'cat-file', '-t', future) == 'commit'


def test_runtime_metadata_retains_only_setup_ids(tmp_path):
    path = tmp_path / 'instance.json'
    row = dict(repo='org/repo', instance_id='case', base_commit='base', version='1',
               patch='private repair', test_patch='private test', original_issue='private source', hints_text='hint')
    path.write_text(json.dumps([row]))
    audit = clean_instance_record(path)
    assert json.loads(path.read_text()) == [{k: row[k] for k in ('repo', 'instance_id', 'base_commit', 'version')}]
    assert 'patch' in audit['removed_keys'] and 'test_patch' in audit['removed_keys']


def test_missing_verification_marker_is_not_cleanup_success(tmp_path):
    runtime = SimpleNamespace(swebench_workspace_dir='/workspace/repo', copy_to=Mock(),
                              run_action=Mock(return_value=SimpleNamespace(exit_code=0, content='')))
    with pytest.raises(RuntimeError, match='missing verified output'):
        clean_reference_history(runtime, SimpleNamespace(base_commit='base'), tmp_path / 'audit.json')
    assert not (tmp_path / 'audit.json').exists()
