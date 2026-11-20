"""Run the unchanged official harness with exact cached dependency Git blobs.

Only raw dependency-list GETs are substituted; patches, tests, test commands,
parsers and grading rules remain those of the installed SWE-bench harness.
Never imported by a repair agent.
"""
import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import swebench.harness.utils as harness_utils

data = json.loads((Path(__file__).resolve().parent / 'offline_grader_requirements.json').read_text())
assert all(hashlib.sha256(value.encode()).hexdigest() == data['sha256'][path]
           for path, value in data['files'].items())
prefix = 'https://raw.githubusercontent.com/' + data['repo'] + '/' + data['commit'] + '/'


def exact_cached_get(url, *args, **kwargs):
    if not url.startswith(prefix):
        raise RuntimeError('Offline dependency substitution refused an unexpected URL')
    path = url[len(prefix):]
    if path not in data['files']:
        return SimpleNamespace(status_code=404, text='')
    return SimpleNamespace(status_code=200, text=data['files'][path])


harness_utils.requests.get = exact_cached_get
print('Official harness: exact environment-commit dependency blobs substituted for HTTP only.', flush=True)
runpy.run_module('swebench.harness.run_evaluation', run_name='__main__')
