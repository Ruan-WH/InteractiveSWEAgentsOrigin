"""Produce a short, qualified report after all official evaluations finish."""
import json
from pathlib import Path

from evaluation.analysis.model_swap_10554_offline_20261119.analyze import main as analyze


def main():
    analyze()
    here = Path(__file__).resolve().parent
    data = json.loads((here / 'results.json').read_text())
    assert data['source_unchanged_since_freeze']
    assert all(data['question_modules_equal_9208606'].values())
    names = {'ideal_no_followup': 'ideal（不追问）', 'partial_followup': 'partial（追问）',
             'partial_no_followup': 'partial（不追问）'}
    lines = ['10554：清理参考历史并禁用外网后的 Qwen3.8-Flash 三组实验', '',
        '基于 9208606；只替换修复 agent，Proxy 保持 Qwen3-Coder-Flash，检查器保持 DeepSeek-Flash。',
        '初始问题/回答固定。每组最多 100 步；追问组最多 2 次；不追问组跳过检查器和追问。',
        '交接沿用原版：issue 摘要 + 全部公开问答 + 未解决信息。不加入测试生成模块或额外摘要模型。',
        '温度 0、top_p 1、输出上限 8192、上下文上限 131072；新模型关闭思考。', '',
        '组别 | 实际追问 | 修复响应数 | 官方目标 | 官方回归 | resolved']
    for name, label in names.items():
        arm = data['arms'][name]
        network = json.loads((here / ('runtime_network_' + name + '.json')).read_text())
        assert network['external_tcp_blocked'] and all(network['networks_internal'].values())
        assert arm['reference_history']['verified'] and arm['reference_history']['pip_cache_absent']
        assert arm['reference_fix_observation'] is None, 'Unexpected reference repair observation'
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
    lines += ['', '环境：两个仓库仅保留基线提交及源码树；移除其他 Git 对象、标签、远端及实例 JSON 答案字段。',
        '逐组清理验证和网络审计通过；容器无外网，pip 缓存清空，GitHub/PyPI 请求失败。',
        '中间仅清理 Git 的尝试因下载新版源码而停止；该尝试和上一轮结果均单独保留。',
        '',
        '评分细节：官方目标计数均为 0/2，但原始日志中旧目标明确显示 ok。',
        '新目标发生子测试失败后，两个测试名拼到同一行，解析器误判旧目标；未改写官方报告。',
        '新增排序目标确实失败，因此三组 resolved=False 的结论不变。证据见 results.json 的 raw_test_log_review。',
        '',
        '行为：ideal 删除不匹配的排序项，仍得到错误顺序；partial 追问组补回信息但仅生成复现脚本。',
        'partial 不追问组主要修改默认排序传播，仍未解决新增排序目标。三组都用完 100 步。',
        '四个追问模块与 9208606 一致，全部公开问答逐字交接，未加入修复测试生成模块。',
        '限制：仅一个开发样本、每组一次；尚无新模型独立修复收益证据，不作为总体修复率结论。',
        '检查：65 项单元测试通过；环境预检、逐容器审计、配置与来源哈希见 verification.json 及相关 JSON。',
        '全部原始记录：evaluation/evaluation_outputs/model-swap-10554-offline-20261119。']
    (here / 'RESULTS.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
