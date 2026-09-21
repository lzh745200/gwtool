# open-code-review 全项目代码审查报告

> 审查对象：公文汇编助手（`C:/gwtool`）全项目源码
> 审查工具：**alibaba/open-code-review**（`open-code-review v1.12.5`，windows/amd64）
> 审查日期：2026-09-21
> 版本基线：**v1.6.0（本次不更新版本号）**

---

## 〇 工具使用情况（如实说明）

| 项 | 状态 |
| --- | --- |
| 工具可用性 | ✅ 已安装（npm 全局命令 `ocr`），版本 `v1.12.5 (189be5b02)` |
| 扫描范围 | 由工具自身 `ocr scan --preview` 给出：**161 文件 / 39,358 行** |
| 内置规则集 | ✅ 经 `ocr delegate rule` 取得，**11 大类** |
| 工具内置 LLM 分析 | ❌ **未能使用** |

**为什么内置 LLM 分析没用上**：本机 `~/.opencodereview/config.json` 配置的 provider 为
`deepseek`（模型 `deepseek-flash`），密钥有效但**账户余额为 0**：

```
$ ocr llm test
Error: llm request failed: POST "https://api.deepseek.com/chat/completions":
402 Payment Required {"message":"Insufficient Balance",...}
```

工具内置 26 个 provider（openai / anthropic / dashscope / kimi / 火山 / 硅基流动…），
但本机只配置了 deepseek 一把密钥。

**采用的替代路径**：该工具自带的 **delegate 模式**（`ocr delegate`，官方描述为
"Output review spec for host-agent delegation (**no LLM required**)"）——
由工具产出**确定性流水线 + 规则集**，由宿主 agent 充当 LLM 完成推理。
本报告即按此模式产出，**不是绕开工具，而是使用它为此场景预留的路径**。

> 如需用 deepseek 原生跑一遍 `ocr scan`，充值该密钥后即可，命令为
> `ocr scan --format json --audience agent`。

---

## 一 审查范围

`ocr scan --preview` 给出的可审范围（工具自动排除 `dist/`、`build/`、`.venv/`、
`packs/`；`.md`、`.bat`、`.iss`、`.pptx`、`.doc` 因扩展名/二进制不支持而排除）：

| 目录 | 文件数 | 说明 |
| --- | --- | --- |
| `gwtool/` | **76** | 产品源码（核心，本次重点） |
| `tests/` | 62 | 测试 |
| `scripts/` | 18 | 构建与自检脚本 |
| 仓库根 | 4 | `main.py` 等 |
| `.github/` | 1 | CI 工作流 |
| **合计** | **161 文件 / 39,358 行** | |

**优先次序**（按本次要求"优先核心源码"）：`gwtool/` → `scripts/` + 根 + `.github/` → `tests/`。

---

## 二 工具的规则集（11 大类）

取自 `ocr delegate rule`，规则组为 `system / **/*.{py,pyi,ipynb}`：

| 类别 | 关注点 |
| --- | --- |
| Content（总纲） | **宁可漏报不可误报**：仅在有把握时提出问题；安全与正确性问题视为阻断级，风格问题为非阻断 |
| 拼写错误 | 仅报声明处，不报引用处 |
| 死代码 | 不可达分支、未读取的变量/导入/参数、大段注释掉的代码 |
| 可变默认参数与共享状态 | `def f(x=[])`、类级可变属性、模块级可变全局、闭包捕获循环变量 |
| 边界与边缘情形 | 空输入、越界、`None` 传播、浮点 `==`、整除截断、除零、异构集合、字典缺键 |
| 异常处理 | 裸 `except`、过宽 `except`、**捕获后静默丢弃**、丢失原始回溯、过宽 `try`、`assert` 做运行期校验 |
| 身份与相等比较 | `is` 比较字面量、`==` 比较 `True/False` |
| 资源管理 | 未用 `with`、绕过上下文管理器、`finally` 清理不完整 |
| 性能 | 循环内字符串 `+=`、明确数据规模与热路径后再报 |
| 并发与异步 | 线程/异步竞态、共享状态 |
| 安全敏感代码 | 注入、路径穿越、敏感信息泄露 |

---

## 三 审查结果

### 3.1 候选收敛过程

```
原始候选                        2,368 条
  ↓ 范围校正：assert 规则在测试里本就是正确写法 → 排除 tests/
  ↓ 判据校正：修正扫描器把「整数 +=」误判为「字符串拼接」
生产代码候选                       82 条
  ↓ 逐条读上下文甄别
├─ 合理降级（设计如此，不改）       62 条
├─ 确认缺陷（已修）                 12 条
└─ 误报（不改）                      8 条
```

### 3.2 确认缺陷 12 条（已全部修复）

**共同特征：失败被吞掉，但代码继续做出"成功"的承诺。**

| # | 位置 | 危害 | 修复 |
| --- | --- | --- | --- |
| 1 | `core/backup.py` 恢复前自动备份 | 该步是"恢复失败时的退路"。失败被静默 → **用户以为有安全网，实际没有** | 记入 `RestoreReport.warnings` + 日志，UI 点名 |
| 2 | `core/backup.py` 模板回写 | 迁移包写了却不还原，用户不知 | 同上 |
| 3 | **`core/backup.py` 附件还原** | **附件还原失败被吞成 `restored = 0`，而报告仍是 `ok=True`** → 用户以为附件都回来了。**附件是公文原件，丢了就是丢了** | 同上（最重的一条） |
| 4 | `core/batch.py` 批量纠错快照 | 快照是"改错了能回退"的唯一依据。失败仍继续改，等于**撤掉安全网再动手**，且用户不知 | 写入 `result.failures`："这篇无法用「历史版本」退回改前状态" |
| 5 | `ui/feature_dialogs.py` 批量替换快照 | 同型 | 写入结果元组的说明位 |
| 6 | `core/exporter.py` 原件读取 | 原件读不出来时**静默退化为纯文本**，而用户以为移交包里是原件（原格式与批注全丢） | 加日志留痕 |
| 7 | `core/enhance_pack.py` 旧布局迁移 | 迁移失败 → 表现为"增强包明明导入了却用不了"，且**没有任何线索** | 加日志留痕 |
| 8 | `core/csc_gec.py` 开关持久化 | 开关没写进库 → 重启后回到旧值。**界面本次会话内是生效的，属最难排查的不一致** | 加日志："重启后将回到原值" |
| 9 | `core/reminder.py` 开关持久化 | 同型 | 同上 |
| 10 | `core/dbhealth.py` 自检日期 | 记录失败 → "下次是否该自检"判断失真 | 加日志留痕 |
| 11 | `ui/feature_dialogs.py` 增强包导入后自动开启 | 自动开启没存下来 → 用户下次启动发现开关又关了，以为导入没生效 | 加日志留痕 |
| 12 | `ui/feature_dialogs.py` 增强包卸载后关闭 | 同型 | 同上 |

**新增载体**：`RestoreReport.warnings: list[str]`
—— 恢复过程里"没成功、但没到要让整次恢复失败"的步骤统一收集，
由 UI（`ui/main_window.py` 恢复入口）在结果对话框逐条点名。

### 3.3 判定为"合理降级"的 62 条（不改）

这些是**刻意设计**，语句本身或紧邻注释已说明意图。按规则集"宁可漏报不可误报"的要求保持原样——
一刀切改为 `raise` 会引入新的使用障碍：

| 位置 | 为什么合理 |
| --- | --- |
| `core/corrector.py:73` | 注释明写"数据库不可用时退化为纯内置数据"——有备用数据源 |
| `core/csc_gec.py:593` | numpy 失败落回纯 Python，注释明写"语义不变" |
| `core/backup.py:_unlink_quietly` | 函数名即意图：尽力删除，失败无害 |
| `core/pdfrender.py` / `core/watermark.py` | 临时文件清理，且紧接 `raise`（原异常照抛） |
| `core/compiler.py` / `core/parsers/doc_parser.py` | COM 资源释放：任一步失败都要继续退应用，否则泄漏更多 |
| `logs.py`（共 11 处） | **日志自身失败绝不能拖垮主程序**——这是该模块的设计前提 |
| `ui/editor_panel.py` | 注释明写"画线失败绝不影响编辑" |
| `ui/workers.py:27` | 注释明写日志失败后仍走 `traceback.print_exc()` |
| 其余 `stat`/`mtime`/样式内省类 | 探测失败返回安全默认值 |

### 3.4 误报 8 条（不改）

| 位置 | 为什么是误报 |
| --- | --- |
| `scripts/smoke_real_packs.py`（5 处 assert） | 自检脚本，非生产代码；`assert` 在此是正确写法 |
| `ui/editor_panel.py` `y += im.height()` | 整数坐标累加，被扫描器误判为字符串拼接 |

---

## 四 影响范围

**改动文件（14 个，全部为生产与测试代码，无新增依赖、无 schema 变更）**：

```
gwtool/logs.py                 脱敏过滤器 + 崩溃摘要（不含正文）+ shutdown 清理 handler
gwtool/core/diagpack.py        诊断包日志行脱敏 + L4/L5 错误串脱敏
gwtool/core/backup.py          RestoreReport.warnings + 3 处静默点改为可见
gwtool/core/batch.py           快照失败记入 failures
gwtool/core/csc_gec.py         开关持久化失败留痕
gwtool/core/reminder.py        模块日志接入 + 开关持久化失败留痕
gwtool/core/dbhealth.py        自检日期记录失败留痕
gwtool/core/exporter.py        模块日志接入 + 原件读取失败留痕
gwtool/core/enhance_pack.py    模块日志接入 + 旧布局迁移失败留痕
gwtool/ui/main_window.py       恢复结果对话框展示 warnings
gwtool/ui/feature_dialogs.py   模块日志接入 + 3 处静默点改为留痕
tests/test_logs_privacy.py     （新增）隐私泄露专项断言
tests/test_logs.py             异常消息不再进日志（行为变更同步）
tests/test_diagpack.py         日志尾部超长行脱敏（行为变更同步）
```

**对用户可见的行为变化（两处，均为"把承诺变成事实"）**：

1. **诊断包与日志不再包含公文正文**
   原先 `redact()` 定义在 `logs.py:40` 却**全仓 0 处调用**，诊断包把整份原始日志塞进 zip，
   而 UI 承诺"不含任何公文正文"。现在：日志**入队前**即脱敏（参数超 64 字压成长度标记）、
   崩溃摘要只留「异常类型 + 消息长度 + 末帧位置」、诊断包再对超 200 字的日志行兜底脱敏。

2. **恢复结果会如实列出降级项**
   原先"恢复前自动备份没做成""附件没还原成功"都表现为"恢复成功"。
   现在会在结果对话框中逐条点名（`恢复成功` 仅在所有步骤都真正成功时出现）。

**不受影响的范围**：数据库 schema 未变（仍是 v4）、未新增任何第三方依赖、
纠错引擎 L1–L5 逻辑未动、构建链与打包参数未动、版本号未变。

---

## 五 验证结果

### 5.1 三条门禁（全部全绿）

| 门禁 | 结果 |
| --- | --- |
| `pytest tests/ -q` | **1127 passed / 3 skipped / 0 failed**（311.6 s） |
| `ruff check .` | **All checks passed** |
| `scripts/e2e_check.py` | **20 项通过 / 0 失败** |

### 5.2 修复落地核验（按修复内容逐条搜索，不依赖行号）

```
✓ backup.py  恢复前自动备份      ✓ csc_gec.py     开关持久化
✓ backup.py  模板回写            ✓ reminder.py    开关持久化
✓ backup.py  附件还原            ✓ dbhealth.py    自检日期
✓ batch.py   快照                ✓ feature_dialogs.py 导入后开启
✓ feature_dialogs.py 批量替换快照 ✓ feature_dialogs.py 卸载后关闭
✓ exporter.py 原件读取           ✓ enhance_pack.py 旧布局迁移
落地 12 处 / 缺失 0 处
```

`RestoreReport.warnings` 贯通核验：声明 → 3 处追加 → 传入报告 → UI 消费，链路完整。

### 5.3 隐私修复的专项断言（新增 `tests/test_logs_privacy.py`）

| 断言 | 覆盖的泄露渠道 |
| --- | --- |
| 长参数被压成长度标记 | 带参日志 |
| 短参数仍保留（证明没把排障信息一并抹掉） | 反向验证 |
| f-string 长消息整体隐去 | 无参日志（截断不足以防泄漏） |
| 异常消息不进日志、但类型必须保留 | 崩溃通道 |
| 诊断包 zip 内**所有成员**均不含正文标记 | 诊断包通道 |
| 脱敏后仍保留版本/能力/行数 | 证明"能排障"没被一起修没 |
| L4/L5 错误串已脱敏 | `capability_info` 通道 |

> 说明：日志脱敏采用**长度启发式**（参数阈值 64 字）。低于阈值的短片段会被保留
> —— 这是为保留路径/计数/状态码等排障必需信息而做的**刻意取舍**，
> 已在 `logs.py` 模块文档中写明。

### 5.4 本次未验证的部分（如实说明）

| 项 | 状态 |
| --- | --- |
| 工具内置 LLM（deepseek）原生扫描 | **未执行**（402 余额不足） |
| `tests/` 目录的规则级深审 | 仅做了范围校正与误报剔除，未逐文件深审 |
| 打包产物冒烟（`smoke_dist.py`） | 本次未重跑（改动不涉及打包链） |
| 麒麟 ARM64 实机验收 | 未做（需真实机器） |
