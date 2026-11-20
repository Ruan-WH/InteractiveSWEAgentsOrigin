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
    if os.environ.get('EVAL_INTERNAL_RUNTIME_IP'):
        bypass.add(os.environ['EVAL_INTERNAL_RUNTIME_IP'])
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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--samples', type=int, choices=(1, 3, 4, 5), default=None)
    selection.add_argument('--instance-ids', nargs='+', help='Run these instance IDs in the supplied order')
    parser.add_argument('--agent-llm-config', default='hidden_qwen38_flash')
    parser.add_argument('--proxy-llm-config', default='hidden_qwen_coder')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--proxy-policy', choices=('original', 'grounded-v1'), default='original')
    parser.add_argument('--prompt-variant', choices=('original', 'targeted-v1', 'paper-mandatory-v1', 'answer-check-v1'), default='original')
    parser.add_argument('--max-followups', type=int, choices=(0, 1, 2), default=2,
                        help='Followups after the initial batch, for answer-check-v1 only')
    parser.add_argument('--response-fixture', type=Path)
    parser.add_argument('--response-condition', choices=('ideal', 'partial'))
    parser.add_argument('--answer-check-policy', choices=('legacy-v5', 'simple-v1'), default='legacy-v5')
    parser.add_argument('--checker-llm-config', default='hidden_deepseek_flash')
    parser.add_argument('--clarification-mode', choices=('adaptive', 'initial-only'), default='adaptive',
                        help='initial-only hands off the fixed Q/A without checker calls or followups')
    parser.add_argument('--clean-reference-history', action='store_true',
                        help='Keep only baseline Git objects and remove runtime reference-answer metadata')
    parser.add_argument('--repair-workflow', choices=('original', 'environment-v1', 'evidence-v1', 'composition-v1', 'composition-readable-v1', 'retrieved-v1', 'contrastive-v1', 'minimal-v1', 'bash-v1', 'progress-v1'), default='original',
                        help='Opt-in Django diagnosis: align installed source; optionally add repair workflow guidance')
    args, extra = parser.parse_known_args()
    project = Path(__file__).resolve().parents[4]
    os.chdir(project)
    sys.path.insert(0, str(project))
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
    config = tomllib.loads((project / 'config.toml').read_text())
    settings = config['llm'][args.agent_llm_config]
    proxy_settings = config['llm'][args.proxy_llm_config]
    if args.answer_check_policy == 'simple-v1':
        if args.prompt_variant != 'answer-check-v1':
            parser.error('simple-v1 requires --prompt-variant answer-check-v1')
        config['llm'][args.checker_llm_config]
    os.environ['ANSWER_CHECK_POLICY'] = args.answer_check_policy
    os.environ['ANSWER_CHECK_LLM_CONFIG'] = args.checker_llm_config
    ids = args.instance_ids if args.instance_ids else DEFAULT_IDS[:args.samples or 3]
    if len(ids) != len(set(ids)):
        parser.error('--instance-ids must not contain duplicates')
    if bool(args.response_fixture) != bool(args.response_condition):
        parser.error('--response-fixture and --response-condition must be supplied together')
    if args.clarification_mode == 'initial-only' and not args.response_fixture:
        parser.error('initial-only requires fixed initial Q/A')
    os.environ['CLARIFICATION_MODE'] = args.clarification_mode
    if args.clean_reference_history and not args.response_fixture:
        parser.error('reference cleanup currently requires the controlled experiment fixture')
    os.environ['CLEAN_REFERENCE_HISTORY'] = '1' if args.clean_reference_history else '0'
    if args.repair_workflow != 'original' and (
            not args.clean_reference_history or any(not iid.startswith('django__django-') for iid in ids)):
        parser.error('Repair workspace diagnosis requires clean reference history and Django instances')
    os.environ['REPAIR_WORKFLOW'] = args.repair_workflow
    if args.response_fixture:
        if args.prompt_variant != 'answer-check-v1':
            parser.error('Fixed response fixtures require --prompt-variant answer-check-v1')
        fixture_path = args.response_fixture.resolve()
        fixture = json.loads(fixture_path.read_text())
        if ids != [fixture['instance_id']]:
            parser.error('A fixed response fixture must run only its matching instance')
        os.environ['INTERACTION_RESPONSE_FIXTURE'] = str(fixture_path)
        os.environ['INTERACTION_RESPONSE_CONDITION'] = args.response_condition
    else:
        os.environ.pop('INTERACTION_RESPONSE_FIXTURE', None)
        os.environ.pop('INTERACTION_RESPONSE_CONDITION', None)
    prepare_environment(proxy_settings)
    os.environ['INTERACTION_PROMPT_VARIANT'] = args.prompt_variant
    os.environ['INTERACTION_PROXY_POLICY'] = 'controlled-fixture-v1' if args.response_fixture else args.proxy_policy
    os.environ['ANSWER_CHECK_MAX_FOLLOWUPS'] = str(args.max_followups)
    print('Proxy policy:', os.environ['INTERACTION_PROXY_POLICY'])
    if args.response_fixture:
        print('Fixed response condition:', args.response_condition)
    print('Prompt variant:', args.prompt_variant)
    print('Agent model:', settings['model'])
    print('User proxy:', os.environ['INTERACTION_USER_MODEL'])
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
            '--llm-config', f'llm.{args.agent_llm_config}', '--csv_file', str(path),
            '--dataset', 'princeton-nlp/SWE-bench_Verified', '--split', 'test',
            '--eval-n-limit', str(len(ids)), '--max-iterations',
            '100' if args.prompt_variant in ('paper-mandatory-v1', 'answer-check-v1') else '70',
            '--eval-num-workers', '1', '--eval-note', 'interaction-qwen-flash-pilot-v2',
            *extra,
        ]
        runpy.run_module('evaluation.benchmarks.swe_bench.interact_run_infer', run_name='__main__')


if __name__ == '__main__':
    main()
