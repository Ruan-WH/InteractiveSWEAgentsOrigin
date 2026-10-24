"""Launch the original hidden evaluator with the local DeepSeek Flash config.

From the project root:
    PYTHONPATH=. /home/rwh/miniconda3/envs/ambig-swe/bin/python \
        evaluation/benchmarks/swe_bench/scripts/run_hidden_flash.py

Extra evaluator arguments override the defaults, for example --max-iterations 5.
Credentials and selected_ids are read from the Git-ignored root config.toml.
"""

import os
from pathlib import Path
import runpy
import sys
import tempfile
import tomllib


def main(
    llm_config_name='hidden_deepseek_flash',
    eval_note='hidden-deepseek-flash-first',
    model_metadata=None,
):
    # HTTPX 0.27 requires the explicit SOCKS5 scheme, rather than socks://.
    # Normalize only this process's proxy settings before importing LiteLLM.
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy'):
        proxy = os.environ.get(name, '')
        if proxy.lower().startswith('socks://'):
            os.environ[name] = 'socks5://' + proxy[len('socks://'):]

    project = Path(__file__).resolve().parents[4]
    os.chdir(project)
    sys.path.insert(0, str(project))
    # Runtime image tooling also needs this environment's poetry executable.
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
    with (project / 'config.toml').open('rb') as stream:
        config = tomllib.load(stream)
    llm = config['llm'][llm_config_name]
    if model_metadata is not None:
        import litellm

        # This LiteLLM version strips openai/ during capability lookup.
        # Register both names so metadata and native-tool checks agree.
        model = llm['model']
        litellm.register_model({
            model: model_metadata,
            model.removeprefix('openai/'): model_metadata,
        })
        if not litellm.supports_function_calling(model=model):
            raise RuntimeError('Native tool calling is not enabled for this model')
    # The original module requires this at import, although hidden never calls
    # its simulated user. Keep the original evaluator and framework untouched.
    os.environ['API_KEY'] = llm['api_key']
    os.environ.setdefault('USE_INSTANCE_IMAGE', 'true')
    os.environ.setdefault('USE_HINT_TEXT', 'false')
    # Match the original shell defaults, including its existing browsing flag.
    os.environ.setdefault('RUN_WITH_BROWSING', 'false')
    os.environ.pop('SANDBOX_ENV_GITHUB_TOKEN', None)

    extra_args = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix='hidden-flash-') as temporary:
        import pandas as pd

        source = project / 'evaluation/benchmarks/swe_bench/data/full_summaries_verified.xlsx'
        dataset = pd.read_excel(source)
        selected_ids = config['hidden_experiment']['selected_ids']
        subset = dataset[dataset.instance_id.isin(selected_ids)]
        if not selected_ids or len(subset) != len(set(selected_ids)):
            raise ValueError('selected_ids must identify existing, nonempty dataset rows')
        subset_path = Path(temporary) / 'hidden_samples.xlsx'
        subset.to_excel(subset_path, index=False)
        sys.argv = [
            'hidden_run_infer.py',
            '--agent-cls', 'CodeActAgent',
            '--llm-config', f'llm.{llm_config_name}',
            '--csv_file', str(subset_path),
            '--dataset', 'princeton-nlp/SWE-bench_Verified',
            '--split', 'test',
            '--eval-n-limit', str(len(subset)),
            '--max-iterations', '70',
            '--eval-num-workers', '1',
            '--eval-note', eval_note,
            *extra_args,
        ]
        runpy.run_module('evaluation.benchmarks.swe_bench.hidden_run_infer', run_name='__main__')


if __name__ == '__main__':
    main()
