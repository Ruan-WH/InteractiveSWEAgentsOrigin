"""Rebuild an evidence-checked, single-annotator audit of the ten saved runs.

No model/API calls, credentials, containers, or changes to historical runs.
Decisions are explicit LLM-assisted judgments in annotation_decisions.json,
not classifications produced by this export script.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
RUNS = PROJECT / 'evaluation/evaluation_outputs'
INPUTS = [
    RUNS / 'qwen-proxy-grounded-v1-20261029/inference.output.snapshot.jsonl',
    RUNS / 'qwen-new7-grounded-v1-20261101/inference.output.snapshot.jsonl',
]
SENSITIVE = {
    ('astropy__astropy-12907', 3): ['incomplete'],
    ('astropy__astropy-13579', 3): ['incomplete'],
    ('astropy__astropy-13398', 6): ['truth'],
    ('astropy__astropy-8872', 3): ['truth'],
    ('django__django-10880', 4): ['truth'],
}
OLD_USAGE = {
    'astropy__astropy-12907': [
        '事件7读取 separable.py：导航信息进入检索。',
        '事件11/13将 Linear1D 和 Pix2Sky_TAN 组合用于复现。',
        '事件87把正确右下对角块误判为应全 True；说明预期理解仍不稳定，不能归因证明由回答遗漏导致。',
        '用户未提供测试；Agent 事件9/87自行检索和运行测试。',
        '事件87仍混淆矩阵布尔值；错误类别说明未成为可靠数值判据。',
    ],
    'astropy__astropy-13033': [
        '事件35明确分析重复 time 报错；有诊断采用迹象。',
        '事件61将首列访问改为完整列列表/切片，但未逐字采用 required 措辞；属于部分采用。',
        '事件35搜索 required.*flux，说明注意到具体列，但后续转向 BinnedTimeSeries，没有忠实复现源场景。',
        '来源只给测试文件导航线索，Agent 后续自行找测试；没有用户提供专门失败测试的证据。',
    ],
    'astropy__astropy-13236': [
        'NdarrayMixin 进入定位；事件39移除相应自动转换分支。',
        '事件29/39围绕 structured ndarray 的自动转换复现并修改。',
        '事件29/39采用直接创建 Column 的方向。',
        '事件39删除来源中展示的 data.view(NdarrayMixin) 分支。',
        '事件39直接删除自动转换，未新增警告；与 Q3 共享同一个需求决策。',
    ],
}


def read_json(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value):
    if value is None or str(value).lower() == 'nan':
        return ''
    return str(value).replace('_x000D_', '').replace('\r\n', '\n')


def compact(value):
    return re.sub(r'\s+', ' ', clean(value)).strip()


def split_numbered(text):
    matches = list(re.finditer(r'(?m)^(\d+)\.\s+', text))
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = int(match.group(1))
        assert number not in result, f'Duplicate number {number}'
        result[number] = text[match.end():end].strip()
    return result


def write_json(name, data):
    (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def counts(rows):
    return {
        'questions': len(rows),
        'incomplete': sum(r['labels']['incomplete'] for r in rows),
        'ambiguous': sum(r['labels']['ambiguous'] for r in rows),
        'truth_issue': sum(r['labels']['truth'] != 'supported' for r in rows),
        'any_primary_issue': sum(
            r['labels']['incomplete'] or r['labels']['ambiguous']
            or r['labels']['truth'] != 'supported' for r in rows
        ),
        'source_availability': dict(Counter(r['labels']['source_availability'] for r in rows)),
        'truth': dict(Counter(r['labels']['truth'] for r in rows)),
        'responsiveness': dict(Counter(r['labels']['responsiveness'] for r in rows)),
    }


def label_text(row):
    labels = row['labels']
    tags = []
    if labels['incomplete']:
        tags.append('Incomplete')
    if labels['ambiguous']:
        tags.append('Ambiguous')
    if labels['truth'] == 'conflicting':
        tags.append('与后期来源冲突')
    elif labels['truth'] == 'mixed':
        tags.append('部分事实无依据/确定化')
    if labels['source_availability'] != 'available':
        tags.append('来源部分可答' if labels['source_availability'] == 'partial' else '来源不可答')
    if labels['responsiveness'] != 'direct':
        tags.append('部分回应/答非所问')
    return '；'.join(tags) or '充分且有依据'


def main():
    decisions = read_json(HERE / 'annotation_decisions.json')
    records, sources, excluded, message_review = [], {}, [], []
    annotations, trajectories, manifest = [], {}, []
    prior_path = RUNS / 'qwen-new7-grounded-v1-20261101/question_annotations.json'
    prior = {(r['instance_id'], r['question_number']): r for r in read_json(prior_path)}
    manifest.append({'path': str(prior_path), 'sha256': digest(prior_path),
                     'use': 'prior downstream adoption audit only; not answer-quality labels'})
    grading_paths = [RUNS / 'qwen-proxy-grounded-v1-20261029/comparison.json',
                     RUNS / 'qwen-new7-grounded-v1-20261101/official_eval/results.json']
    grades = read_json(grading_paths[0])['current'] + read_json(grading_paths[1])['results']
    grades = {r['instance_id']: r['resolved'] for r in grades}
    for path in grading_paths:
        manifest.append({'path': str(path), 'sha256': digest(path),
                         'use': 'historical outcome context only; not used to assign answer-quality labels'})
    for path in INPUTS:
        manifest.append({'path': str(path), 'sha256': digest(path)})
        for line in path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                record['_input_path'] = str(path)
                records.append(record)
    assert len(records) == 10
    assert len({r['instance_id'] for r in records}) == 10
    for record in records:
        iid = record['instance_id']
        inst = record['instance']
        sources[iid] = {
            'input_snapshot': record['_input_path'],
            'base_commit': inst['base_commit'],
            'summary': clean(inst.get('problem_statement')),
            'issue': clean(inst.get('original_issue')),
            'hints': clean(inst.get('hints_text')),
            'files': clean(inst.get('files')),
        }
        turns = record['test_result']['interactions']
        assert [t['turn'] for t in turns] == [1, 2, 3]
        first = turns[0]
        questions = split_numbered(first['question'])
        answers = split_numbered(first['answer'])
        assert set(questions) == set(answers) == set(range(1, len(decisions[iid]) + 1))
        history = {e['id']: e for e in record['history'] if 'id' in e}
        trajectories[iid] = {}
        for event in record['history']:
            if event.get('source') == 'agent' and event.get('action') == 'message':
                text = event.get('args', {}).get('content', event.get('message', ''))
                candidate = bool(re.search(
                    r'\?|\b(?:can|could|would) you\b|please (?:provide|clarify|confirm)',
                    text, re.I,
                ))
                message_review.append({
                    'instance_id': iid, 'event_id': event['id'], 'text': text,
                    'candidate_from_lexical_screen': candidate,
                    'targeted_user_followup': False,
                    'review_note': (
                        '该句 But what is dtype at this point? 是自我诊断，未向用户索取信息。'
                        if candidate else '修复阶段进度/自我分析或空消息，没有面向用户的针对性追问。'
                    ),
                })
        for turn in turns[1:]:
            excluded.append({
                'instance_id': iid, 'turn': turn['turn'],
                'agent_message': turn['question'], 'proxy_answer': turn['answer'],
                'classification': 'progress_acknowledgement',
                'reason': '当前消息为进度说明，Proxy 仅确认；不是新澄清问题及其答案。',
            })
        for number, decision in enumerate(decisions[iid], 1):
            evidence = []
            for source, quote in decision['evidence']:
                assert compact(quote) in compact(sources[iid][source]), (iid, number, quote)
                evidence.append({
                    'source': source, 'quote': quote,
                    'match': 'substring_after_whitespace_and_excel_CR_normalization',
                    'source_path': str(HERE / 'sources.json'),
                    'source_json_pointer': '/' + iid + '/' + source,
                })
            for event_id in decision['events']:
                assert event_id in history, (iid, event_id)
                event = history[event_id]
                raw = event.get('tool_call_metadata', {}).get('model_response', {})
                choices = raw.get('choices', [])
                trajectories[iid][str(event_id)] = {
                    'event_id': event_id, 'source': event.get('source'),
                    'action': event.get('action'), 'observation': event.get('observation'),
                    'message': event.get('message'), 'args': event.get('args'),
                    'content': event.get('content'),
                    'llm_message': choices[0].get('message') if choices else None,
                }
            annotations.append({
                'annotation_id': f'{iid}:turn1:q{number}',
                'instance_id': iid, 'turn': 1, 'question_number': number,
                'title_zh': decision['title'],
                'question': questions[number], 'answer': answers[number],
                'batch_context': {
                    'questions': first['question'], 'answer': first['answer'],
                    'evaluation_scope': 'current numbered item with whole-batch context',
                },
                'labels': {
                    'truth': decision['truth'],
                    'incomplete': decision.get('incomplete', False),
                    'ambiguous': decision.get('ambiguous', False),
                    'source_availability': decision['availability'],
                    'responsiveness': decision.get('responsiveness', 'direct'),
                },
                'rationale_zh': decision['reason'],
                'missing_information_zh': decision.get('missing'),
                'unsupported_claim': decision.get('unsupported_claim'),
                'remaining_alternatives_zh': decision.get('alternatives', []),
                'evidence': evidence,
                'suggested_next_action_zh': decision['followup'],
                'downstream_event_ids': decision['events'],
                'downstream_use_zh': (
                    OLD_USAGE[iid][number - 1] if iid in OLD_USAGE
                    else prior[(iid, number)]['actual_effect']
                ),
                'downstream_use_provenance': (
                    'current review with exported history events' if iid in OLD_USAGE
                    else str(prior_path) + f' (instance_id={iid}, question_number={number})'
                ),
                'downstream_caveat_zh': (
                    prior.get((iid, number), {}).get('diagnostic_caveat')
                    or prior.get((iid, number), {}).get('operational_caveat')
                ),
                'historical_official_resolved': grades[iid],
                'review_sensitive_dimensions': SENSITIVE.get((iid, number), []),
                'targeted_followup_observed': False,
                'annotation_method': 'current Codex LLM, source-grounded single-annotator review',
                'independent_human_review': 'pending',
            })
    assert len(annotations) == 42 and len(excluded) == 20
    summary = {
        'schema_version': 'answer-quality-audit-v1', 'date': '2026-11-16',
        'annotation_method': 'Single Codex LLM annotator; no external judge API calls; evidence mechanically checked; no independent human review.',
        'inputs': manifest,
        'decision_file_sha256': digest(HERE / 'annotation_decisions.json'),
        'unit': 'numbered initial question and its final delivered answer, interpreted in full-batch context',
        'initial_question_batches': 10, 'proxy_reply_messages': 30,
        'excluded_progress_acknowledgements': len(excluded),
        'solver_plain_text_messages_reviewed': len(message_review),
        'targeted_solver_followups_observed': 0,
        'overall': counts(annotations),
        'by_instance': {iid: counts([r for r in annotations if r['instance_id'] == iid]) for iid in sources},
        'primary_issue_instances': sorted({r['instance_id'] for r in annotations
            if r['labels']['incomplete'] or r['labels']['ambiguous'] or r['labels']['truth'] != 'supported'}),
        'limitations': [
            'Selected ten cases, not random benchmark prevalence.',
            'Multiple numbered answers can share one underlying gap; rates are not independent observations.',
            'No followup is descriptive; progress messages also consumed the original three-reply budget.',
            'Source support does not establish execution correctness or fidelity to a gold patch.',
            'Downstream adoption is observational and cannot establish per-answer causal effects.',
            'Ambiguous and incomplete are distinct; missing exact outputs need not admit multiple semantic interpretations.',
            'All labels require independent human review before use as publication ground truth.',
        ],
    }
    conservative = json.loads(json.dumps(annotations))
    for row in conservative:
        for dimension in row['review_sensitive_dimensions']:
            row['labels'][dimension] = 'supported' if dimension == 'truth' else False
    summary['sensitivity_if_borderline_flags_are_removed'] = counts(conservative)
    write_json('sources.json', sources)
    write_json('annotations.json', annotations)
    write_json('excluded_messages.json', excluded)
    write_json('solver_message_review.json', message_review)
    write_json('trajectory_evidence.json', trajectories)
    write_json('summary.json', summary)
    make_report(annotations, summary)
    print(json.dumps({'output_directory': str(HERE), 'summary': summary['overall'],
                      'affected_instances': summary['primary_issue_instances'],
                      'reviewed_solver_messages': len(message_review)}, ensure_ascii=False, indent=2))


def make_report(rows, summary):
    all_counts = summary['overall']
    lines = [
        '# 10 个历史样本的 Proxy 回答充分性分析与标注', '',
        '本次对两轮已完成的 grounded-v1 实验进行回顾，分析最终真正交给 Agent 的回答。'
        '标注由当前 Codex 模型逐题对照完整来源完成，尚未经过独立人工或第二位评审复核；'
        '没有额外调用外部 Judge API。生成脚本仅导出显式标注决定、校验引用和计算统计，不自动判断答案质量。', '',
        '## 数据和统计口径', '',
        '- 前 3 例来自 qwen-proxy-grounded-v1-20261029，新增 7 例来自 qwen-new7-grounded-v1-20261101。',
        '- 共 10 批初始问答，42 道编号问题，30 条成功保存的 Proxy 回复；其中 20 条仅确认进度，不纳入回答质量分母。',
        '- 标注使用最终交付答案，不用复核前的原始 draft；丢弃、失败及重试问答不混入这 42 道问题。',
        '- 每题单独记录，但判断时可见同批其他答案；共享事实不能重复宣称为独立信息增量。',
        '- 缺失的 Hints/files 中字符串 nan 统一视为空；原文的重复段落、乱码不当作新事实。', '',
        '## 标注标准', '',
        '| 维度 | 标签与含义 |', '| --- | --- |',
        '| 真实性 | supported：来源支持；mixed：正确内容与无依据/过度确定化混合；conflicting：违背后期明确来源 |',
        '| Incomplete | 来源能提供、与当前所问直接相关且影响行为或验证的关键事实未被回答；不因没有复述全部 issue 而标记 |',
        '| Ambiguous | 结合整批回答后仍有两种以上合理且行为不同的解释；不把一般不够详细、实现方法多样自动算作需求歧义 |',
        '| 来源可答性 | available：当前所需事实有来源；partial：只有部分子项或有保留意见；unavailable：所求具体信息没有来源 |',
        '| 回应程度 | direct：直接回应；partial：未回答部分子项、改答相关根因，或未明确说明未知部分 |', '',
        '以上标签可重叠。supported 不等于充分；source-limited 不等于 Incomplete；'
        '后续修复失败也不自动意味着回答不充分。原文没有明确的兼容规范、精确版本或复现代码时，不用隐藏测试倒推应答要求。', '',
        '## 汇总结果', '',
        '| 指标 | 数量 | 占 42 题比例 |', '| --- | ---: | ---: |',
    ]
    for key, title in [('incomplete','关键事实遗漏 Incomplete'), ('ambiguous','残余歧义 Ambiguous'),
                       ('truth_issue','与来源冲突或含无依据/过度确定化内容'), ('any_primary_issue','上述至少一种问题，去重后')]:
        n = all_counts[key]
        lines.append(f'| {title} | {n}/42 | {n / 42:.1%} |')
    lines += ['', f"上述问题涉及 {len(summary['primary_issue_instances'])}/10 个样本。"
              '这些是所选历史样本的描述性频率，各标签不能相加作为总数。', '',
              '| 样本 | 题数 | Incomplete | Ambiguous | 真实性问题 | 至少一种问题 |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    for iid, c in summary['by_instance'].items():
        lines.append(f"| {iid} | {c['questions']} | {c['incomplete']} | {c['ambiguous']} | {c['truth_issue']} | {c['any_primary_issue']} |")
    sensitivity = summary['sensitivity_if_borderline_flags_are_removed']
    lines += ['', '### 边界标注的敏感性', '',
              '五个优先复核项是：12907 Q3、13579 Q3 的完整性；13398 Q6、8872 Q3、10880 Q4 的真实性。'
              '它们分别涉及精确判据是否属于当前问题必须交付的信息、历史讨论是否被当成当前约束、以及概括/版本指代是否过强。', '',
              f"若将这些边界标记全部取消，Incomplete 为 {sensitivity['incomplete']}/42，"
              f"真实性问题为 {sensitivity['truth_issue']}/42，Ambiguous 仍为 {sensitivity['ambiguous']}/42，"
              f"至少一种问题的去重数为 {sensitivity['any_primary_issue']}/42。"
              '这是标注尺度的敏感性分析，不是第二位评审的独立标注结果。']
    lines += ['', '### 来源不足与回答不充分的区别', '',
              '来源可答性分布：' + '；'.join(f'{k}={v}' for k,v in all_counts['source_availability'].items()) + '。', '',
              '- 12907 Q4、8707 Q3、10880 Q2：来源分别没有具体已有测试、bytes traceback/反例、最小 Count/Case 代码；不能把这些全部算作可通过追问恢复的信息。',
              '- 13033 Q4：Hints 有 test_common.py 的导航线索，但没有明确指出专门复现此 bug 的已有用例；标为部分可答，不把缺少特定测试名算作关键需求遗漏。',
              '- 10097 Q2：正确非法 URL 已给出，但反向合法却被拒绝的例子原文没有；未明确说未知属于部分回应。',
              '- 10880 Q4：回归提交有依据，最后正常版本没有；“Django 2.2 之前/之内”的具体解释需谨慎，当前轻度真实性标注应优先人工复核。', '',
              '### Agent 是否继续澄清', '',
              f"检查保存的 {summary['solver_plain_text_messages_reviewed']} 条修复阶段 Agent 纯文本消息，未观察到面向用户的针对性追问。"
              '唯一带问号的候选是 8872 事件 186 中的自我诊断 “But what is dtype at this point?”，不是请求用户补充信息。', '',
              '20 条成功交互中的后续消息都是进度说明，且同样消耗原有三次回复预算。'
              '因此能陈述“这些轨迹没有完成针对缺口的追问闭环”，但不能仅据此归因模型不具备识别能力；'
              '流程设计和回复预算也限制了追问。完整修复阶段消息见 solver_message_review.json。', '',
              '## 适合作为 Motivation 的案例', '',
              '### 10554 可回答的信息被遗漏', '',
              'Proxy 的最小复现在首次打印 qs 后结束。完整 issue 明确还包含求值 '
              "qs.order_by().values_list('pk', flat=True) 后再次求值原 qs 的步骤。"
              '这是比泛泛要求“多给些信息”更明确的追问目标。'
              '原文示例本身有括号不完整等问题，不能全部归咎 Proxy；关键触发链被删去则可以直接核实。'
              'Agent 后续尝试复制 query，但复现遇到语法、配置、建表问题；这些观察不能证明补齐回答必然修复成功。', '',
              '### 12907 定性回答正确仍可能缺少验证判据', '',
              'Proxy 正确说独立分量在嵌套后仍应独立，却没有传达来源中的精确预期矩阵。'
              '事件 87 显示 Agent 将正确的右下对角块误判为应全 True。'
              '这支持为具体反例补充精确输出判据的动机，但不证明原回答在逻辑上有两种合理需求解释，'
              '也不能证明有矩阵就必然避免推理错误。该例最终官方 resolved 为 True，不能将中途误判写成最终失败。', '',
              '### 13398 不完整与不真实必须分开', '',
              'Q3/Q5/Q6 延续早期忽略时间或警告讨论，Q7 将范围限于 ITRS/Observed；'
              '后期 Hints 已转向 actual topocentric ITRS，并明确涉及 ICRS/CIRS/TETE。'
              '同一批回答同时存在最终方向遗漏、历史方案状态混淆及警告/异常分支未定。'
              '事件 132 的修改采纳旧方向并加入 UserWarning，说明错误信息确实有被采用的风险，'
              '但这里是回答纠正与补充的混合问题，不适合作为纯 Partial 条件的唯一证据。', '',
              '### 13236 充分回答与正确停止的正例', '',
              '历史回答已完整传达直接创建 Column、立即改变且无需 FutureWarning，纠正了问题自带的旧前提。'
              '这例历史数据不能写成“Proxy 原本回答不完整”。若后续将其用于 Partial 实验，'
              '必须明确是新构造的受控回答缺口，并保留该历史充分回答作参考。', '',
              '## 对下一步初测的具体影响', '',
              '- 保留 12907、13236、10554 的实验选择，但明确 13236 是充分回答正例与后续受控 Partial 素材。',
              '- 核心任务区分为：补充可回答遗漏、识别无依据或过期信息、对来源不足正确停止。',
              '- 当前严格 Ambiguous 标签集中在 13398 的同批警告/异常选择，不能宣传为普遍存在的残余需求歧义。',
              '- 先用以上标注定义充分性检查项，再设计 Partial 策略，避免为了展示追问收益而把每个答案都判为不足。', '',
              '## 限制和复核建议', '',
              '这是单模型辅助的第一版标注，全部 42 条仍需独立人工复核；机械引用匹配只验证摘录确实存在，'
              '不验证语义推断一定成立。优先复核 12907 Q3 的“关键验证细节”边界、13579 Q3 的回应范围、'
              '8872 Q3 的过度概括以及 10880 Q4 的版本指代。小样本、共享批次、原有进度消息消耗预算、'
              '历史运行的导入路径问题和人工操作都会限制因果结论。', '',
              '未重新运行修复或测试这些历史补丁。后续使用证据沿用已有轨迹，并导出所引事件供检查；'
              '不能把后来单独诊断得到的结果冒充 Agent 当时已完成的验证。', '',
              '此前审计引用过的 13398 事件154 不在所用快照 history 中，本次不沿用该引用；'
              '导出的全部事件号均已检查存在。新7例的信息使用说明保留此前逐题审计的来源，'
              '并附原有导入路径和人工操作等 caveat，不当作本轮重新执行得到的结论。', '',
              '## 文件与复现', '',
              '- annotations.md：42 题原问原答、标签、理由、证据与建议追问。',
              '- annotations.json：相同逐题记录的机器可读格式，包含整批上下文。',
              '- annotation_decisions.json：本次逐题判断；修改标注应先修改此文件。',
              '- sources.json：10 个样本的摘要、完整 issue、Hints、files 和历史快照路径。',
              '- summary.json：统计、输入 SHA256、标注方法及限制。',
              '- excluded_messages.json：20 条排除的进度交互。',
              '- solver_message_review.json：修复阶段纯文本消息及针对性追问检查。',
              '- trajectory_evidence.json：逐题引用的原始事件内容，事件号沿用历史 history.id。', '',
              '复现导出与校验：', '', '```bash',
              f'/home/rwh/miniconda3/envs/ambig-swe/bin/python -B {HERE / "build_annotations.py"}',
              '```', '']
    (HERE / 'README.md').write_text('\n'.join(lines))
    detail = ['# 42 道问题的逐题标注', '',
              '口径、总体统计和限制见 README.md。以下原问原答按编号拆分；实际判定结合完整批次。'
              '原文中的代码围栏及语法问题保留。单模型辅助标注，独立人工复核尚未完成。', '']
    for row in rows:
        detail += [f"## {row['instance_id']} Q{row['question_number']} {row['title_zh']}", '',
                   f"标签：{label_text(row)}。真实性：{row['labels']['truth']}。", '',
                   '原问题：', '', '~~~~text', row['question'], '~~~~', '',
                   '实际交付回答：', '', '~~~~text', row['answer'], '~~~~', '',
                   '判定：' + row['rationale_zh'], '']
        if row['missing_information_zh']:
            detail += ['缺失或未回应项：' + row['missing_information_zh'], '']
        if row['unsupported_claim']:
            detail += ['需核实的无依据/确定化表述：' + row['unsupported_claim'], '']
        if row['review_sensitive_dimensions']:
            detail += ['优先人工复核维度：' + '、'.join(row['review_sensitive_dimensions']) + '。已计入敏感性分析。', '']
        if row['remaining_alternatives_zh']:
            detail += ['残余行为分支：' + '；'.join(row['remaining_alternatives_zh']), '']
        detail += ['来源证据：', '']
        for evidence in row['evidence']:
            detail += [f"- {evidence['source']}：{evidence['quote']}"]
        detail += ['', '建议下一步：' + row['suggested_next_action_zh'], '',
                   '后续使用观察：' + row['downstream_use_zh'], '',
                   '观察来源：' + row['downstream_use_provenance'], '',
                   '历史官方 resolved：' + str(row['historical_official_resolved'])
                   + '。仅作背景，不用于判定本题回答充分性。', '',
                   '后续证据事件：' + (', '.join(map(str,row['downstream_event_ids'])) or '未指定'), '',
                   '实际针对性追问：未观察到。后续事件仅用于检查信息使用，不用于倒推本题标签。', '']
        if row['downstream_caveat_zh']:
            detail += ['历史诊断限制：' + row['downstream_caveat_zh'], '']
    (HERE / 'annotations.md').write_text('\n'.join(detail))


if __name__ == '__main__':
    main()
