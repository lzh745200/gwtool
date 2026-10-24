# Issue tracker: GitHub

本仓库的 issue 与规格存放在 **GitHub Issues**：`lzh745200/gwtool`。
所有操作走 `gh` CLI（在克隆目录内运行会自动识别仓库，无需显式指定 `--repo`）。

## 约定

- **建 issue**：`gh issue create --title "..." --body "..."`。多行正文用 heredoc。
- **读 issue**：`gh issue view <number> --comments`，用 `jq` 过滤评论，同时取 labels。
- **列 issue**：
  `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`
  配合 `--label`、`--state` 过滤。
- **评论**：`gh issue comment <number> --body "..."`
- **打/摘标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

## Pull requests as a triage surface

**PRs as a request surface: no.**

本仓库不接受把外部 PR 当作功能请求面来 triage。若将来要打开，把上面一行改成
`yes`，`/triage` 会读取该标记；届时 PR 与 issue 共用同一套标签与状态，
用 `gh pr view` / `gh pr list` / `gh pr comment` / `gh pr edit --add-label` /
`gh pr close` 等等价命令操作。

GitHub 的 issue 与 PR 共用一个编号空间，一个裸 `#42` 可能是任一种：
先 `gh pr view 42`，失败再 `gh issue view 42`。

## 技能说"发布到 issue tracker"时

建一个 GitHub issue。

## 技能说"取回相关工单"时

执行 `gh issue view <number> --comments`。

## Wayfinding 操作

供 `/wayfinder` 使用。**map 是一张 issue，工单是它的子 issue。**

- **Map**：单个带 `wayfinder:map` 标签的 issue，正文承载
  Notes / Decisions-so-far / Fog 三段。`gh issue create --label wayfinder:map`。
- **子工单**：作为 map 的子 issue 关联（走 `gh api` 的子 issue 端点）。
  若子 issue 尚未启用，则把子工单加进 map 正文的任务列表，并在子工单正文顶部写
  `Part of #<map>`。标签用 `wayfinder:<type>`
  （`research` / `prototype` / `grilling` / `task`）。被认领后指派给驱动该 map 的开发者。
- **阻塞关系**：用 GitHub **原生 issue 依赖**（UI 可见，是权威表示）：
  `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，
  其中 `<blocker-db-id>` 是被阻塞者的**数据库 id**
  （`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，**不是** `#number` 也不是 `node_id`）。
  GitHub 用 `issue_dependencies_summary.blocked_by`（只算未关闭的阻塞者）给出实时门禁。
  若依赖功能不可用，退化为在子工单正文顶部写 `Blocked by: #<n>, #<n>`。
  当所有阻塞者都关闭时，该工单解除阻塞。
- **frontier 查询**：列出 map 下未关闭的子 issue，剔除有未关闭阻塞者的
  （`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 行里还有未关闭 issue）
  以及已有 assignee 的；按 map 顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`，本次会话的第一次写操作。
- **解决**：`gh issue comment <n> --body "<答案>"` → `gh issue close <n>` →
  把上下文指针（要点 + 链接）追加到 map 的 Decisions-so-far。
