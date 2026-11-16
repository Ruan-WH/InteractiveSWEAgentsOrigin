"""Summarize saved pair outputs without new model or Docker calls."""

import argparse
import json
from pathlib import Path
import re


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def token_usage(responses):
    total = {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
    for response in responses:
        total['calls'] += 1
        usage = response.get('usage') or {}
        for key in ('prompt_tokens', 'completion_tokens'):
            total[key] += usage.get(key, 0) or 0
    return total


def has_exact_call(text):
    return bool(re.search(r"values_list\s*\(\s*['\"]pk['\"]\s*,\s*flat\s*=\s*True\s*\)", text))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    manifest = read(root / 'manifest.json')
    findings = read(root / 'protocol_audit.json', [])
    summary = {'instance_id': manifest['instance_id'], 'model': manifest['model'],
               'conditions': {}, 'protocol_audit': findings,
               'limits': ['One case, one realization per condition; no significance estimate.',
                          'Both arms use the checker; there is no Partial-without-followup arm.',
                          'Only the frozen Q2 is supplied; not the historical four-question batch.',
                          'The public summary already reveals the broad derived-query/re-evaluation mechanism.',
                          'Source truth/disclosure deviations must be considered before interpreting repair differences.']}
    transcript = ['# 10554 Ideal / Partial 实际问答', '',
                  '以下是实际公开问答及检查器决定。固定初始回复没有模型调用；后续检查与 Proxy 回复由模型产生。', '']
    for condition in manifest['condition_order']:
        folder = root / condition
        output = folder / 'output.jsonl'
        if not output.exists():
            summary['conditions'][condition] = {'status': manifest['conditions'].get(condition, {}).get('status')}
            continue
        row = json.loads(output.read_text().splitlines()[0])
        actions = [{key: event.get(key) for key in ('id', 'source', 'action', 'observation', 'message', 'args', 'content')
                    if key in event} for event in row.get('history', [])]
        (folder / 'solver_events.json').write_text(json.dumps(actions, ensure_ascii=False, indent=2) + '\n')
        check = row['test_result']['answer_check']
        turns = row['test_result']['interactions']
        initial = check['exchanges'][0]['answer']
        final_public = '\n'.join(e['answer'] for e in check['exchanges'])
        decisions = [c['decision'] for c in check['checks'] if 'decision' in c]
        grader_path = manifest['conditions'][condition].get('official_report')
        grader = read(Path(grader_path)) if grader_path else None
        grade = grader.get(manifest['instance_id']) if grader else None
        completion_paths = list((folder / 'inference').rglob('llm_completions/**/*.json'))
        solver_responses = [read(p).get('response', {}) for p in completion_paths]
        checker_responses = [a['response'] for c in check['checks'] for a in c['attempts']]
        proxy_responses = [a['response'] for t in turns for a in t.get('attempts', [])]
        tool_calls = [t for response in solver_responses
                      for choice in response.get('choices', [])
                      for t in (choice.get('message', {}).get('tool_calls') or [])]
        scored = grade.get('tests_status', {}) if grade else {}
        result = {
            'status': manifest['conditions'][condition]['status'],
            'checker_status': check['status'], 'stop_reason': check['stop_reason'],
            'first_decision': decisions[0] if decisions else None,
            'all_decisions': decisions, 'followups': check['followups_used'],
            'initial_exact_call_present': has_exact_call(initial),
            'final_exact_call_present': has_exact_call(final_public),
            'unresolved_information': check['unresolved_information'],
            'solver_error': row.get('error'),
            'solver_agent_actions': sum(e.get('source') == 'agent' and bool(e.get('action')) for e in row.get('history', [])),
            'checker_usage': token_usage(checker_responses),
            'proxy_usage': token_usage(proxy_responses),
            'solver_usage': token_usage(solver_responses),
            'empty_tool_call_items': sum(not t.get('function', {}).get('name') for t in tool_calls),
            'scored_tests': {name: {'passed': len(values.get('success', [])),
                                    'failed': len(values.get('failure', []))}
                             for name, values in scored.items()},
            'patch_sha256': manifest['conditions'][condition].get('patch_sha256'),
            'official_grade': grade,
        }
        summary['conditions'][condition] = result
        transcript += ['## ' + condition, '']
        for index, exchange in enumerate(check['exchanges']):
            transcript += [f'### 问答 {index + 1}', '', 'Agent：' + exchange['question'], '',
                           'Proxy：', '', exchange['answer'], '']
        transcript += ['### 检查器决定', '']
        for index, decision in enumerate(decisions):
            transcript += [f"- 检查 {index + 1}：{decision['decision']}；缺口：{decision['missing_information'] or '无'}；"
                           f"理由：{decision['reason']}"]
        transcript += ['', '停止原因：' + check['stop_reason'], '']
    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    (root / 'dialogues.md').write_text('\n'.join(transcript))
    lines = ['# 10554 Ideal 与 Partial 单例对照结果', '',
             f"模型：{manifest['model']}；温度 {manifest['temperature']}；修复最多100步，最多2次追问。"
             '两组都经过同一个检查器，仅固定 Q2 初始回答不同。', '',
             '| 条件 | 追问 | 关键调用 | 修复模型调用 | 目标测试 | 回归测试 | 官方 resolved |',
             '| --- | ---: | --- | ---: | --- | --- | --- |']
    for condition, result in summary['conditions'].items():
        if 'followups' not in result:
            lines.append(f"| {condition} | 未完成 | — | — | — | — | — |")
            continue
        grade = result['official_grade'] or {}
        coverage = '初始已提供' if result['initial_exact_call_present'] else ('追问后恢复' if result['final_exact_call_present'] else '未恢复')
        def score(name):
            values = result['scored_tests'].get(name)
            return f"{values['passed']}/{values['passed'] + values['failed']}" if values else '待评测'
        lines.append(f"| {condition} | {result['followups']} | {coverage} | "
                     f"{result['solver_usage']['calls']} | {score('FAIL_TO_PASS')} | {score('PASS_TO_PASS')} | {grade.get('resolved', '无报告')} |")
    lines += ['', '精确调用指 `.values_list(\'pk\', flat=True)`。大体的“派生操作后重新求值原查询”已在摘要中公开，不算新增恢复事实。', '',
              '## 结论', '',
              '本次闭环在工程上完整运行，但没有观察到 Partial 的目标缺口被识别和补齐。'
              '两次追问都指向来源没有的模型定义，措辞变化没有带来信息增量。'
              'Ideal 已有关键步骤，却也问了一次模型定义；停止不等于通过了严格的充分性验证。', '',
              '两组官方目标测试均未通过，Partial 另新增一项回归失败。'
              '没有 Partial 不追问组，加上 Ideal Proxy 的越界根因补充，不能把修复差异归因于回答完整性或追问收益。', '',
              '## 实际判断', '']
    for condition, result in summary['conditions'].items():
        if result.get('first_decision'):
            d = result['first_decision']
            lines.append(f"- {condition} 首次判断：{d['decision']}；{d['question'] or d['reason']}")
    lines += ['', '## 协议偏差与解释限制', '']
    for finding in findings:
        lines.append(f"- {finding['condition']}：{finding['finding_zh']}")
    lines += ['- 只有一个样本、每组一次，且没有 Partial 不追问组；不能据此宣称追问提升修复率。',
              '- Ideal 是关键操作链信息完整，不是已验证的独立可运行项目；模型定义在原来源中缺失。',
              '- 修复阶段空工具名条目：Ideal '
              + str(summary['conditions'].get('ideal', {}).get('empty_tool_call_items', '待统计'))
              + '，Partial ' + str(summary['conditions'].get('partial', {}).get('empty_tool_call_items', '待统计'))
              + '；这是模型工具调用输出问题，不能直接归因于问答质量。',
              '- 详细原问原答见 dialogues.md；判断、Token、官方测试明细见 summary.json；原始补丁未经人工修改。', '']
    (root / 'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps({condition: {k:v for k,v in result.items() if k not in ('all_decisions','official_grade')}
                      for condition,result in summary['conditions'].items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
