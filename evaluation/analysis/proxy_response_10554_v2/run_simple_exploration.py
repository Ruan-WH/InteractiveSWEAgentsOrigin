"""Freeze public development/validation cases, then test single-call followup prompts."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomllib

from openai import OpenAI

from evaluation.utils.simple_followup import SIMPLE_PROMPTS, generate_simple_followup
from evaluation.utils.controlled_proxy import ControlledProxy
from evaluation.analysis.proxy_response_10554_v2.run_clarification import make_fixture
from types import SimpleNamespace


PROJECT = Path(__file__).resolve().parents[3]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def build_cases():
    fixture = json.loads((PROJECT / 'evaluation/analysis/proxy_response_10554_v1/fixture.json').read_text())
    source = json.loads((PROJECT / 'evaluation/analysis/answer_quality_10_20261116/sources.json').read_text())
    cases = []
    for condition in ('ideal', 'partial'):
        for unknown in (False, True):
            cases.append({
                'id': '10554-' + condition + ('-unknown' if unknown else ''), 'split': 'development',
                'summary': fixture['public_task']['summary'],
                'exchanges': [{'question': fixture['initial_responses']['question'],
                               'answer': fixture['initial_responses'][condition]['answer']}],
                'expected_action': 'stop' if condition == 'ideal' else 'ask',
                'expected_gap': '' if condition == 'ideal' else 'exact operation and arguments after first successful display, before original query fails',
            })
            if unknown:
                cases[-1]['exchanges'].append({'question': 'Can you provide the complete Dimension model definition?',
                                              'answer': 'I do not have that information in the supplied source.'})

    def add(case_id, iid, answer, action, gap=''):
        cases.append({'id': case_id, 'split': 'validation', 'summary': source[iid]['summary'],
                      'exchanges': [{'question': 'What concrete behavior and reproduction does the report describe?',
                                     'answer': answer}], 'expected_action': action, 'expected_gap': gap})

    error = "ValueError: TimeSeries object is invalid - expected 'time' as the first columns but found 'time'"
    start = 'The existing TimeSeries ts has time and flux columns with data. '
    end = f"The reported exception is {error}. Expected: report that required columns are missing."
    add('13033-complete', 'astropy__astropy-13033', start +
        'Run ts._required_columns = ["time", "flux"], then ts.remove_column("flux"). ' + end, 'stop')
    add('13033-missing-required-setting', 'astropy__astropy-13033', start +
        'Run ts.remove_column("flux"). ' + end, 'ask', 'which additional columns are required and how declared')
    add('13033-missing-error', 'astropy__astropy-13033', start +
        'Run ts._required_columns = ["time", "flux"], then ts.remove_column("flux"). '
        'The exception message is confusing. Expected: report that required columns are missing.',
        'ask', 'exact current exception message')
    add('10880-source-exhausted', 'django__django-10880',
        'In Django 2.2, a Count annotation containing a Case condition and distinct=True generates '
        'COUNT(DISTINCTCASE WHEN ..., which raises a query syntax error on every backend. '
        'Expected: separate DISTINCT and CASE with whitespace. The supplied source does not include '
        'a complete ORM reproduction, model definitions, or database setup.', 'stop')
    add('8707-complete', 'astropy__astropy-8707',
        'Header.fromstring rejects Python 3 bytes while accepting str assuming ASCII encoding. '
        'Header.fromfile already accepts text and binary file modes. Expected: Header.fromstring '
        'accept both str and bytes; Card.fromstring needs similar treatment. The source does not '
        'provide a concrete bytes example or exact traceback.', 'stop')
    add('8707-missing-method', 'astropy__astropy-8707',
        'A header parsing method rejects Python 3 bytes, but accepts str assuming ASCII encoding. '
        'Its file-reading counterpart accepts both text and binary file modes. Expected: the '
        'parser accepts both str and bytes. The source does not provide a concrete bytes example '
        'or exact traceback.', 'ask', 'name of the affected parser method and related method')
    # New transfer cases frozen before observing responses to the contract policy.
    def transfer(case_id, iid, answer, action, gap=''):
        add(case_id, iid, answer, action, gap)
        cases[-1]['split'] = 'transfer'
    transfer('10097-complete', 'django__django-10097',
        'URLValidator accepts raw :, @ and / in the username/password fields. These characters '
        'must be percent-encoded. For example http://foo/bar@example.com currently passes but '
        'should be rejected. Appending ?m=foo@example.com can also make invalid URLs pass. '
        'The required change is to reject these invalid unencoded userinfo cases.', 'stop')
    transfer('10097-missing-input', 'django__django-10097',
        'URLValidator incorrectly accepts some URLs with username and password fields. '
        'The invalid forms should be rejected rather than accepted.', 'ask',
        'which unencoded characters or concrete invalid URLs are accepted')
    matrix = ('The function is separability_matrix. With cm = m.Linear1D(10) & m.Linear1D(5), '
        'separability_matrix(cm) is [[True, False], [False, True]]. '
        'The flattened combination m.Pix2Sky_TAN() & m.Linear1D(10) & m.Linear1D(5) has '
        'a 2x2 True upper-left block and independent diagonal lower-right linear models. ')
    transfer('12907-complete', 'astropy__astropy-12907', matrix +
        'For m.Pix2Sky_TAN() & cm, the actual lower-right 2x2 block is all True. '
        'Expected: preserve the lower-right diagonal block as in the flattened combination.', 'stop')
    transfer('12907-missing-nested-expression', 'astropy__astropy-12907', matrix +
        'A nested expression instead gives an all-True lower-right 2x2 block. '
        'Expected: preserve the lower-right diagonal block as in the flattened combination.', 'ask',
        'the exact nested expression producing the all-True lower-right block')
    wcs = ('Given the existing 3D fits_wcs with spatial and spectral axes coupled by its PC '
        'matrix, create sl = SlicedLowLevelWCS(fits_wcs, 0). ')
    transfer('13579-complete', 'astropy__astropy-13579', wcs +
        'Set world = fits_wcs.pixel_to_world_values(0, 0, 0), then '
        'out_pix = sl.world_to_pixel_values(world[0], world[1]). '
        'out_pix[0] is incorrect; expected np.allclose(out_pix[0], 0). '
        'The reverse pixel-to-world operation works.', 'stop')
    transfer('13579-missing-world-input', 'astropy__astropy-13579', wcs +
        'Run out_pix = sl.world_to_pixel_values(world[0], world[1]). '
        'out_pix[0] is incorrect; expected np.allclose(out_pix[0], 0). '
        'The reverse pixel-to-world operation works.', 'ask',
        'the concrete world coordinates or how world was obtained')
    import pandas as pd
    public_rows = pd.read_excel(PROJECT / 'full_summaries_verified.xlsx',
        usecols=['instance_id', 'original_issue', 'problem_statement'])
    def external(case_id, iid, answer, action, gap=''):
        row = public_rows.loc[public_rows.instance_id == iid].iloc[0]
        cases.append({'id': case_id, 'split': 'external', 'summary': row.problem_statement,
            'exchanges': [{'question': 'What concrete inputs, observed behavior and expected behavior does the report describe?', 'answer': answer}],
            'expected_action': action, 'expected_gap': gap,
            'source_issue': str(row.original_issue).replace('_x000D_', '')})
    external('2931-complete', 'psf__requests-2931',
        'requests.put("http://httpbin.org/put", data=u"√∂√∂√∂".encode("utf-8")) works in '
        'Requests 2.8.1 but fails in 2.9. The report attributes the failure to calling '
        'to_native_string. Expected: continue accepting this encoded binary payload. '
        'The supplied issue does not give an exception traceback.', 'stop')
    external('2931-missing-payload', 'psf__requests-2931',
        'requests.put("http://httpbin.org/put", data=payload) works in Requests 2.8.1 but '
        'fails in 2.9. The payload is described as encoded data. The report attributes the '
        'failure to calling to_native_string. Expected: continue accepting the binary payload. '
        'The supplied issue does not give an exception traceback.', 'ask',
        'the exact payload value/type or how it was encoded')
    external('5414-complete', 'psf__requests-5414',
        'requests.get("http://.example.com") raises UnicodeError: encoding with idna codec '
        'failed (label empty or too long). Expected: raise a requests invalid-URL exception '
        'indicating URL has an invalid label, rather than leaking UnicodeError.', 'stop')
    external('5414-missing-url', 'psf__requests-5414',
        'requests.get(url) raises UnicodeError: encoding with idna codec failed (label empty '
        'or too long). Expected: raise a requests invalid-URL exception indicating URL has '
        'an invalid label, rather than leaking UnicodeError.', 'ask', 'the exact failing URL')
    sympy = ('With SymPy 1.8.dev, clear_cache(); x, y, z = symbols("x y z", real=True). ')
    external('21379-complete', 'sympy__sympy-21379', sympy +
        'Set expr = exp(sinh(Piecewise((x, y > x), (y, True)) / z)), then expr.subs({1: 1.0}) '
        'raises PolynomialError: Piecewise generators do not make sense. A repeat without '
        'clearing cache works; non-real symbols work. Expected: substitution should succeed '
        'without this exception consistently.', 'stop')
    external('21379-missing-expression', 'sympy__sympy-21379', sympy +
        'For an expression expr involving a hyperbolic function and a Piecewise argument, '
        'expr.subs({1: 1.0}) raises PolynomialError: Piecewise generators do not make sense. '
        'A repeat without clearing cache works; non-real symbols work. Expected: substitution '
        'should succeed without this exception consistently.', 'ask', 'the exact expression expr')
    def fresh(case_id, iid, question, answer, action, gap=''):
        external(case_id, iid, answer, action, gap)
        cases[-1]['split'] = 'fresh'
        cases[-1]['exchanges'][0]['question'] = question
    q = 'What was the exact method argument value and type, and what happened when it was converted?'
    fresh('2317-complete', 'psf__requests-2317', q,
        'On Python 3.4 with Requests 2.4.3, method = b"GET" is bytes. '
        'builtin_str(method) turns it into the literal string "b\'GET\'", not "GET", and '
        'the request gets 404 Not Found. Expected: handle the binary method as normal GET, '
        'as in Python 2.6.', 'stop')
    fresh('2317-missing-method-value', 'psf__requests-2317', q,
        'On Python 3.4 with Requests 2.4.3, method is a binary string. '
        'builtin_str(method) turns it into a literal representation rather than a normal '
        'HTTP method, and the request gets 404 Not Found. Expected: handle the binary method '
        'as a normal HTTP method, as in Python 2.6.', 'ask', 'the exact binary HTTP method value')
    q = 'What exact input string and evaluate flag were passed to parse_expr, and what actual and expected results were reported?'
    fresh('24661-complete', 'sympy__sympy-24661', q,
        'parse_expr("1 < 2", evaluate=False) returns True. Expected: return an unevaluated '
        'relational expression equivalent to Lt(1, 2, evaluate=False), displayed as 1 < 2.', 'stop')
    fresh('24661-missing-expression-text', 'sympy__sympy-24661', q,
        'parse_expr(expr_text, evaluate=False) returns True. expr_text is described as an '
        'inequality. Expected: return the unevaluated relational expression instead of True.',
        'ask', 'the exact expression string expr_text')
    q = 'What exact scale-setting and limit-setting calls were used, and what actual versus expected behavior occurred?'
    start = 'For existing plotted data y, call ax.set_yscale("log"); '
    ending = 'The linear scale inverts, but the log scale does not. Expected: both scales invert.'
    fresh('14623-complete', 'matplotlib__matplotlib-14623', q,
        start + 'ax.set_ylim(y.max(), y.min()). ' + ending, 'stop')
    fresh('14623-missing-limit-call', 'matplotlib__matplotlib-14623', q,
        start + 'then change the limits. ' + ending, 'ask', 'the exact limit-setting call and arguments')
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--split', choices=('development', 'validation', 'transfer', 'external', 'fresh'), default='development')
    parser.add_argument('--policy', choices=tuple(SIMPLE_PROMPTS))
    parser.add_argument('--llm-config', choices=('hidden_qwen_coder', 'hidden_deepseek_flash'), default='hidden_qwen_coder')
    parser.add_argument('--deliver-proxy', action='store_true')
    parser.add_argument('--repeats', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    if not manifest_path.exists():
        snapshot = root / 'source_snapshot'
        snapshot.mkdir()
        dump(root / 'cases.json', build_cases())
        hashes = {}
        for rel in ['evaluation/utils/simple_followup.py',
                    'evaluation/utils/batch_extract_followup.py',
                    'evaluation/utils/contract_slots.py',
                    'evaluation/utils/controlled_proxy.py',
                    'evaluation/analysis/proxy_response_10554_v2/run_clarification.py',
                    'evaluation/analysis/proxy_response_10554_v2/run_simple_exploration.py']:
            path = PROJECT / rel
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            shutil.copy2(path, snapshot / path.name)
        manifest = {'protocol': 'simple-single-call-exploration-v1', 'source_sha256': hashes,
                    'created_at': datetime.now().astimezone().isoformat(),
                    'cases_sha256': hashlib.sha256((root / 'cases.json').read_bytes()).hexdigest(),
                    'policy_names': list(SIMPLE_PROMPTS), 'repair_runs': 0,
                    'criteria': 'Exact requested gap, no unavailable/already-known requests; empty for complete/source-exhausted. Review outputs manually, not just ask/stop.',
                    'stages': {}}
        dump(manifest_path, manifest)
    manifest = json.loads(manifest_path.read_text())
    if args.prepare_only:
        print('Prepared all development and held-out inputs: ' + str(root), flush=True)
        return
    for rel, digest in manifest['source_sha256'].items():
        if hashlib.sha256((PROJECT / rel).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen source changed: ' + rel)
    if hashlib.sha256((root / 'cases.json').read_bytes()).hexdigest() != manifest['cases_sha256']:
        raise ValueError('Frozen cases changed')
    if args.split in manifest['stages']:
        raise ValueError('Stage already started; do not reroll')
    if args.split != 'development' and not args.policy:
        raise ValueError('Select one policy before validation')
    settings = tomllib.loads((PROJECT / 'config.toml').read_text())['llm'][args.llm_config]
    bypass = set(filter(None, (os.environ.get('NO_PROXY', '') + ',' + os.environ.get('no_proxy', '')).split(',')))
    bypass.update(('localhost', '127.0.0.1', '::1'))
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(sorted(bypass))
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(name, '')
        if value.lower().startswith('socks://'):
            os.environ[name] = 'socks5://' + value[len('socks://'):]
    client = OpenAI(api_key=settings['api_key'], base_url=settings['base_url'], timeout=120, max_retries=0)
    proxy_settings = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']['hidden_qwen_coder']
    proxy_client = OpenAI(api_key=proxy_settings['api_key'], base_url=proxy_settings['base_url'], timeout=120, max_retries=0)
    model = settings['model'].removeprefix('openai/')

    class Model:
        def completion(self, messages):
            return client.chat.completions.create(model=model, messages=messages,
                temperature=settings.get('temperature', 0), top_p=settings.get('top_p', 1),
                response_format={'type': 'json_object'})

    cases = [c for c in json.loads((root / 'cases.json').read_text()) if c['split'] == args.split]
    policies = [args.policy] if args.policy else list(SIMPLE_PROMPTS)
    plan = [(case, policy, rep) for rep in range(args.repeats) for case in cases for policy in policies]
    manifest['stages'][args.split] = {'started_at': datetime.now().astimezone().isoformat(),
        'policies': policies, 'repeats': args.repeats, 'model': model, 'llm_config': args.llm_config, 'temperature': settings.get('temperature', 0),
        'api_retries': 0, 'semantic_retries': 0, 'concurrency': 3,
        'deliver_proxy': args.deliver_proxy, 'proxy_model': proxy_settings['model'] if args.deliver_proxy else None}
    dump(manifest_path, manifest)

    def run(item):
        case, policy, repeat = item
        folder = root / args.split / policy / case['id'] / str(repeat)
        folder.mkdir(parents=True)
        try:
            # Only summary and Q/A enter the model, never private expected_action/gap.
            result = generate_simple_followup(Model(), case['summary'], case['exchanges'], policy)
        except Exception as error:
            result = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
        dump(folder / 'response.json', result)
        restored = None
        if args.deliver_proxy and case['id'].startswith('10554-'):
            condition = 'partial' if 'partial' in case['id'] else 'ideal'
            proxy = ControlledProxy(make_fixture(), condition, proxy_client,
                proxy_settings['model'].removeprefix('openai/'), folder / 'proxy.json')
            proxy.initial_exchange()
            proxy.public_history = [{k:e[k] for k in ('question','answer')} for e in case['exchanges']]
            if result.get('question'):
                try:
                    answer = proxy.generate_reply(SimpleNamespace(content=result['question']))
                    after = generate_simple_followup(Model(), case['summary'],
                        case['exchanges'] + [{'question': result['question'], 'answer': answer}], policy)
                    dump(folder / 'after.json', after)
                    result['followup_answer'] = answer
                    result['after_question'] = after.get('question')
                    restored = 'F3' in proxy.interactions[-1].get('fact_ids', [])
                except Exception as error:
                    result['delivery_error'] = f'{type(error).__name__}: {error}'
                dump(folder / 'response.json', result)
        return {'case': case['id'], 'policy': policy, 'repeat': repeat, 'status': result['status'],
                'question': result.get('question'), 'error': result.get('error'),
                'f3_received': restored, 'after_question': result.get('after_question'),
                'delivery_error': result.get('delivery_error')}

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(run, item) for item in plan]):
            result = future.result()
            results.append(result)
            dump(root / f'{args.split}_results.json', results)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    manifest['stages'][args.split]['finished_at'] = datetime.now().astimezone().isoformat()
    dump(manifest_path, manifest)


if __name__ == '__main__':
    main()
