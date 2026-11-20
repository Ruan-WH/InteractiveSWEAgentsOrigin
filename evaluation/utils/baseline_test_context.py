"""Retrieve existing baseline tests using only words in public clarification.

The runtime entry point uses the standard library and supports Python 3.6.
It neither generates tests nor reads reference metadata.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

MARKER = 'BASELINE_TEST_CONTEXT='


def collect_context(root, public, max_chars=22000, allowed_paths=None):
    """Rank baseline test files by API calls also named in the public text."""
    root = Path(root)
    text = public['task_summary'] + '\n' + '\n'.join(
        exchange['question'] + '\n' + exchange['answer'] for exchange in public['exchanges'])
    words = set(re.findall(r'\b[A-Za-z_]\w*\b', text))
    words -= {'get', 'set', 'append', 'format', 'split', 'strip', 'join'}
    candidates = []
    for path in (root / 'tests').rglob('test*.py'):
        relative = str(path.relative_to(root))
        if allowed_paths is not None and relative not in allowed_paths:
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 40000:
            continue
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
        except (UnicodeError, SyntaxError):
            continue
        calls = Counter(node.func.attr for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in words)
        if len(calls) < 2:
            continue
        score = 100 * len(calls) + sum(calls.values())
        candidates.append({'path': relative, 'matched_api_names': sorted(calls),
                           'score': score, 'source': source,
                           'sha256': hashlib.sha256(source.encode()).hexdigest()})
    candidates.sort(key=lambda item: (-item['score'], item['path']))
    selected = []
    for item in candidates:
        if len(item['source']) <= max_chars:
            selected.append(item)
            max_chars -= len(item['source'])
        if len(selected) >= 2:
            break
    return {'scope': 'existing-baseline-tests-only', 'files': selected,
            'candidate_files': len(candidates), 'new_tests_generated': False}


def collect_documentation(root, public, api_names, allowed_paths, max_chars=6000):
    """Retrieve API-interaction and error-topic passages from tracked baseline docs."""
    root = Path(root)
    text = public['task_summary'] + '\n' + '\n'.join(e['answer'] for e in public['exchanges'])
    public_words = set(re.findall(r'\b[A-Za-z_]\w*\b', text.lower()))
    errors = re.findall(r'(?:Error|Exception):\s*([^\n]+)', text)
    topics = set(re.findall(r'\b[A-Za-z_]\w*\b', ' '.join(errors).lower()))
    topics -= {'order', 'by', 'is', 'not', 'in', 'the', 'a', 'an', 'of', 'on', 'to', 'at', 'list', 'does', 'match', 'any'}
    stems = {name.rsplit('_', 1)[0] for name in api_names if '_' in name}
    candidates = []
    for relative in sorted(allowed_paths):
        path = root / relative
        if (not relative.startswith('docs/') or path.suffix not in ('.txt', '.rst', '.md')
                or path.is_symlink() or not path.is_file() or path.stat().st_size > 1000000):
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except UnicodeError:
            continue
        if not any(name in source for name in api_names):
            continue
        bounds = []
        start = 0
        for separator in re.finditer(r'\n[ \t]*\n', source):
            if source[start:separator.start()].strip():
                bounds.append((start, separator.start()))
            start = separator.end()
        if source[start:].strip():
            bounds.append((start, len(source)))
        for index, (start, end) in enumerate(bounds):
            if index + 1 < len(bounds) and bounds[index + 1][1] - start <= 1800:
                end = bounds[index + 1][1]
            snippet = source[start:end]
            if not 80 <= len(snippet) <= 1800:
                continue
            words = set(re.findall(r'\b[A-Za-z_]\w*\b', snippet.lower()))
            matched = set(api_names) & words
            if not matched:
                continue
            related = stems & words
            hits = topics & words
            overlap = len(public_words & words)
            candidates.append({'path': relative, 'start_line': source[:start].count('\n') + 1,
                'end_line': source[:end].count('\n') + 1, 'matched_api_names': sorted(matched),
                'matched_error_terms': sorted(hits), 'source': snippet,
                'sha256': hashlib.sha256(snippet.encode()).hexdigest(),
                'api_score': 100 * len(matched) + 5 * len(related) + overlap,
                'topic_score': 100 * len(hits) + 3 * overlap + 10 * len(related) - (30 if '>>>' in snippet else 0)})
    selected = []
    def choose(items, key):
        for item in sorted(items, key=lambda value: (-value[key], value['path'], value['start_line'])):
            if len(item['source']) > max_chars - sum(len(x['source']) for x in selected):
                continue
            if any(item['path'] == old['path'] and
                   not (item['end_line'] + 3 < old['start_line'] or old['end_line'] + 3 < item['start_line'])
                   for old in selected):
                continue
            selected.append(item)
            break
    choose(candidates, 'api_score')
    focused = [item for item in candidates if item['matched_error_terms'] and len(item['matched_api_names']) == 1]
    choose(focused, 'topic_score')
    choose(focused, 'topic_score')
    return selected


def prepare_baseline_test_context(runtime, instance, record, log_path, include_docs=False):
    """Retrieve from a verified baseline before CodeAct, passing only public Q/A."""
    import shlex
    import tempfile
    from openhands.events.action import CmdRunAction
    public = {'task_summary': record['task_summary'], 'exchanges': record['exchanges']}
    runtime.copy_to(str(Path(__file__).resolve()), '/tmp/')
    with tempfile.TemporaryDirectory(prefix='repair-public-') as folder:
        path = Path(folder) / 'repair_public.json'
        path.write_text(json.dumps(public))
        runtime.copy_to(str(path), '/tmp/')
    action = CmdRunAction(command='python /tmp/baseline_test_context.py --workspace '
                          + shlex.quote(runtime.swebench_workspace_dir)
                          + ' --public /tmp/repair_public.json --base ' + shlex.quote(str(instance.base_commit))
                          + (' --include-docs' if include_docs else ''))
    action.timeout = 120
    observation = runtime.run_action(action)
    records = [line[len(MARKER):] for line in str(observation.content).splitlines() if line.startswith(MARKER)]
    if observation.exit_code != 0 or len(records) != 1:
        raise RuntimeError('Baseline context retrieval failed or output was truncated')
    context = json.loads(records[0])
    Path(log_path).write_text(json.dumps(context, ensure_ascii=False, indent=2) + '\n')
    return context


def attach_context(instruction, context):
    """Attach unchanged public source as evidence, with no proposed fix."""
    chunks = ['\n<existing_baseline_test_context>',
              'These are existing public baseline tests retrieved by API names in the issue and Q/A. '
              'They are not new or hidden evaluation tests. Use their existing behavior contracts '
              'alongside the public exchanges, including interactions among the named operations. '
              'Do not change existing expectations to make a patch pass.']
    for item in context['files']:
        chunks.extend(['Source file: ' + item['path'],
                       'Matched public API names: ' + ', '.join(item['matched_api_names']),
                       '```python', item['source'], '```'])
    chunks.append('</existing_baseline_test_context>')
    if context.get('documentation'):
        chunks.append('<existing_baseline_documentation>')
        chunks.append('Verbatim baseline API documentation retrieved from public API names and error terms:')
        for item in context['documentation']:
            chunks.extend(['Source: ' + item['path'] + ':' + str(item['start_line']),
                           '```text', item['source'], '```'])
        chunks.append('</existing_baseline_documentation>')
    return instruction + '\n'.join(chunks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--public', required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--include-docs', action='store_true')
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if not Path('/.dockerenv').exists() or root.parent != Path('/workspace'):
        raise ValueError('Only an isolated repair runtime is supported')
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    if head != args.base:
        raise ValueError('Context repository is not the requested baseline')
    subprocess.check_call(['git', '-C', str(root), 'diff', '--exit-code', args.base, '--'],
                          stdout=subprocess.DEVNULL)
    public = json.loads(Path(args.public).read_text(encoding='utf-8'))
    tracked = set(subprocess.check_output(
        ['git', '-C', str(root), 'ls-tree', '-r', '--name-only', args.base, '--', 'tests', 'docs']).decode().splitlines())
    context = collect_context(root, public, allowed_paths=tracked)
    if args.include_docs:
        names = sorted({name for item in context['files'] for name in item['matched_api_names']})
        budget = min(6000, 22000 - sum(len(item['source']) for item in context['files']))
        context['documentation'] = collect_documentation(root, public, names, tracked, max_chars=budget)
        context['scope'] = 'existing-baseline-tests-and-documentation-only'
    context['source_commit'] = head
    print(MARKER + json.dumps(context), flush=True)


if __name__ == '__main__':
    main()
