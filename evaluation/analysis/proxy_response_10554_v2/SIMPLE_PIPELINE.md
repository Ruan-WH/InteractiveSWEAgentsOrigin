# 当前选出的最小候选

**一个检查模型，每次检查调用一次。** 当前已验证配置为 `hidden_deepseek_flash`；Qwen 的初始强制澄清、Proxy 和修复配置保留。

```text
公开摘要＋原问题和回答
        ↓
一次检查：回答是否补齐了原问题要求的信息？
        ├─ 给出来源证据，停止
        └─ 提出一个遗漏问题 → 补答加入历史 → 再检查
```

程序只负责证据源覆盖、标准未知回复的记忆、重复和最多两次追问的限制。操作链问题与其他问题由公开英文摘要选择对应提示，没有独立审核器或缺口管理模型。

入口：`evaluation/utils/simple_clarification.py` 的 `run_simple_clarification(llm, summary, exchanges, proxy, log_path)`。`llm` 使用 JSON 模式适配器，参考 `run_simple_exploration.py` 的 Model。返回记录可交给现有 `checked_solving_instruction`。尚未切换默认评测入口。

复现实验：

```bash
python -B -m evaluation.analysis.proxy_response_10554_v2.run_simple_exploration --output-dir evaluation/evaluation_outputs/NEW_RUN --prepare-only
python -B -m evaluation.analysis.proxy_response_10554_v2.run_simple_exploration --output-dir evaluation/evaluation_outputs/NEW_RUN --split fresh --policy focused_answer --llm-config hidden_deepseek_flash --repeats 3
```

使用项目原 conda 环境。已有阶段不允许重抽；实际冻结的输入与请求见 `simple-focused-final-20261116`。

结果和失败边界：`evaluation/evaluation_outputs/simple-followup-search-20261116/REPORT.md`。仅为小规模候选验证；未测试修复率，未证明可发现所有未提问的需求。实验性失败变体保留在 `simple_followup.py` 与各源码快照，运行入口固定只使用最终候选。
