"""Summarize every optimization attempt, including failures, without model calls."""

import argparse
import hashlib
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[3]
OUTPUTS = PROJECT / 'evaluation/evaluation_outputs'
SCENARIOS = ['ideal', 'partial', 'partial_after_unavailable', 'ideal_after_unavailable']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    rounds = []
    dialogues = ['# 澄清阶段优化：全部公开问答', '', '每一版均保留；带 unavailable 的场景额外加入一条人工预置的未知回答。', '']
    initial_fixture = json.loads((PROJECT / 'evaluation/analysis/proxy_response_10554_v1/fixture.json').read_text())
    for version in range(2, 6):
        folder = OUTPUTS / f'clarification-10554-v{version}-20261116-run1'
        summary = json.loads((folder / 'summary.json').read_text())
        manifest = json.loads((folder / 'manifest.json').read_text())
        fixture = json.loads((folder / 'source_snapshot/fixture.json').read_text())
        validation = {
            'initial_answers_unchanged': fixture['initial_responses'] == initial_fixture['initial_responses'],
            'source_snapshots_match_manifest': all(
                hashlib.sha256((folder / 'source_snapshot' / Path(rel).name).read_bytes()).hexdigest() == digest
                for rel, digest in manifest['source_sha256'].items()),
            'checker_requests_public_only': True,
        }
        totals = {'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0}

        def count(attempt):
            totals['api_calls'] += 1
            usage = attempt.get('response', {}).get('usage') or {}
            for key in ('prompt_tokens', 'completion_tokens'):
                totals[key] += usage.get(key, 0)

        for scenario in SCENARIOS:
            record = json.loads((folder / scenario / 'answer_check.json').read_text())
            dialogues += [f'## v{version} / {scenario}', '',
                          f'状态：{record["status"]}；停止：{record["stop_reason"]}；追问：{record["followups_used"]}', '']
            if record['error']:
                dialogues += [f'错误：{record["error"]}', '']
            for index, exchange in enumerate(record['exchanges']):
                dialogues += [f'### 问答 {index + 1}', '', '**Q:** ' + exchange['question'], '',
                              '**A:** ' + exchange['answer'], '']
            for index, check in enumerate(record['checks']):
                decision = check.get('decision')
                dialogues += [f'检查 {index + 1}：' + json.dumps(decision, ensure_ascii=False), '']
                for attempt in check.get('evidence_attempts', []) + check['attempts']:
                    count(attempt)
                    # Public evidence is derived solely from summary and public answers.
                    for message in attempt['request_messages'][1:]:
                        if any(marker in message['content'] for marker in (
                            'private_release_rules', 'PRIVATE_FACT_ID_DEFINITIONS',
                            'private_source_json', '"condition":', '"fact_ids":',
                        )):
                            validation['checker_requests_public_only'] = False
            if len(record['checks']) and record['checks'][-1].get('public_evidence'):
                dialogues += ['末次公开证据表：', '', '```json',
                              json.dumps(record['checks'][-1]['public_evidence'], ensure_ascii=False, indent=2), '```', '']
        for path in folder.rglob('interactions.json'):
            for interaction in json.loads(path.read_text()):
                for attempt in interaction.get('attempts', []):
                    count(attempt)
        rounds.append({'version': version, 'folder': str(folder), 'summary': summary,
                       'validation': validation, 'usage': totals})
    latest = rounds[-1]
    lines = ['# 10554 澄清模块优化记录', '',
             '当前尚未达到进入完整修复对照的标准：普通 Partial 收到了缺失操作，但追问是否准确命中缺口仍有疑问；Ideal 多余追问、未知信息重复追问仍未解决。', '',
             '本次仅运行澄清阶段，没有 CodeAct 修复、Docker 执行或官方修复评分。', '',
             '初始 Ideal/Partial 回答沿用原版本；检查模块始终只看公开摘要与问答。每版每场景仅运行一次，保留失败结果。', '',
             '| 版本 | Ideal：追问/达标 | Partial：追问/补回 F3 | 已知未知后的 Partial：追问/补回 F3 | 已知未知后的 Ideal：追问/达标 | Proxy 规则匹配 |',
             '|---|---|---|---|---|---|']
    for run in rounds:
        s = run['summary']
        cells = []
        for scenario in SCENARIOS:
            entry = s[scenario]
            success = entry['pass'] if scenario.startswith('ideal') else entry['f3_recovered_by_followup']
            cells.append(f'{entry["followups"]} / {"是" if success else "否"}' +
                         ('（运行失败）' if entry['status'] == 'failed' else ''))
        # Table orders normal pair, then seeded partial and ideal.
        boundary = s['boundaries']
        lines.append(f'| v{run["version"]} | ' + ' | '.join(cells) +
                     f' | {boundary["passed"]}/{boundary["total"]} |')
    lines += ['', 'F3 指精确中间操作及参数；表中“补回”仅表示 Proxy 实际返回了 F3，不等于人工确认追问准确命中缺口。广义“派生查询后重新显示原查询”的时机已在公开摘要中，不算新增信息。', '',
              '各版修改：', '',
              '- v2：加强操作链、未知信息处理提示；Proxy 改为模型选规则、程序返回已审核模板。',
              '- v3：增加公开证据提取，再决定追问；修正字段定义问题的规则范围。',
              '- v4：要求先引用摘要中的失败转折；发现逐字引文和输出结构校验失败。',
              '- v5：改用程序编号的公开文本作为引用来源，统一证据条目格式。', '',
              '当前结果：', '']
    for scenario in SCENARIOS:
        entry = latest['summary'][scenario]
        record = json.loads((Path(latest['folder']) / scenario / 'answer_check.json').read_text())
        lines.append(f'- {scenario}：{entry["status"]}，追问 {entry["followups"]} 次；'
                     f'规则 {entry["rules"]}；停止原因 {entry["stop_reason"]}。')
        if entry['error']:
            lines.append('  错误：' + entry['error'])
        if entry['followups']:
            initial_count = 2 if scenario.endswith('_after_unavailable') else 1
            if len(record['exchanges']) > initial_count:
                lines.append('  首次追问：' + record['exchanges'][initial_count]['question'])
    lines += ['', '解释与限制：', '',
              '- v2 的两个规则匹配失败实际返回了“原文没有信息”，没有额外透露诊断；严格规则 ID 判断仍记为失败。',
              '- Proxy 模板约束能够限制正文自由发挥；模型仍可能误选规则。20 条边界问答不是泛化保证。',
              '- v5 普通 Partial 的第二次追问仍主要要求 union 构造及 order_by 调用；Proxy 将其匹配为 R2，给出 F3。该请求没有明确指向首次成功后的中间操作，存在宽松匹配或误选规则的可能，不能据此宣称缺口识别已经成功。',
              '- 文本编号校验保证引用存在于公开材料，不保证模型描述与引用在语义上完全一致。',
              '- 四个版本在同一开发样本上调试；这些结果属于开发证据，不能当独立评测成绩。',
              '- unavailable 场景包含人工预置历史，用来测试处理未知信息，不是新的真实用户样本。',
              '- 达标标准在每轮调用前写入 manifest；没有改初始回答、给检查器隐藏代码或重抽同版结果。', '',
              '[全部公开问答与检查决定](dialogues.md) · [机器可读汇总](summary.json)', '']
    (root / 'REPORT.md').write_text('\n'.join(lines))
    (root / 'dialogues.md').write_text('\n'.join(dialogues))
    (root / 'summary.json').write_text(json.dumps(rounds, ensure_ascii=False, indent=2) + '\n')
    print('Recorded all attempts: ' + str(root))


if __name__ == '__main__':
    main()
