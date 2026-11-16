# 10554 Q2 Ideal 与 Partial 回答及追问规则

用途：为 Motivation 案例构建可审阅、可冻结的回答对，验证 Agent 能否补齐精确复现信息。以下保留最初的设计和预期规则。

实际运行已于 2026-11-16 完成，两组均经过检查、追问、CodeAct 和官方评测。结果与预期不同，见[实际结果报告](../../evaluation_outputs/answercheck-10554-pair-20261116/REPORT.md)及[实际问答](../../evaluation_outputs/answercheck-10554-pair-20261116/dialogues.md)；不能把下文的预期示例当作已测得结果。

## 设计边界

- Ideal 完整提供当前复现问题所需、来源可支持的操作链；不是提供完整 issue、确定根因或替 Agent 修复。
- Partial 只提供真实的查询构造信息和首次正常显示，不提供精确派生调用及其后再次显示的具体片段；不声称这已是完整复现。
- 这是一对新构造的受控回答，不是逐字重放历史 Proxy 的回答。
- 两组共用同一段构造说明、首次显示内容、错误消息与缺少模型/数据库配置的说明；Ideal 仅追加触发链。
- 原文查询构造括号不闭合、完整模型也未提供；本轮共用自然语言说明构造，只展示已存在 qs 上的 REPL 操作，不单方面修好 Ideal 的代码。
- 保留交互显示 qs 的形式，不用 list(qs) 替代，不宣称未执行的求值方式彼此等价。
- 错误消息在两组初始回答中都公开，避免把错误诊断信息和操作链恢复混为一个变量。

## 已知信息与真正缺口

当前公开摘要如下：

> There is an issue with combining querysets using union operations in Django, particularly when ordering is applied to derived querysets. This results in a programming error during query execution, as the ordering position is not included in the select list. The problem arises when attempting to re-evaluate the original queryset after applying an order_by operation, leading to a traceback error. The issue may be related to a previously reported problem.

因此 Agent 在提问前已知道“派生查询排序操作之后，重新求值原查询会报错”。不能把这段大体机制当成 Partial 隐藏的未知事实；真正缺口是 values_list 的精确调用参数，以及完整具体 REPL 操作链。

| 事实 | 内容 | Ideal 初始回答 | Partial 初始回答 |
| --- | --- | --- | --- |
| F1 | union 查询的构造信息 | 提供 | 提供 |
| F2 | 首次交互显示原 qs 正常 | 提供 | 提供 |
| F3 | 精确的派生查询调用 | 提供 | 不主动提供 |
| F4 | 派生操作后再次显示原 qs 才报错 | 提供 | 不主动提供 |
| F5 | 精确错误消息 | 提供 | 提供 |
| F6 | 原讨论中的复制假说及其保留意见 | 不主动提供 | 不主动提供 |
| F7 | 可能相关的历史 ticket | 不主动提供 | 不主动提供 |

F4 的大体机制已在摘要中公开，不能作为全新恢复事实计分。主要恢复目标是 F3 的 `.order_by().values_list('pk', flat=True)`，并检查它是否被放在“首次正常显示→派生操作求值→再次显示原 qs 失败”的正确位置。

## 固定初始问题

Can you provide a minimal code example that reproduces this issue with union operations and ordering?

## Ideal 初始回答

The report uses a Dimension queryset named qs, combining the pk__in=[10, 11] and pk__in=[16, 17] queries with union, with ordering on the "order" field. The reported exception is django.db.utils.ProgrammingError: ORDER BY position 4 is not in select list.

The source does not provide the Dimension model definition or a standalone database setup. The following is an excerpt of the reported interactive session, using that existing qs:

```pycon
>>> qs
<QuerySet [<Dimension: boeksoort>, <Dimension: grootboek>, <Dimension: kenteken>, <Dimension: activa>]>
```

Next, evaluate the derived queryset, then display the original qs again:

```pycon
>>> qs.order_by().values_list('pk', flat=True)
<QuerySet [16, 11, 10, 17]>
>>> qs
[breaks]
```

The exception above occurs on that second display of the original qs, after the derived-queryset operation. The first display of qs succeeds.

## Partial 初始回答

The report uses a Dimension queryset named qs, combining the pk__in=[10, 11] and pk__in=[16, 17] queries with union, with ordering on the "order" field. The reported exception is django.db.utils.ProgrammingError: ORDER BY position 4 is not in select list.

The source does not provide the Dimension model definition or a standalone database setup. The following is an excerpt of the reported interactive session, using that existing qs:

```pycon
>>> qs
<QuerySet [<Dimension: boeksoort>, <Dimension: grootboek>, <Dimension: kenteken>, <Dimension: activa>]>
```

## Partial 的追问信息释放表

表中的例子是人工构造的预期行为，不是已经测得的模型输出。按语义匹配，接受中英文和改述，不以关键词或第几轮触发。

| 规则 | 当前追问针对什么 | 可补充事实 | 不应额外补充 |
| --- | --- | --- | --- |
| R1 询问从正常到失败的完整顺序 | 询问首次成功后发生什么、到报错之间的中间操作、或明确要求包含派生操作和再次求值的完整时间线。 | F2, F3, F4 | 不追加复制假说、修复建议、模型定义或数据库版本。 |
| R2 只询问派生查询的精确操作 | 要求派生 queryset 的调用、values_list 参数、是否清除排序/只取主键；未询问完整报错时间线。 | F3 | 不自动追加再次显示原 qs 的步骤；若同条问题也问报错时机，再匹配 R3。 |
| R3 只询问错误发生时机和对象 | 询问第一次还是第二次求值、原 qs 还是派生 qs 报错；没有要求派生调用的具体代码。 | F2, F4 | 不自动透露 values_list 的精确参数；这条回答的大体时间线在摘要中已经可见。 |
| R4 只询问错误消息 | 仅要求异常类型、错误文字或当前 SQL 错误。 | F5 | 不因为已经进入第二轮就补送操作链。 |
| R5 来源未明确给出的环境或模型定义 | 询问确切 Django/数据库版本、数据库后端名称、完整 Dimension 模型或独立建表/数据准备脚本。 | 无新事实 | 不根据 traceback 的 Python 版本或错误风格猜成已确认环境；只回应当前询问的未知子项。 |
| R6 只询问已给出的查询构造 | 询问 union 的两个 pk 条件、模型名称或排序字段。 | F1 | 不额外给出派生查询和触发时间线；原始构造括号不闭合，不能伪造为已验证脚本。 |
| R7 泛化请求或重复初始问题 | 只说更多细节、请重说、再给一次最小复现，未指明中间操作、派生查询或报错时机。 | 无新事实 | 不固定在第2轮或第3轮自动补齐。只重述公开内容，不声称这是完整复现，也不暗示自己刻意隐瞒。 |
| R8 直接询问根因或修复方向 | 明确询问是不是复制/共享状态造成，或原讨论有没有提出修复思路。 | F6 | 不生成补丁、不把假说当最终方案；没有被问到就不提供这段额外实现线索。 |
| R9 历史关联 | 只询问相关 issue/ticket 的编号。 | F7 | 不访问或编造外部 ticket 内容。 |
| R10 纠正与来源不符的前提 | 当前问题明确假定首次显示原 qs 就失败，或派生查询本身就是报告的失败点。 | F2, F4 | 纠正时机，不无条件追加精确派生代码；若当前问题也问了该操作，再匹配 R2。 |

若当前问题包含多个明确请求，合并相关规则；完整顺序问题用 R1，只问调用用 R2，只问报错对象/时机用 R3，泛化重复才用 R7。已公开事实保持可用，重复提问不能触发遗忘。尚未覆盖的来源内问题只回答所问内容；来源没有的内容明确未知。

## 两条可审阅的对话路径

### 有效追问补齐操作链

初始：交付上面的固定 Partial。

Agent：第一次正常显示以后，到报错之间还执行了哪些操作？

Proxy（R1）：First displaying qs succeeds. Then evaluate qs.order_by().values_list('pk', flat=True). Display the original qs again; that second display fails with the reported error.

结果：F3 的精确调用及其在触发链中的位置得到补充。是否随后被正确复现和用于修复，需要后续实验验证。

### 无关追问不自动补齐

初始：交付相同的固定 Partial。

Agent：具体错误消息是什么？

Proxy（R4）：The reported error is django.db.utils.ProgrammingError: ORDER BY position 4 is not in select list.

即使第二、第三次仍问同一个错误消息，也不能因为轮次增加自动送出 F3。这里只重复已公开信息，不算有效的信息恢复。

## 后续 Proxy prompt 与接入约定

fixture.json 的 followup_prompt_templates 包含 Ideal、Partial 两份英文模板。模板占位符由未来宿主填入 JSON 转义后的当前问题、公开历史、私有来源，以及 Partial 的规则表。固定初始回答直接从文件读取，不交给模型随机生成。

- Online Agent/充分性判断器只看任务摘要和公开问答；完整 fixture 及私有事实表仅供 Proxy/离线审计。
- 模型返回 JSON；宿主仅将 answer 转给 Agent，规则号和事实号只保存在私有日志。
- 非法 JSON/空答案不更新公开历史；同轮最多额外重试两次，失败后记录协议错误。
- Partial 真实性复核只检查事实与允许公开范围，不能把遗漏自动补齐。
- 后续仍沿用每题最多两次追问、最多五道初问的预算；本轮未执行这些对话。
- 对根因问题保留 Looks like 的不确定性和后期“尚未找到正确策略”的说明；没有询问就不主动提供复制假说。

## 校验结果与尚未验证的部分

已校验 11 条事实摘录存在于原始 issue/Hints，两组共同前缀完全相同，初始事实集合差异仅为 F3/F4，Partial 不含精确 values_list 调用。

已准备 22 个追问/边界情景及预期释放事实，包括中文改述、复合问题、重复问题、进度说明和越权索取私有信息。结构校验通过只说明材料自洽，不说明动态 Proxy 已能稳定按规则回答。

尚未验证：模型语义路由稳定性、拒绝额外透露信息的效果、Django 操作链在实际环境中复现、补齐信息是否改善修复结果。完整数据库版本、模型定义和独立准备脚本均没有来源，不能由 Ideal 凭空补出。

## 文件

- ideal_response.txt / partial_response.txt：实际可交付 Agent 的固定英文回答。
- fixture.json：公开任务、回答、私有事实及其证据、释放规则、prompt 模板、追问预期和哈希；不能整个传给 Agent。
- build_fixture.py：权威内容及可重复导出/来源校验脚本。

重新导出：

```bash
/home/rwh/miniconda3/envs/ambig-swe/bin/python -B /home/rwh/Ambig-SWE-Origin/InteractiveSWEAgents/evaluation/analysis/proxy_response_10554_v1/build_fixture.py
```

这些回答的充分性限于当前 Q2 及所给来源；不代表完整任务已无其他需求缺口。
