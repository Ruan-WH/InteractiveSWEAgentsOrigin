"""Qwen interaction pilot; --check-only never starts containers or calls APIs."""

import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import tomllib

from run_hidden_qwen import MODEL_METADATA

DEFAULT_IDS = [
    'astropy__astropy-12907',
    'astropy__astropy-13033',
    'astropy__astropy-13236',
    'astropy__astropy-13579',
    'django__django-10554',
]


def prepare_environment(settings):
    bypass = set(filter(None, (os.environ.get('NO_PROXY', '') + ','
                              + os.environ.get('no_proxy', '')).split(',')))
    bypass.update(('localhost', '127.0.0.1', '::1'))
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(sorted(bypass))
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(name, '')
        if value.lower().startswith('socks://'):
            os.environ[name] = 'socks5://' + value[len('socks://'):]
    os.environ['API_KEY'] = settings['api_key']
    os.environ['INTERACTION_USER_API_KEY'] = settings['api_key']
    os.environ['INTERACTION_USER_BASE_URL'] = settings['base_url']
    os.environ['INTERACTION_USER_MODEL'] = settings['model'].removeprefix('openai/')
    os.environ['INTERACTION_USER_TEMPERATURE'] = str(settings.get('temperature', 0))
    os.environ['INTERACTION_USER_TOP_P'] = str(settings.get('top_p', 1))
    os.environ['USE_INSTANCE_IMAGE'] = 'true'
    os.environ['USE_HINT_TEXT'] = 'false'
    # Preserve the original runner's inverted flag: false disables web use in
    # its instruction, even though the internal browsing flag remains true.
    os.environ['RUN_WITH_BROWSING'] = 'false'
    os.environ.setdefault('DOCKER_RUNTIME_KWARGS', '{"mem_limit":"6g","memswap_limit":"8g"}')
    os.environ.pop('SANDBOX_ENV_GITHUB_TOKEN', None)


def check_images(ids):
    import docker
    from openhands import __version__
    from openhands.runtime.utils.runtime_build import (
        get_hash_for_lock_files, get_hash_for_source_files, get_runtime_image_repo,
    )
    source_hash = get_hash_for_source_files()
    missing = []
    client = docker.from_env()
    try:
        for instance_id in ids:
            image = ('docker.io/xingyaoww/sweb.eval.x86_64.' + instance_id.replace('__', '_s_')).lower()
            try:
                found = client.images.get(image)
                lock = f'oh_v{__version__}_{get_hash_for_lock_files(image)}'
                try:
                    client.images.get(f'{get_runtime_image_repo()}:{lock}_{source_hash}')
                    cache = 'exact runtime cached'
                except docker.errors.ImageNotFound:
                    try:
                        client.images.get(f'{get_runtime_image_repo()}:{lock}')
                        cache = 'dependency layer cached; source wrapper may rebuild'
                    except docker.errors.ImageNotFound:
                        cache = 'runtime wrapper may need building'
                print(f'{instance_id}: base={found.short_id}; {cache}')
            except docker.errors.ImageNotFound:
                missing.append(image)
    finally:
        client.close()
    if missing:
        raise RuntimeError('Missing local base images: ' + ', '.join(missing))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, choices=(3, 4, 5), default=3)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--prompt-variant', choices=('original', 'targeted-v1'), default='original')
    args, extra = parser.parse_known_args()
    project = Path(__file__).resolve().parents[4]
    os.chdir(project)
    sys.path.insert(0, str(project))
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
    config = tomllib.loads((project / 'config.toml').read_text())
    settings = config['llm']['hidden_qwen_coder']
    ids = DEFAULT_IDS[:args.samples]
    prepare_environment(settings)
    os.environ['INTERACTION_PROMPT_VARIANT'] = args.prompt_variant
    print('Prompt variant:', args.prompt_variant)
    print('Agent model:', settings['model'])
    print('User proxy:', os.environ['INTERACTION_USER_MODEL'], '(Qwen, not paper GPT-4o)')
    print('Selected instances:', ', '.join(ids))
    check_images(ids)
    if args.check_only:
        print('Check complete. No containers, builds, downloads, or API calls started.')
        return
    import litellm
    model = settings['model']
    litellm.register_model({model: MODEL_METADATA, model.removeprefix('openai/'): MODEL_METADATA})
    if not litellm.supports_function_calling(model=model):
        raise RuntimeError('Native tool calling unavailable')
    import pandas as pd
    dataset = pd.read_excel(project / 'evaluation/benchmarks/swe_bench/data/full_summaries_verified.xlsx')
    subset = dataset.set_index('instance_id', drop=False).loc[ids].reset_index(drop=True)
    with tempfile.TemporaryDirectory(prefix='interact-qwen-') as folder:
        path = Path(folder) / 'samples.xlsx'
        subset.to_excel(path, index=False)
        sys.argv = [
            'interact_run_infer.py', '--agent-cls', 'CodeActAgent',
            '--llm-config', 'llm.hidden_qwen_coder', '--csv_file', str(path),
            '--dataset', 'princeton-nlp/SWE-bench_Verified', '--split', 'test',
            '--eval-n-limit', str(len(ids)), '--max-iterations', '70',
            '--eval-num-workers', '1', '--eval-note', 'interaction-qwen-flash-pilot-v2',
            *extra,
        ]
        runpy.run_module('evaluation.benchmarks.swe_bench.interact_run_infer', run_name='__main__')


if __name__ == '__main__':
    main()
