"""Check that repair execution and edits share one baseline repository."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evaluation.utils.repair_workspace import align_workspace, prepare_repair_workspace, repair_guidance


def test_installed_path_sees_edits_and_baseline_diff(tmp_path):
    workspace = tmp_path / 'workspace'
    installed = tmp_path / 'testbed'
    workspace.mkdir()
    subprocess.run(['git', 'init', str(workspace)], check=True, capture_output=True)
    (workspace / 'source.py').write_text('value = 1\n')
    subprocess.run(['git', '-C', str(workspace), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                    'commit', '-m', 'baseline'], check=True, capture_output=True)
    base = subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', 'HEAD']).decode().strip()
    subprocess.run(['git', 'clone', str(workspace), str(installed)], check=True, capture_output=True)
    record = align_workspace(workspace, installed, base)
    assert record['single_source_tree']
    (workspace / 'source.py').write_text('value = 2\n')
    assert (installed / 'source.py').read_text() == 'value = 2\n'
    diff = subprocess.check_output(['git', '-C', str(installed), 'diff', base]).decode()
    assert '+value = 2' in diff


def test_wrong_base_is_rejected_without_deleting_installation(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    subprocess.run(['git', 'init', str(workspace)], check=True, capture_output=True)
    with pytest.raises(subprocess.CalledProcessError):
        align_workspace(workspace, tmp_path / 'installed', 'not-a-baseline')


def test_missing_alignment_marker_rejected(tmp_path):
    runtime = SimpleNamespace(swebench_workspace_dir='/workspace/repo', copy_to=Mock(),
                              run_action=Mock(return_value=SimpleNamespace(exit_code=0, content='')))
    with pytest.raises(RuntimeError, match='alignment failed'):
        prepare_repair_workspace(runtime, SimpleNamespace(base_commit='base'), tmp_path / 'audit.json')


def test_guidance_preserves_public_answers_and_has_no_issue_specific_fix():
    public = 'in the /repo directory\n<qa>public question and answer</qa>'
    environment = repair_guidance(public, '/workspace/repo', 'environment-v1')
    evidence = repair_guidance(public, '/workspace/repo', 'evidence-v1')
    assert '<qa>public question and answer</qa>' in environment
    assert '<qa>public question and answer</qa>' in evidence
    assert 'in the /repo directory' not in environment
    assert '<repair_workflow>' not in environment
    assert '<repair_workflow>' in evidence
    assert 'add_select_col' not in evidence and 'compiler.py' not in evidence
    composition = repair_guidance(public, '/workspace/repo', 'composition-v1')
    assert '<qa>public question and answer</qa>' in composition
    assert '<operation_composition>' in composition
    assert 'add_select_col' not in composition and 'compiler.py' not in composition
