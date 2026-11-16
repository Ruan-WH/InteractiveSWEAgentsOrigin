# 澄清阶段优化试验

后续探索已筛出更简单的单调用候选，见 [SIMPLE_PIPELINE.md](SIMPLE_PIPELINE.md)；该文档及 `simple-followup-search-20261116/REPORT.md` 是最新状态。下面保留前几轮失败试验的历史说明。

当前：Proxy 已改为模型选规则、程序返回审核模板；检查模块仍未稳定识别缺口，暂未进入三组修复对照。

另已完成“先生成候选、再独立审核”的单轮对照；未替换 v5 入口。运行器为 `run_candidate_comparison.py`，汇总为 `evaluation/evaluation_outputs/candidate-comparison-10554-20261116-run1/REPORT.md`，逐条复核见同目录 `manual_review.md`。新版两种 Partial 场景均未恢复 F3，审核固定检查为 4/6，仍未达标。

检查模块分两次模型调用：先从公开摘要和回答提取操作链与不可得信息，再决定追问或停止。引用使用程序编号的公开原文；检查器看不到隐藏复现、答案条件或 Proxy 私有规则。每次追问后重新检查，最多两次追问。

初始 Ideal/Partial 答案保持不变。Proxy 的动态回答使用 v2 模板模式；原有自由生成模式保留兼容。规则由模型语义匹配，正文及 fact IDs 由程序生成，仍需检查误选规则。

本目录沿用 v2 名称；实际四轮 v2–v5 版本由各自 manifest 和源码快照确定。

- 汇总：`evaluation/evaluation_outputs/clarification-10554-optimization-20261116/REPORT.md`
- 全部公开问答：同目录 `dialogues.md`
- 每轮原始请求、响应、准入标准：`evaluation/evaluation_outputs/clarification-10554-v{2,3,4,5}-20261116-run1/`
- 首个无 run1 后缀的 v2 目录仅为准备及入口导入失败记录，无模型调用。

运行新一轮（仓库根目录；输出目录必须全新）：

```bash
/home/rwh/miniconda3/envs/ambig-swe/bin/python -B -m evaluation.analysis.proxy_response_10554_v2.run_clarification --output-dir evaluation/evaluation_outputs/NEW_RUN --prepare-only
/home/rwh/miniconda3/envs/ambig-swe/bin/python -B -m evaluation.analysis.proxy_response_10554_v2.run_clarification --output-dir evaluation/evaluation_outputs/NEW_RUN
```

只运行澄清，不启动 CodeAct、Docker 或官方评分。预置 unknown 历史仅为合成检查场景；同一开发样本上的多轮调试不属于独立评测。
