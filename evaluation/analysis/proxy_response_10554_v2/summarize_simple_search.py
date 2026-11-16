"""Record all search attempts and the selected pilot, without new model calls."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tomllib


PROJECT = Path(__file__).resolve().parents[3]
OUTPUTS = PROJECT / 'evaluation/evaluation_outputs'
SELECTED = OUTPUTS / 'simple-focused-final-20261116'
MANUAL_REVIEWS = {
    '2317-complete': 'Value b"GET", bytes type, converted literal and 404/expected normal method all given; all three stops appropriate.',
    '2317-missing-method-value': 'All three questions ask the actual binary method value; GET/POST are framed as examples, not asserted facts. Repeat 0 also asks the converted literal, which is not supplied exactly.',
    '24661-complete': 'Exact string, evaluate=False, True actual result and unevaluated expected expression all supplied; all three stops appropriate.',
    '24661-missing-expression-text': 'All three questions ask the exact missing expr_text, not a guessed relational input.',
    '14623-complete': 'Existing data is given; set_yscale and set_ylim calls plus both outcomes supplied; all three stops appropriate.',
    '14623-missing-limit-call': 'All three questions ask the omitted limit-setting call/arguments. Repeat 0 also asks values and both scale cases; these are relevant and available in the original report.',
}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cases = {c['id']: c for c in json.loads((SELECTED / 'cases.json').read_text())}
    fresh = json.loads((SELECTED / 'fresh_results.json').read_text())
    verification = json.loads((SELECTED / 'runtime_verification.json').read_text())
    completion = json.loads((SELECTED / 'completion_result.json').read_text())
    assert len(fresh) == 18
    assert len(verification['closure_replays']) == 9
    assert completion['status'] == 'completed' and completion['question'] == ''

    reviews = []
    for row in sorted(fresh, key=lambda r: (r['case'], r['repeat'])):
        case = cases[row['case']]
        action_match = row['status'] == 'completed' and bool(row['question']) == (case['expected_action'] == 'ask')
        manual_note = MANUAL_REVIEWS[row['case']]
        reviews.append({**row, 'expected_action': case['expected_action'],
                        'expected_gap': case['expected_gap'], 'action_match': action_match,
                        'assistant_targeting_review': True, 'manual_note': manual_note,
                        'review_basis': 'Inspected all generated questions against the original question and source-backed complete/partial pair; not an external human annotation.'})
    dump(root / 'selected_reviews.json', reviews)
    assert all(r['action_match'] and r['assistant_targeting_review'] for r in reviews)

    attempts = []
    source_integrity = []
    response_ids = {}
    def collect(value):
        if isinstance(value, dict):
            response = value.get('response')
            if isinstance(response, dict) and response.get('model') in ('deepseek-flash', 'qwen3-coder-flash'):
                response_ids[response['id']] = response
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for folder in sorted(OUTPUTS.glob('simple-*')):
        manifest_path = folder / 'manifest.json'
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        for rel, digest in manifest.get('source_sha256', {}).items():
            path = folder / 'source_snapshot' / Path(rel).name
            source_integrity.append(path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest)
        case_map = {c['id']: c for c in json.loads((folder / 'cases.json').read_text())}
        for result_file in sorted(folder.glob('*_results.json')):
            if result_file.name == 'closure_results.json':
                continue
            rows = json.loads(result_file.read_text())
            if not isinstance(rows, list) or not rows or 'case' not in rows[0]:
                continue
            counts = {'records': len(rows), 'failed': 0, 'ask_stop_match_only': 0}
            for row in rows:
                counts['failed'] += row['status'] != 'completed'
                expected = case_map.get(row['case'], {}).get('expected_action')
                counts['ask_stop_match_only'] += (row['status'] == 'completed' and
                    bool(row.get('question')) == (expected == 'ask'))
            attempts.append({'folder': str(folder), 'stage_file': result_file.name,
                'policies': sorted({row.get('policy', '') for row in rows}), **counts,
                'note': 'Coarse action agreement only, NOT targeted-question quality or independent evaluation.'})
        for path in folder.rglob('*.json'):
            if 'source_snapshot' in path.parts or 'offline_replay' in path.parts:
                continue
            collect(json.loads(path.read_text()))
    dump(root / 'all_attempts.json', attempts)
    dump(root / 'recorded_usage.json', {
        'unique_model_responses': len(response_ids),
        'prompt_tokens': sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in response_ids.values()),
        'completion_tokens': sum((r.get('usage') or {}).get('completion_tokens', 0) for r in response_ids.values()),
        'note': 'Logged provider usage only; no billing estimate. Does not count rejected HTTP requests without responses or offline replay as new calls.',
    })
    settings = tomllib.loads((PROJECT / 'config.toml').read_text())['llm']
    keys = [value['api_key'].encode() for value in settings.values() if isinstance(value, dict) and value.get('api_key')]
    goal_files = [path for folder in OUTPUTS.glob('simple-*') for path in folder.rglob('*') if path.is_file()]
    leak = any(any(key in path.read_bytes() for key in keys) for path in goal_files)
    public_only = True
    for path in SELECTED.glob('fresh/focused_answer/*/*/*.json'):
        value = json.loads(path.read_text())
        for message in value.get('request_messages', [])[1:]:
            public_only &= not any(marker in message['content'] for marker in (
                'expected_gap', 'expected_action', 'source_issue', 'private_release_rules', '"condition":'))
    validation = {'all_snapshots_match_recorded_hashes': all(source_integrity),
        'final_case_hash_matches_manifest': verification['case_hash_matches_manifest'],
        'fresh_runtime_decisions_replay_identically': all(r['input_identical'] and r['decision_identical'] for r in verification['fresh_replays']),
        'primary_runtime_inputs_replay_identically': all(r['inputs_identical'] for r in verification['primary_runtime_replays']),
        'selected_checker_requests_public_only': public_only, 'configured_api_keys_absent_from_artifacts': not leak,
        'files_checked': len(goal_files), 'repair_runs': 0}
    assert all(value for key, value in validation.items() if key not in ('files_checked', 'repair_runs'))
    dump(root / 'validation.json', validation)
    snapshot = root / 'runtime_source'
    snapshot.mkdir(exist_ok=True)
    hashes = {}
    for rel in ('evaluation/utils/simple_clarification.py', 'evaluation/utils/simple_followup.py'):
        path = PROJECT / rel
        shutil.copy2(path, snapshot / path.name)
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    dump(root / 'runtime_sha256.json', hashes)

    dialogues = ['# Selected pipeline: all fresh public questions and followups', '']
    for review in reviews:
        case_id, repeat = review['case'], str(review['repeat'])
        case = cases[case_id]
        folder = SELECTED / 'fresh/focused_answer' / case_id / repeat
        initial = json.loads((folder / 'response.json').read_text())
        dialogues += [f'## {case_id} / {repeat}', '', 'Summary: ' + case['summary'], '',
            'Original question: ' + case['exchanges'][0]['question'], '',
            'Reporter answer: ' + case['exchanges'][0]['answer'], '',
            'Generated followup: ' + (initial.get('question') or '(stop)'), '']
        closure_path = folder / 'closure.json'
        if closure_path.exists():
            closure = json.loads(closure_path.read_text())
            replay = next(r for r in verification['closure_replays'] if r['case'] == case_id and r['repeat'] == repeat)
            dialogues += ['First source-backed scripted reply: see closure_started.json.',
                'Raw next output: ' + closure['response']['choices'][0]['message']['content'], '',
                'Current parser replay: ' + json.dumps(replay, ensure_ascii=False), '']
    (root / 'dialogues.md').write_text('\n'.join(dialogues))

    lines = ['# 简单追问流程：探索结果', '',
        '筛出的候选：**每次检查只调用一次模型，返回已有证据或一个追问**。代码入口为 `run_simple_clarification`。', '',
        '当前验证配置：DeepSeek-flash 检查回答；原 Qwen Proxy 保留。未重跑 CodeAct 修复，也未替换现有评测默认入口。', '',
        '流程：公开摘要＋原问题及回答 → 一次完整性检查 → 问一个遗漏部分／停止 → 收到补答后再检查。程序只做源覆盖校验、未知问题记忆、重复与预算控制。最多两次追问。', '',
        '为避免检查范围漂移，程序用公开英文摘要中的 after/before/again/re-evaluate 选择操作链检查；其他问题只核对原问题是否被完整回答。不传 Ideal/Partial、隐藏原文、缺口标签或标准答案。', '',
        '## 新验证：3 个 issue、6 种构造问答，各重复 3 次', '',
        '| 情况 | 正确次数 | 复核 |', '|---|---:|---|',
        '| 2317：方法值已给／缺失 | 6/6 | 缺失时追问确切 bytes 方法值 |',
        '| 24661：表达式已给／缺失 | 6/6 | 缺失时追问 expr_text |',
        '| 14623：限制调用已给／缺失 | 6/6 | 缺失时追问调用和参数 |', '',
        '**18/18 初始判断符合预先标准，全部 9 个追问均指向实际缺口。** 这是 3 个 issue 的人工构造配对，不是 18 个独立样本。提示在这些调用之前固定。', '',
        '补答测试：9 条序列最终均停止。原始首次补答后为 5 条停止、3 条拼接引文校验失败、1 条继续询问。3 条失败经逐句来源覆盖校验离线恢复，不重抽模型；剩余 1 条确实只给了 log 配置，模型继续问 linear 配置，补齐原文中的两种配置后停止。补答由原文支持的脚本提供，不是新的真实 Proxy／人工用户。', '',
        '## 10554 开发证据与已知限制', '',
        '- 严格操作链分支第一次三轮重复：Ideal 6/6 不追问，Partial 6/6 从真实 Qwen Proxy 收到 F3，补回后 6/6 停止。',
        '- 较后一次确认：Ideal 4/4 不追问，Partial 4/4 收到 F3；其中 1/4 补回后仍提出多余问题，其余 3/4 主动停止。这些旧请求与当前运行函数逐条离线回放一致。',
        '- 未完全解决多轮重复、隐含需求和所有类型的缺口识别。关键词范围选择面向本项目英文摘要；不能把它当可靠的语义分类器。',
        '- 仅检查已有问题是否被回答完整；未问过的需求仍依赖前置强制澄清。只验证了当前 DeepSeek 配置，不代表 Qwen 用同一方案也会相同。',
        '- 模型配置与流程都相对旧版发生变化；不能从这组小试验单独归因于流程或模型。',
        '- 引文匹配证明文字有来源，不证明语义完整或修复正确。跨轮证据允许由各自有来源的完整句子组成，不允许拼词造出新句子。',
        '- 所有旧 validation/transfer/external 在后续调试中均转为开发数据；仅最终 fresh 组用于最后的固定提示检验。没有把失败集重新标成成功。', '',
        '## 被淘汰的方向', '',
        '- 英／中文短提示、单纯加提示强度：仍索要模型定义和已知构造。',
        '- 只提取操作链：漏掉摘要中的未展示步骤；固定四槽位则会追问隐含的正常预期。',
        '- 单独生成候选再审核：审核器也会把缺失说成已回答。',
        '- 统一要求完整复现：在 10554 有效，但会向其他 issue 索要原文没有的样例和 traceback。',
        '- 统一判断“足以修复”：又会把未给定输入当成可以猜出的事实，漏问。', '',
        '最终有效的区别是：**按原问题核对回答，避免把“我大概知道怎么修”当成“用户已经回答了”。**', '',
        '[全部探索记录](all_attempts.json) · [最终问答](dialogues.md) · [逐条复核](selected_reviews.json) · [一致性校验](validation.json)', '']
    (root / 'REPORT.md').write_text('\n'.join(lines))
    print('Saved full search record: ' + str(root))


if __name__ == '__main__':
    main()
