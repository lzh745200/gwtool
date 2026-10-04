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

---

# 第二轮 / 第三轮：核心源码逐行深审（增量）

> 追加日期：2026-09-22
> 范围：`gwtool/db/`、`gwtool/core/`、`gwtool/ui/` 全部源码逐文件通读，
> 并对第一轮未覆盖的 `ui/` 三大文件与 `core/` 剩余模块派发专项深审。
> 结论：**再确认 4 条真缺陷（#9–#12）+ 6 处相邻问题，另推翻 7 条误报**。

## 六 第三轮增量缺陷 4 条（已修复，均红→绿验证）

以 `git show HEAD:<file>` 取出**修复前源码**实测复现，再验证修复版通过：

| # | 位置 | 契约 vs 实际 | 危害 | 红→绿证据 |
| --- | --- | --- | --- | --- |
| 9 | `db/dao.py` `max_doc_no_serial` / `max_reg_no_serial` | 契约「返回当前最大流水号」 vs 实际「返回**首个匹配**的流水号」 | 自动取号给出**已存在的号** → 台账出现重号 | 旧实现返回 `8`（期望 `12`） |
| 10 | `ui/compile_wizard.py` `_save_products_to_library` | 契约「保存后产物可用于登记台账」 vs 实际「`products` 显式传入时**不回填** `self._products`」 | **批量模式下产物永远进不了台账，且全程无任何提示** | 旧实现 `wiz._products == []` → `AssertionError` |
| 11 | `ui/feature_dialogs.py` `SnapshotsDialog._preview/_restore` | 契约「对快照操作」 vs 实际「`get_snapshot` 返回 `None` 时直接取下标」 | 快照被裁剪后预览/回滚**抛 `TypeError`**，功能中断 | 旧实现 `get_snapshot(None)` → `TypeError` |
| 12 | `ui/feature_dialogs.py` `InspectorDialog._export_report` | 契约「导出体检报告」 vs 实际「靠 `lbl_stat` 文案非空判断」 | 体检**失败后**仍可导出**空报告** | 旧实现无 `_has_result` → `AttributeError` |

### 6.1 #9 细节：为什么 `re.search` 是错的

发文字号形如 `〔2026〕第 12 号`。同一年度内台账里可能同时存在
`〔2026〕1 号` 与 `〔2026〕12 号`（例如历史数据导入 + 手工补录）。

```python
# 修复前：只取第一条匹配 —— SQL 的排序不保证，结果取决于物理行序
m = re.search(r"〕\s*(\d+)\s*号", r["doc_no"] or "")
if m:
    best = max(best, int(m.group(1)))
```

实际落库行序常是「先插入的在前」，于是 `1 号` 先被扫到、`12 号` 被忽略，
`max_doc_no_serial()` 返回 `1`，下一个自动号给出 `2` —— **与既有的 `2 号` 重号**。

```python
# 修复后：全量取号再求最大；单段异常只跳过该段，不报废整条记录
for tok in re.findall(r"〕\s*(\d+)\s*号", r["doc_no"] or ""):
    try:
        best = max(best, int(tok))
    except (TypeError, ValueError):
        continue
```

`try/int` 逐段包裹是刻意的：号段里若混进超长数字（`int()` 在 CPython 3.11 起
对超 4300 位数字会抛 `ValueError`），不能让**一条脏记录**把整张表的取号全废掉。

### 6.2 #10 细节：静默失败的完整链路

`_run_batch()` 在开始前清空 `self._products`，随后以临时列表调用：

```python
self._save_products_to_library([(p, Path(p).stem) for p in paths])
```

修复前该函数签名是 `(self, products=None)`，只有 `products is None` 分支才回填
`self._products`；显式传入时**不回填**。于是紧随其后的 `_register_from_compile()`
从**空的** `self._products` 出发，而 `_register_impl` 开头：

```python
docs = ...  # from self._products
if not docs:
    return False          # ← 静默 return，无提示、无日志
```

**批量模式（用户最常用的模式）下，汇编产物进不了发文字号台账，且界面不报错。**
这是一个"功能整体失效但看起来一切正常"的缺陷，危害高于崩溃类缺陷。

### 6.3 #12 细节：为什么不能用 `lbl_stat` 文案判断

`_run()` 一开始就写 `体检中…`，`_run_failed()` 写 `体检未完成` —— **两者都非空**。
以「文案非空」为判据，等于"只要点过开始体检就能导出报告"，
失败时导出的是**空报告**，用户拿去当体检结论使用。

改为显式状态标记（`__init__` 置 `False` → `_run` 重置 `False` → `_run_done` 置 `True`），
判据 `if not self._findings and not self._has_result:`。

## 七 相邻问题（同批修复，非独立缺陷）

### 7.1 `_reset_buttons` 遗漏复位「执行」按钮

`BatchCorrectDialog._apply()` 开头把 `btn_apply` 置灰防重入，但 `_reset_buttons()`
只复位了 `btn_preview`。用户在确认框点「取消」、或写回失败后，
**`btn_apply` 永久置灰 → 再也无法重试**，只能关掉对话框重来。

```python
def _reset_buttons(self):
    self.progress.setVisible(False)
    self.btn_preview.setEnabled(True)
    # 「执行」也必须复位：它在 _apply 一开始就被置灰，若只在成功路径
    # 复位，用户在确认框点"取消"或写回失败后就再也没法重试了
    self.btn_apply.setEnabled(bool(self._plans))
```

### 7.2 防重入必须早于模态确认框

`BulkReplaceDialog._apply` / `BatchCorrectDialog._apply` 原先把
`isRunning()` 检查放在 `QMessageBox.question()` **之后**。但模态对话框是
**嵌套事件循环** —— 它弹着的时候事件仍在派发、按钮仍可用：

- 连按回车/空格（`QMessageBox` 默认按钮是「是」）
- 或极快的双击

都能**重入 `_apply`**，弹出第二个确认框，最终起两个 worker 同时写库。

修复：把 `isRunning()` 检查与 `btn_apply.setEnabled(False)` 提到 `ask()` **之前**。

## 八 `core/ocr.py` 两处资源与参数缺陷

| 位置 | 问题 | 修复 |
| --- | --- | --- |
| `ocr_pdf` | `fitz.open(path)` 写在 `try` **之外**，而 `tmpdir` 的清理只写在 `finally` 里 → 加密/损坏/被占用的 PDF 直接抛时，**`finally` 从未进入，`gwtool_ocr_xxxx` 临时目录永久残留**（程序内没有任何地方扫它） | `doc = None` 提到外层，`fitz.open` 移入 `try`，`finally` 里 `if doc is not None: doc.close()` 后再 `rmtree` |
| `_tess_env` | 无条件设 `TESSDATA_PREFIX`，**覆盖用户环境原有的语言包路径**；且 `bundled_td` 算了却没用上 | 仅当实际选用的引擎**就是随包那份**时才设置，否则保留用户环境原值 |

```python
# _tess_env 修复后
bundled, bundled_td = _bundled()
if bundled and tess and Path(tess) == Path(bundled):
    td = _bundled_tess_data_dir()
    if td:
        env["TESSDATA_PREFIX"] = td
return env
```

## 九 误报表（7 条，经实测推翻，**代码未动**）

深审过程中收到过若干"高置信度"候选，**逐条实测后不成立**。如实列出，
避免后续会话重复投入：

| 候选 | 子代理主张 | 实测结论 |
| --- | --- | --- |
| `core/wordlist.py` 取 `dict(sqlite3.Row)` | 会抛 `TypeError`，应改 `tuple(row)` | ❌ **误报**：`sqlite3.Row` 实现 mapping 协议，`dict(row)` 实测可用；且既有 6 条用例（含 `test_same_pair_is_overwrite_not_add`）一直通过 |
| `db/dao.py` `dispatch_stats` 年度条件缺括号 | `LIKE ? OR LIKE ? GROUP BY k` 会让**年度过滤失效** | ❌ **误报**：`OR` 不跨出 `WHERE` 范围；实测新旧两条 SQL 结果**完全一致**。保留括号版作为**防退化护栏**并加注释，但**不记为缺陷修复** |
| `core/archive.py` 排序键遇 `None` 抛 `TypeError` | `Receive().id` 可能是 `None` | ❌ **误报**：`new` 对象的 `id` 实测是 `int 0`，排序键恒可比 |
| `core/backup.py` `except BaseException` 抓不到 `RuntimeError` | MRO 上 `RuntimeError` 不在 `BaseException` 之下 | ❌ **推理错误**：`RuntimeError` ⊂ `Exception` ⊂ `BaseException`，可捕获 |
| `core/backup.py` `_replace_with_retry` 异常链"会丢数据" | 重试耗尽后原文件已被移走 | ❌ **过度演绎**：逐句读后不成立（新文件已就位才移走旧的，失败路径有回滚） |
| `core/xlsx.py` `_cell` 数值分支写出 `<v>nan</v>` | `NaN` 会污染表格 | ⚠️ **理论成立但当前不可达**：调用方（`registry` / `receive` / `archive`）的数值**全部来自 SQLite 整数列**，不产生 `NaN`。未改代码 |
| `ui/editor_panel.py` `y += im.height()` | 字符串拼接性能问题 | ❌ **误报**：整数坐标累加，扫描器误判 |

> 方法论备注：**子代理的"高置信度"不等于事实**。本批 12 条候选里只有 4 条成立
> （命中率 1/3）。凡涉及"某 API 会抛异常""某 SQL 语义变化"的主张，
> **一律以本机实测为准**（`python -c` 直接跑），不接受纯推理结论。

## 十 第三轮影响范围与门禁

### 10.1 本轮改动文件（7 个）

```
gwtool/db/dao.py                re.search → re.findall 取最大；年度条件加括号（护栏）
gwtool/ui/compile_wizard.py     _save_products_to_library 回填 self._products
                                _register_impl 字号查重移到 dlg.exec() 之后、改用 final.doc_no
gwtool/ui/feature_dialogs.py    快照预览/回滚判空 ×2；InspectorDialog._has_result ×3
                                BulkReplace/BatchCorrect 防重入提前 ×2；_reset_buttons 补复位
gwtool/core/ocr.py              fitz.open 移入 try（tmpdir 泄漏）；_tess_env 加选用引擎判据
tests/test_deep_review_regression.py  7 类 69 项 → 12 类 80 项
```

### 10.2 新增回归测试 12 类 80 项（`tests/test_deep_review_regression.py`）

| 测试类 | 条数 | 覆盖 |
| --- | --- | --- |
| `TestAmountCapitalization` | 10 | #1 跨段补零 |
| `TestSimhashShortString` | 8 | #2 短串不同阶 |
| `TestDateCalendarValidation` | 14 | #3 / #4 无日历校验 |
| `TestDocTreeBlockDedup` | 9 | #5 level 未校验 |
| `TestBulkReplacePreviewConsistency` | 8 | #6 预览-执行不一致 |
| `TestPdfHandleLeak` | 5 | #7 渲染失败句柄泄漏 |
| `TestBuiltinDictProtection` | 6 | #8 内置词典误删 |
| **`TestSerialExtractionTakesMax`** | **4** | **#9 序号取最大** |
| `TestYearlyStatsBracket` | 4 | 护栏（**非缺陷修复**），含 `test_year_filter_also_matches_doc_no` |
| **`TestBatchProductsReachRegister`** | **1** | **#10 批量产物进台账** |
| **`TestSnapshotPrunedDoesNotCrash`** | **1** | **#11 快照裁剪后不崩** |
| **`TestInspectExportRequiresResult`** | **1** | **#12 体检失败禁导出** |

> 含 1 条边界项：`test_open_bad_pdf_tmpdir_cleaned` —— 用**损坏的 PDF**
> 触发 `fitz.open` 抛异常，断言系统临时目录下**不留** `gwtool_ocr_*` 残留。

### 10.3 门禁结果（第三轮全绿）

| 门禁 | 结果 |
| --- | --- |
| `ruff check gwtool/ scripts/ tests/ main.py` | **All checks passed!** |
| `pytest tests/ -q --timeout=120` | **1383 passed / 3 skipped / 1 warning**（305.53 s） |
| `scripts/e2e_check.py` | **29 项通过 / 0 项失败** |

> 通过数演进：&nbsp;799 → 1115（09-16）→ 1127（第一轮审查）→ 1365 → **1383（本轮）**

### 10.4 `_register_impl` 字号查重时机（补充说明）

第一轮之外的额外加固：原先把"发文字号是否已存在"的查重放在 `dlg.exec()`
**之前**，且只查 `rec.doc_no`（汇编封面带来的、通常为空的字段）。
而用户真正填字号是在**对话框内**（含「自动取号」按钮）。

`idx_dispatch_no` 是普通索引、库层允许同号，`find_dispatch_by_document` 又只按
`doc_id` 匹配 —— 于是"**在对话框里手填一个已存在的字号**"完全无人拦截。现在
查重移到 `dlg.exec()` 之后，并对 `final.doc_no` 判定（同文档自身除外）。

## 十一 本轮未验证 / 未做的事（如实说明）

| 项 | 状态 |
| --- | --- |
| 工具内置 LLM（deepseek）原生扫描 | **仍未执行**（密钥 402 余额不足，未充值） |
| 麒麟 V10 实机验收 | 未做（无真实机器）→ 改为 **CI 侧组装 + 产物冒烟** |
| 本报告所述改动是否已进安装包 | 见下文"推送与流水线"章节 |

---

# 第四轮：全量改动 delegate 深审（2026-09-23）

> **范围**：`ocr delegate preview` 认定的 workspace 模式 **34 个可审文件 / +7,923 行**
> （含 v7 段落参考、风格量化的全部新增模块与脚本、测试）。
> **方式**：`ocr review` 再次因 **DeepSeek 余额 402** 失败（`0 finding(s); 34 of 34
> selected item(s) failed`，LLM 阶段全部拿不到响应），改用工具自带的
> **delegate 模式**：`ocr delegate rule` 取出 11 大类规则原文，由宿主 agent 按规则
> 逐文件审查；**每条候选都写探针脚本实测复核**后才判定，不凭印象。
> **结论**：确认 **25 条**待处置项（16 条真缺陷 + 8 条死代码/一致性 + 1 条说明性），
> **已全部闭环**，新增回归测试 `tests/test_review_round4.py`（14 项）。

## 十二 真缺陷 16 条（全部已修 + 红→绿验证）

| # | 位置 | 契约 vs 实际 | 危害（实测证据） |
| --- | --- | --- | --- |
| 1 | `core/paragraph_ref.search_paragraphs_ex` | 「组内相对相关度」vs 实际**方向反了** | 写成 `1.0-(abs-lo)/span`，而 FTS5 的 bm25 是**负值、越负越相关** → 3 次词频的段 score=0.55 排第二、1 次词频的段 1.00 排第一（探针实测）。**用户看到的第一条永远是最不相关的** |
| 2 | `core/reference.lookup` | 同源同错（整篇检索） | 与 #1 同一行同源公式，`documents`/`phrases` 源排序全部倒置 |
| 3 | `core/paragraph_ref._ordinal_issues` | 「序号连续性」vs 期望值用 enumerate 下标 | 下标把被跳过的重复项也算进去 → 「一、二、二、三」里的「三」被报"应为 4"；用户照提示改，反把正确序号改坏 |
| 4 | `core/paragraph_ref._find_org_spans` | 「后缀锚定取单位名」vs 单字后缀无约束命中 | `局/部/厅/委/办/处/科` 命中大量常用词：「本次工作全部完成。」→ `{org}=本次工作全部`、「到处都要注意安全。」→ `{org}=到处`、「好处很多。」→ `{org}=好处`（探针实测） |
| 5 | `core/paragraph_ref._blocks_from_text` | docstring 自称"与 importer 同一口径" | 首行判据用**行下标 0**，正文以空行开头时第一个非空行永远拿不到 `heading`，同一篇文档两条路径给出不同段落类型 |
| 6 | `db/dao.purge_document` | 「彻底删除」vs 只删 4 张表 | 未级联 v7 的 `paragraphs`/`paragraphs_fts`/`fts_index_state` → 探针实测：purge 后段落行与 FTS 行全部残留，只有下一次 rebuild 才顺带回收 |
| 7 | `db/dao.max_doc_no_serial` / `max_reg_no_serial` | 注释称"异常长数字只跳过该段" | `\d+` 捕获的一定是纯数字，`int()` **永不抛**（那个 `except` 不可达）→ 30 个 9 被 int 成功，"已用最大序号"变成 10³⁰，自动取号随之给出天文数字 |
| 8 | `ui/dict_manager._del_dict_row` | 同文件 `_add_word` 会失效缓存 | 删除词条后漏调 `invalidate_cache()` → 纠错仍按旧词典放行，用户以为"删了没用"，**重启才生效** |
| 9 | `ui/main_window._build_menu` | 规格 §9.2 / 约束 C16「关闭后行为与改动前一致」 | 三个段落参考入口**不读** `para_align_enabled`/`para_gen_enabled`，开关只作用于右侧面板 → "关了还能点"；关掉 `para_ref_enabled` 后点开只剩死路提示 |
| 10 | `ui/feature_dialogs` 骨架文种下拉 | 「用户选中文种」vs `addItems` 不设 userData | 实测 `itemData(1) is None` → `currentData()` 恒为 None，派生与落库的 **kind 静默变空串**（下拉框形同装饰）。**本条为本轮新发现，比 #12 更根本** |
| 11 | `ui/feature_dialogs._rebuild_slots` | 规格 §7「每个槽位显示参考原值作为 placeholder」 | 模板一被编辑就重建槽位，而重建走文本正则、不带上 `example` → 探针实测 placeholder 只剩"请填写"，用户看不到参考里本来的单位名/日期 |
| 12 | `ui/feature_dialogs._load_saved` | 「载入已存骨架」vs 只恢复了 name/template/paras | 不回填文种 → 载入后微调再保存，`kind` 被静默覆盖为空串，文种标签无法从界面找回 |
| 13 | `ui/feature_dialogs._preview`/`_on_preview_done` | 「预览所见 = 实际所改」 | 条件快照在**完成时**才读控件，而 `_invalidate_plan` 在预览期间空转 → 期间改条件即可让"新条件改旧清单"重新成立 |
| 14 | `core/style_profile` 标题目标 | 「参数表是唯一真相」 | 硬编码 `4-12/14-24`，与 `style_data.TITLE_LENGTH` 及验收标准文档写的 `4-8/17-18` 都不一致 → 界面与文档互相矛盾 |
| 15 | `tests/test_deep_review_regression` 号段用例 | 「超长号段被忽略」 | 断言 `got >= 9` **无论是否忽略都成立**（恒真=假绿）；实测脏数据下 `best` 为 10³⁰ |
| 16 | `tests/test_ui_actions_smoke` | 「所有模态入口都被屏蔽」 | `QFileDialog` 的屏蔽只在 `win` fixture 内，而 `test_dialog_constructible`（参数化构造全部对话框）**不用它** → 新增的「保存报告…」一旦被点按钮式用例触发就会真弹框挂死全量 |

## 十三 死代码与一致性 8 条（已清理）

| # | 位置 | 处置 |
| --- | --- | --- |
| 17 | `core/paragraph_ref._cn_to_int`：`if s == "十"` **不可达**（`len==1` 分支已返回 10） | 删除该分支 |
| 18 | `db/dao.indexed_paragraph_docs`：零调用方（兜底实际走 `paragraph_index_hash`） | 删除 |
| 19 | `core/ocr._tess_env`：`bundled_td` 取而未用（真正用的是 `_bundled_tessdata()`） | 改为 `bundled, _ = _bundled()` |
| 20 | `core/simhash._trigrams`：重构后零调用方（`jaccard` 自调 `_ngrams`） | 删除 |
| 21 | `ui/reference_panel._results`：只写不读的**第二真相源**（与列表控件不同步） | 删除全部 5 处赋值 |
| 22 | `ui/reference_panel.refresh_switch_state`：零调用方，docstring 承诺不可达 | **接线**：由 `main_window._sync_para_features()` 调用（菜单与面板同步刷新） |
| 23 | `scripts/intro_pptx/build.js`：插入 S21 后 652 行起编号整体落后一页、**S22 出现两次** | 22 处编号 +1（复核无重复）；结尾硬编码 "DONE 43 slides" 改为按实际页数打印（实际 44 页） |
| 24 | `tests/test_deep_review_regression._only`：`lenient_ok` 死参数（调用处仍传 True/False，制造"松/严两条口径"的错觉） | 删除形参与全部实参 |

## 十四 说明性 1 条（如实标注，不改行为）

| # | 位置 | 处置 |
| --- | --- | --- |
| 25 | `core/style_data.py`：13 张参数表**未被任何代码路径读取**（`DEPTH_BY_LENGTH`/`ENDING_DIST`/`TONE_LADDER`/`AI_FLAVOR`/`SCORE_DIMS`/`STANCE_RULES` 等） | 它们在《公文风格量化指标与验收标准》里作参数对照、并属后续接入储备。**不删**（文档引用它们），改为在模块内**逐表标注接线状态**：哪些真被读取、哪些只是对照 —— 否则维护者会以为改了 `SCORE_DIMS` 就改了评分档位 |

## 十五 红绿验证（断言可失败性）

**纪律**：新增断言必须先在修复前的实现上确认失败。

| 方式 | 结果 |
| --- | --- |
| `git stash push -- gwtool/` 回退已跟踪文件的修复 | **11 failed / 3 passed**（3 条属未跟踪新文件，另行验证） |
| 手工回退 `paragraph_ref.py` 的三处（排序公式 / `expect` 判据 / org 白名单） | **3 failed / 11 passed**，且两条"别改坏"的反向护栏仍通过（未把修复做成"永远不报"） |
| 恢复后复跑 | **14 passed** |

## 十六 门禁结果

| 门禁 | 结果 |
| --- | --- |
| `ruff check .`（全仓库） | ✅ All checks passed |
| `pytest tests/ -q` | ✅ **1612 passed, 3 skipped**（含本轮新增 14 项） |
| `scripts/e2e_check.py` | ✅ **45 项通过，0 项失败** |
| `scripts/intro_pptx/build.js` | ✅ 重建 44 页，`paragraphs_fts`/schema v7/段落参考页均已在产物中校验 |

## 十七 本轮未验证 / 未做的事（如实说明）

| 项 | 状态 |
| --- | --- |
| 工具内置 LLM 原生审查 | **未执行**（DeepSeek 402 余额不足；delegate 模式下宿主 agent 逐条实测复核，覆盖面不依赖该 key） |
| 若本项目转商业用途 | 必须按 `third_party/` 两个 `NOTICE.md` 停止使用相应派生数据（`style_data.py` 的数值与规则派生自 PolyForm Noncommercial 上游） |
| 麒麟 ARM64 实机验收 | 仍未做（无真实机器） |


---

# 第五轮：数据层全生命周期探针 + 跨会话增量深审（2026-10-04）

> **范围**：v1.7 语法层落地后的合并基线（e7e45b8）。数据层跑了 29 项全生命周期
> 探针（软删/恢复/彻底删除无孤儿/重复重导入/取号/分类级联/重建一致/快照裁剪/
> 到期提醒/词表往返/备份往返/骨架/畸形查询/标签规约），**全部通过**；
> core 层与 UI 层按规则集复筛，另以"实测复现"为准绳确认 **3 条真缺陷**，已全部
> 修复并补 18 项回归（`tests/test_review_round5.py`）。全程未动 schema、未加依赖。

## 十八 真缺陷 3 条（红→绿）

| # | 位置 | 契约 vs 实际 | 危害（实测证据） | 修复 |
| --- | --- | --- | --- | --- |
| 1 | `core/compiler.py` `collect_sources` + `compile_docx` 标题对齐 | 「来源清单如实反映材料出处」vs 实际**解析失败的文件也被列入清单**；「material_titles 覆盖各材料标题」vs 实际 `zip(trees, titles)` 在坏文件被丢弃后**标题整体错位** | 探针实测：坏 docx 在前时，`collect_sources` 返回 2 条（含失败文件），生成的 docx 一级标题为 `['坏材料标题', …]` —— **好材料被冠以坏材料的标题**；失败本身零留痕，用户以为材料进汇编了。软删除文档还有第三重矛盾：正文进汇编、来源清单却跳过 | 新增 `load_trees_ex()`（返回 trees + failures + 与输入逐位对齐的 `slot_ok`），`load_trees` 保留原签名；`collect_sources` 同口径过滤（`extra_parsed` 参数避免重复解析）；`compile_docx` 标题按**输入槽位**对齐、失败逐条记 warning |
| 2 | `core/inspector.py` `_check_consistency` | 「同一单位简称/全称并存提示核对」vs 实体名被**前导连接词污染** | 本函数 docstring 的招牌用例「县应急局与县应急管理局联合执法」实测**零报告**：正则懒惰前缀从"与"字起匹配出「与县应急管理局」，带连接词的名字与简称做变体比对必然失败——而简称+全称并存最常见的出现场景恰恰是被「与/和/及」连在一起 | 确定性**前导连接字修剪**（闭合小集合 `_ENTITY_LEAD_TRIM`，只收连接词/介词/指代，不收开放类动词）；实测：与/和连接场景全部报出，"和县教育局"等地名前缀、口语句伪实体（群众办/服务全部）均不误报 |
| 3 | `core/fontcheck.py` `missing_fonts` | 「Qt 不可用时返回安全默认值」vs 实际**无 QApplication 时原生崩溃**（access violation，try/except 拦不住） | 实测：无 QApplication 的进程调用即**整个进程无栈死亡**（pytest 中表现为输出戛然而止、无 summary、exit 127）——任何纯逻辑调用方（脚本/无界面工具）走 `compile_docx` 都会触发 | 前置 `QApplication.instance() is None` 判空，无 app 直接返回 `[]`（与既有"探测失败返回安全默认值"纪律一致） |

**红→绿证据**：
- #1/#3：修复前实测复现见上表（探针输出与进程死亡现场）；修复后 `test_review_round5.py` 18 项 + 相邻 `test_compiler_sources.py` 全绿。
- #2：修复前招牌用例 0 finding，修复后与/和/分句三种场景全部报出，且 4 条"别报错"的反向护栏（不同主体/和县地名/伪实体/干净分句）仍通过。
- 顺带修正 `test_compiler_sources.py::test_marks_extra_paths_as_not_imported`：旧用例用 `b"x"` 造的假 docx **把缺陷固化成了断言**，已改用可解析文件并补反向护栏。

## 十九 误报澄清（实测推翻，防止后续重复投入）

| 候选 | 主张 | 实测结论 |
| --- | --- | --- |
| `style_profile.verdict` 未知文种抛 `KeyError` | 应返回空报告 | **设计如此**：docstring 明写"调用方已按约束提供选项，让越界立刻暴露"（fail-fast），非缺陷 |
| `style_profile.match_genre` 缺键 dict 抛 `KeyError: 'sent'` | 应容错 | 同上：合法输入只有 `analyze()` 的产出（恒含全键），外部凑的 dict 不是契约输入 |
| `_cn_to_int` 两实现不一致（paragraph_ref 1..99 vs inspector 更宽） | 应统一 | **域不同**：前者只判层级序号（公文二级序号不会过百，docstring 写明"不猜"），后者服务成文纪年；非漂移 |
| `toolbox.amount_to_cn` 抛 `ValueError` | 应返回原串 | UI 层（`editor_panel.op`）已 try/except 兜底并提示，非缺陷 |
| 全量 `rebuild_fts(incremental=False)` 后检索丢命中 | 索引被清空 | **探针 bug**：单独复核（probe6）证明增量/全量重建后 docs/FTS/state/检索结果完全一致；初测的"丢命中"是因为探针在更早步骤已把目标文档 purge |

## 二十 门禁结果（合并基线：e7e45b8 + 本轮 3 修复）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .`（全仓库） | ✅ All checks passed |
| `pytest tests/ -q` | ✅ **1745 passed, 3 skipped**（基线 1726 + 本轮 18 项回归 − 1 项重复计入；唯一一次失败是语料闸门逮住一处 cXc 触发表述（"方向"二字后紧跟着写"单向"的不规范连写），已改正文档措辞后复跑通过） |
| `scripts/e2e_check.py` | ✅ 50 项通过，0 项失败（v1.7 新增 5 项后基线） |
| 数据层生命周期探针（本轮新增，临时库） | ✅ 29 项通过，0 项失败 |
| `scripts/intro_pptx/build.js` | ✅ 重建 45 页，页码无重复，v1.7/第五轮内容已核验在产物中 |

## 二十一 本轮未验证 / 未做的事（如实说明）

| 项 | 状态 |
| --- | --- |
| 工具内置 LLM 原生审查 | **未执行**（DeepSeek 402 余额不足，与前几轮同因） |
| core 层子代理深审 | 派发后运行过久（>12h 无收敛）被终止，其两条中间候选（编译链口径矛盾、机构正则误命中）由主会话**重新实测**后确认为上表 #1/#2 并修复；"误报面为零"亦以实测为准 |
| 麒麟 ARM64 实机验收 | 仍未做（无真实机器） |
