"""Build compact comparison records from saved model output, without API calls."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    summary = json.loads((root / 'summary.json').read_text())
    boundaries = json.loads((root / 'review_cases.json').read_text())
    validation = {'source_snapshots_match': True, 'paired_initial_contexts_identical': True,
                  'checker_requests_public_only': True, 'repair_runs': manifest['repair_runs']}
    for rel, digest in manifest['source_sha256'].items():
        name = 'original_fixture.json' if rel.endswith('v1/fixture.json') else Path(rel).name
        validation['source_snapshots_match'] &= hashlib.sha256((root / 'source_snapshot' / name).read_bytes()).hexdigest() == digest
    lines = ['# 候选追问生成与独立审核：对照记录', '',
             '本轮新版未达到准入标准：两种 Partial 场景均未收到 F3；审核器将有效缺口问题拒绝，又批准了询问已知值的问题。详见逐条复核。', '',
             '单个开发样本，各方法每场景一条轨迹；仅澄清阶段，无完整修复。旧版 v5 保留，新版未替换生产入口。', '',
             '| 场景 | 旧版实际追问 | 新版实际追问 | 旧版收到 F3 | 新版收到 F3 |',
             '|---|---:|---:|---|---|']
    dialogues = ['# 全部公开问答与候选审核', '',
                 'after_unavailable 场景额外预置一条询问模型定义及“原文没有”的回答，不计为本轮真实追问。', '']
    candidates = []
    usage = {'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0}

    def count(attempt):
        usage['api_calls'] += 1
        values = attempt.get('response', {}).get('usage') or {}
        for key in ('prompt_tokens', 'completion_tokens'):
            usage[key] += values.get(key, 0)

    for item in manifest['plan']:
        scenario = item['scenario']
        old, new = summary[scenario]['baseline_v5'], summary[scenario]['candidate_v1']
        lines.append(f'| {scenario} | {old["followups"]} | {new["followups"]} | {old["f3_received"]} | {new["f3_received"]} |')
        contexts = [(root / scenario / method / 'initial_public_context.json').read_text() for method in item['methods']]
        validation['paired_initial_contexts_identical'] &= len(set(contexts)) == 1
        for method in item['methods']:
            folder = root / scenario / method
            record = json.loads((folder / 'answer_check.json').read_text())
            dialogues += [f'## {scenario} / {method}', '',
                          f'状态 {record["status"]}；停止原因 {record["stop_reason"]}', '']
            for index, exchange in enumerate(record['exchanges']):
                dialogues += [f'### 公开问答 {index + 1}', '', '**Q:** ' + exchange['question'], '',
                              '**A:** ' + exchange['answer'], '']
            for index, check in enumerate(record['checks']):
                if method == 'candidate_v1':
                    candidate = {'scenario': scenario, 'index': index + 1,
                                 'candidate': check.get('candidate'), 'review': check.get('review')}
                    candidates.append(candidate)
                    dialogues += [f'### 候选 {index + 1}', '', check.get('candidate', '(生成失败)'), '',
                                  json.dumps(check.get('review'), ensure_ascii=False, indent=2), '']
                else:
                    dialogues += [f'旧版检查 {index + 1}：' + json.dumps(check.get('decision'), ensure_ascii=False), '']
                for key in ('evidence_attempts', 'attempts', 'generator_attempts', 'reviewer_attempts'):
                    for attempt in check.get(key, []):
                        count(attempt)
                        for message in attempt['request_messages'][1:]:
                            if any(marker in message['content'] for marker in (
                                    'private_release_rules', 'PRIVATE_FACT_ID_DEFINITIONS', '"condition":', '"fact_ids":')):
                                validation['checker_requests_public_only'] = False
            for entry in json.loads((folder / 'interactions.json').read_text()):
                for attempt in entry.get('attempts', []):
                    count(attempt)
    lines += ['', 'F3 是缺失的精确派生操作；收到 F3 不等于问题问得准确，需结合逐条复核。', '',
              f'独立审核固定检查：{sum(r["pass"] for r in boundaries)}/{len(boundaries)} 符合预先期望的批准／拒绝。', '',
              '| 检查项 | 预期期望 | 模型决定 |', '|---|---|---|']
    for result in boundaries:
        count(result)
        lines.append(f'| {result["case"]} | {result["expected"]} | {result.get("review", {}).get("verdict", "error")} |')
    lines += ['', '新版行为：先生成一个纯文本候选，再由独立调用审核；审核不能改写候选。拒绝后停止，不重新抽取。最多发出两条追问。', '',
              '注意：拒绝一个候选不代表信息完整。生成和审核使用同一个 Qwen 配置的独立调用，可能共享判断偏差。', '',
              '[完整公开问答](dialogues.md) · [候选与审核](candidates.json) · [逐条复核](manual_review.md)', '']
    (root / 'REPORT.md').write_text('\n'.join(lines))
    (root / 'dialogues.md').write_text('\n'.join(dialogues))
    (root / 'candidates.json').write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + '\n')
    (root / 'validation.json').write_text(json.dumps(validation, indent=2) + '\n')
    (root / 'usage.json').write_text(json.dumps(usage, indent=2) + '\n')
    print('Saved comparison report: ' + str(root))


if __name__ == '__main__':
    main()
