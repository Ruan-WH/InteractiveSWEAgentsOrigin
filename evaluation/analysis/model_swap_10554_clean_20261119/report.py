"""Produce a short, qualified report after all official evaluations finish."""
import json
from pathlib import Path

from evaluation.analysis.model_swap_10554_clean_20261119.analyze import main as analyze


def main():
    analyze()
    here = Path(__file__).resolve().parent
    data = json.loads((here / 'results.json').read_text())
    assert data['source_unchanged_since_freeze']
    assert all(data['question_modules_equal_9208606'].values())
    names = {'ideal_no_followup': 'ideal（不追问）', 'partial_followup': 'partial（追问）',
             'partial_no_followup': 'partial（不追问）'}
    lines = ['10554：清理参考历史后的 Qwen3.8-Flash 三组实验', '',
        '基于 9208606；只替换修复 agent，Proxy 保持 Qwen3-Coder-Flash，检查器保持 DeepSeek-Flash。',
        '初始问题/回答固定。每组最多 100 步；追问组最多 2 次；不追问组跳过检查器和追问。',
        '交接沿用原版：issue 摘要 + 全部公开问答 + 未解决信息。不加入测试生成模块或额外摘要模型。',
        '温度 0、top_p 1、输出上限 8192、上下文上限 131072；新模型关闭思考。', '',
        '组别 | 实际追问 | 修复响应数 | 官方目标 | 官方回归 | resolved']
    for name, label in names.items():
        arm = data['arms'][name]
        assert arm['all_public_qa_handed_off_exactly']
        official = arm.get('official')
        if official is None:
            assert arm['patch_chars'] == 0, 'Nonempty patch but official report missing: ' + name
            lines.append(f"{label} | {arm['followups_used']} | {arm['solver_responses']} | — | — | 空补丁，未修复")
            continue
        assert arm['graded_patch_matches_raw_output']
        target = official['tests']['FAIL_TO_PASS']
        regression = official['tests']['PASS_TO_PASS']
        lines.append(f"{label} | {arm['followups_used']} | {arm['solver_responses']} | "
            f"{target['passed']}/{target['passed'] + target['failed']} | "
            f"{regression['passed']}/{regression['passed'] + regression['failed']} | {official['resolved']}")
    lines += ['', '实际补丁与执行状态：']
    for name, label in names.items():
        arm = data['arms'][name]
        lines += [label + '：' + (', '.join(arm['patch_files']) or '空补丁'),
                  '  修复错误：' + str(arm.get('solver_error')),
                  '  参考修复历史读取：' + str(arm['reference_fix_observation'] is not None)]
    lines += ['', '清理：工作目录及 /testbed 仅保留基线提交及其源码树；移除其他对象、标签、远端和实例 JSON 的参考答案字段。',
        '清理在 agent 开始前逐组验证；源码树和基线提交保持不变。结果只代表单样本单次实验。',
        '旧 Qwen 同类 partial 追问实验为目标 0/2、回归 23/23；这是历史参考，非新增配对对照。',
        '只测试一个开发样本、每组一次；模型是服务别名，不是已独立确认的固定权重。',
        '费用日志的 0 不代表免费；未确认当前网关真实计价。官方价格说明见 model_selection.json。',
        '问题模块四文件与 9208606 一致，实际交接保留全部公开问答；测试/静态检查记录见 verification.json。',
        '全部原始记录位于 evaluation/evaluation_outputs/model-swap-10554-clean-20261119；逐项数据见 results.json。']
    (here / 'RESULTS.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
