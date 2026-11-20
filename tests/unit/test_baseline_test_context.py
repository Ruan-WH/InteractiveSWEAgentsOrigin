"""Public API retrieval must return unchanged existing source only."""
import hashlib

from evaluation.utils.baseline_test_context import collect_context, collect_documentation, attach_context


def test_retrieves_matching_baseline_source_without_generating_files(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    source = 'def test_operations(qs):\n    return qs.union(qs).order_by("rank").values_list("id")\n'
    (tests / 'test_operations.py').write_text(source)
    (tests / 'test_unrelated.py').write_text('def test_unrelated(x):\n    return x.sort()\n')
    public = {'task_summary': 'union ordering', 'exchanges': [
        {'question': 'Which operations?', 'answer': 'qs.order_by().values_list("id")'}]}
    before = sorted(str(p) for p in tmp_path.rglob('*'))
    context = collect_context(tmp_path, public)
    assert [item['path'] for item in context['files']] == ['tests/test_operations.py']
    item = context['files'][0]
    assert item['source'] == source
    assert item['sha256'] == hashlib.sha256(source.encode()).hexdigest()
    assert item['matched_api_names'] == ['order_by', 'union', 'values_list']
    assert sorted(str(p) for p in tmp_path.rglob('*')) == before
    assert not context['new_tests_generated']
    assert source in attach_context('PUBLIC ISSUE AND Q/A', context)


def test_does_not_scan_source_outside_baseline_tests_or_exceed_budget(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'private_reference.py').write_text('def ref(q):\n    return q.union(q).order_by("rank")\n')
    (tmp_path / 'tests/test_large.py').write_text('def large(q):\n    return q.union(q).order_by("rank")\n' + '# padding\n' * 5000)
    public = {'task_summary': 'union order_by', 'exchanges': []}
    assert collect_context(tmp_path, public, max_chars=10)['files'] == []


def test_runtime_allowlist_excludes_untracked_reference_tests(tmp_path):
    (tmp_path / 'tests').mkdir()
    source = 'def test_operations(q):\n    return q.union(q).order_by("rank")\n'
    (tmp_path / 'tests/test_public.py').write_text(source)
    (tmp_path / 'tests/test_untracked_reference.py').write_text(source)
    public = {'task_summary': 'union order_by', 'exchanges': []}
    context = collect_context(tmp_path, public, allowed_paths={'tests/test_public.py'})
    assert [item['path'] for item in context['files']] == ['tests/test_public.py']


def test_doc_retrieval_preserves_literal_baseline_contract_and_excludes_untracked_docs(tmp_path):
    (tmp_path / 'docs').mkdir()
    overview = 'The union API supports order_by and values_list on the resulting query. ' * 2
    contract = 'Any fields used in order_by are included in the SQL SELECT columns. The extra columns do not appear in returned results.'
    source = overview + '\n\n' + '# other material\n\n' * 8 + contract
    (tmp_path / 'docs/api.txt').write_text(source)
    (tmp_path / 'docs/private.txt').write_text(source)
    public = {'task_summary': 'Ordering fields should be included.', 'exchanges': [
        {'question': 'Error?', 'answer': 'DatabaseError: SELECT column unavailable'}]}
    docs = collect_documentation(tmp_path, public, ['order_by', 'union', 'values_list'], {'docs/api.txt'})
    assert docs and all(item['path'] == 'docs/api.txt' for item in docs)
    assert any(contract in item['source'] for item in docs)
    assert all(item['source'] in source for item in docs)
    assert sum(len(item['source']) for item in docs) <= 6000
