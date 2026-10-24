# Triage Labels

技能用五个**规范化的 triage 角色**说话。本文件把这些角色映射到本仓库 issue tracker
里实际使用的标签字符串。

## Triage 五角色

| 技能里的标签 | 本仓库实际标签 | 含义 |
| ------------ | -------------- | ---- |
| `needs-triage`    | `needs-triage`    | 需要维护者评估该 issue |
| `needs-info`      | `needs-info`      | 等待报告者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格已完备，可直接交给自主代理实现 |
| `ready-for-human` | `ready-for-human` | 需要人工实现（判断、外部访问、设计取舍） |
| `wontfix`         | `wontfix`         | 不做处理 |

本仓库**沿用默认词汇**：左右两列完全一致，标签字符串与角色名逐字相同
（是连字符，不是下划线，也没有驼峰）。不要用下划线或驼峰改写这些名字，
否则 `/triage` 会把同义标签重复建一遍 —— 例如把 needs-triage 写成 needs_triage、
把 ready-for-agent 写成 ready_for_agent，都会在仓库里多出一个空标签。

状态流转：未打标签的 issue 通常先进 `needs-triage`；此后流向 `needs-info`、
`ready-for-agent`、`ready-for-human` 或 `wontfix`。报告者回复后 `needs-info`
回到 `needs-triage`。维护者可随时覆盖；遇到异常的流转要先确认再动手。

## Wayfinder 标签

供 `/wayfinder` 使用。**这些不是 triage 角色**，而是 map/工单的类型标记，
与上面的五角色分属不同语义，不要混用、也不要用于 triage 流转。

| 标签 | 用途 |
| ---- | ---- |
| `wayfinder:map`        | 整张 **map** 本身（一个 issue 承载 Notes / Decisions-so-far / Fog） |
| `wayfinder:research`   | 子工单：查文档 / 第三方 API / 知识库，产出一个决策所等待的**事实**（AFK） |
| `wayfinder:prototype`  | 子工单：做一个粗糙但具体的产物（提纲、草稿、桩、UI/逻辑代码）以提高讨论保真度（HITL） |
| `wayfinder:grilling`   | 子工单：对话式穷问，默认类型（HITL） |
| `wayfinder:task`       | 子工单：**必须先完成的手工活**，做完才能继续讨论（如开通服务、准备数据）（HITL 或 AFK） |

完整清单就是以上五个，不多不少。map 之外的四类都是 map 的**子工单**，
每张子工单只带其中一个 `wayfinder:<type>` 标签。
