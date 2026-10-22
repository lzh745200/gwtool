# v1.9 只读审计 · 02 文字纠错全链路

> 审计对象：`<仓库根>` 公文汇编助手 v1.8.0
> 审计方式：只读 + 独立探针（未改任何源码；未跑全量 pytest/e2e/打包）
> 环境：`.\.venv\Scripts\python.exe` = Python **3.13.14**（项目目标 3.9，见 §6 待验证 T-1）、
> `PYTHONIOENCODING=utf-8`、`QT_QPA_PLATFORM=offscreen`、数据目录重定向到 `%TEMP%\gwtool_audit_v19`
> 探针：`doc/_audit_v19/probes/p01..p14`（按协议审计结束后已删除，关键命令已在文中给出可复跑形式）

---

## 1. 功能清单与入口

### 1.1 引擎（纠错流水线）

| 层 | 能力 | 入口 | 关键实现 |
|---|---|---|---|
| L1 | 精确词表，最长优先 + 词边界门控 | `corrector.check_text`→`_check_exact` | `core/corrector.py:271`、桶 `:252`、门控 `:348` |
| L1 数据 | 内置精标 448 对 + 库内生成对 39 504 + 机构沿革 52 | `_load_patterns` | `core/corrector.py:66`、`core/corrector_data.py:13`(精标)/`:515`(机构)。规则表规模：上下文 49 条、标点 10 条、数字 21 条 |
| L2 | 上下文规则（截止/截至、反应/反映…） | `_check_regex_group` | `core/corrector_data.py:589` |
| L2' | 标点规则（可自动替换） | 同上 | `core/corrector_data.py:710` |
| L2'' | 数字用法（提示类） | 同上 | `core/corrector_data.py:737` |
| L2.5 | 语义冗余 / 关联词 / 搭配 / 表达提示 / 成对标点 | `grammar_rules.all_hits` | `core/grammar_rules.py:244` |
| L3 | 重复字（同字连 + 间隔重复） | `repeat_rules.check_repeat` | `core/repeat_rules.py:411` |
| L4 | 神经精排（可选，默认关） | `csc_neural.enhance` | `core/corrector.py:207` |
| L5 | Seq2Seq 语法纠错（可选，默认关） | `csc_gec.enhance` | `core/corrector.py:230` |
| 仲裁 | 重叠去重（置信度→长度贪心） | `_dedupe` | `core/corrector.py:582` |
| 一致性 | 文内数字/日期/单位/机构/标点不一致（只提示） | `consistency.check` | `core/consistency.py:686` |

### 1.2 评测与门禁

| 项 | 口径 | 阈值 | 落点 |
|---|---|---|---|
| E1 | 内置精标孤立检出率 | 100% | `scripts/eval_corrector.py:203` |
| E2 | 生成对在 3 个模板句中的召回 | ≥75% | `:223` |
| E3 | 负样本误报（处/万字） | ≤0.50 | `:242` |
| E4 | 词表外错字召回 | ≥5% | `:264` |
| E5 | 基础三层耗时（ms/千字） | ≤100 | `:277` |
| E6 | 开启增强层新增误报（处/千字） | ≤1.0 | `:292` |

### 1.3 交互链路

| 入口 | 功能 | 落点 |
|---|---|---|
| F7 / 工具菜单「文字纠错」 | 参考面板全文纠错列表、置信度门槛、逐条替换、忽略本次/永久忽略、全部替换 | `ui/reference_panel.py:249/419/432/463/471` |
| Ctrl+Shift+F7「任意文档纠错」 | 文件/剪贴板 → 逐块纠错 → 标记视图 → 应用此修正/全部应用/忽略此词/上下一处/导出 DOCX/TXT/校对报告 | `ui/correct_dialog.py:36/221/388/408/429/482/503/538` |
| 编辑器波浪线 | 全文坐标 → QTextBlock 换算后画波浪线 | `ui/correction_highlighter.py:37/97` |
| 「按分类批量纠错」 | 预览（哪篇/每处/上下文）→ 确认 → 后台写回 + 改前快照 | `ui/feature_dialogs.py:753`、`core/batch.py:155/189/285` |
| 「批量替换」 | 预览→确认→后台执行（正则/字面） | `ui/feature_dialogs.py:336` |
| 文内一致性 | 数字/日期/单位/机构/标点不一致 | `core/consistency.py:686` |

### 1.4 词库与规则集

| 能力 | 落点 |
|---|---|
| 多格式导入（CSV/TSV/TXT/JSON/xlsx/docx 表格/TBX/XLIFF） | `core/wordfmt/__init__.py:78`、`core/wordlist.py:122/189/230` |
| 预检→预览→事务写入（新增/覆盖/冲突/跳过/无效） | `core/wordlist.py:122/189/230` |
| 三种角色（纠错对/术语/保护词） | `core/wordlist.py:42`、`core/wordfmt/__init__.py:32` |
| 冲突不静默覆盖 | `core/wordlist.py:163` |
| 规则集按来源启停 + CSV 双向 | `dao.set_error_pairs_enabled:876`、`core/ruleset.py:40/65`、`ui/dict_manager.py:312` |
| 增强包导入校验（zip-slip/体积/sha256/license/原子替换） | `core/enhance_pack.py:178/232/293` |

---

## 2. 实测记录

> 所有命令均在仓库根目录、上述环境变量下执行。`boot` 探针与 `eval_corrector.py`
> 同构（`paths._override` + `ensure_database_seeded()`），因此数字与门禁口径一致。

### 2.1 召回（E1/E2）

命令：附录脚手架片段 `e1`（精标）/ `e2`（生成对）。

| 口径 | 结果 |
|---|---|
| 内置精标 448 条 · 孤立 | **448/448 = 100.0%** |
| 内置精标 · 模板句（`各单位要高度重视{}工作。`） | **448/448 = 100.0%** |
| 内置精标 · 裸句（`在{}的领导下`） | **448/448 = 100.0%** |
| 生成对抽样 800（seed=20260915）· 孤立 | 679/800 = **84.9%** |
| 生成对抽样 800 · 上下文模板 | 645/800 = **80.6%**（与 E2 输出一致） |
| 词表规模 | `patterns` 39 999 条；库内 39 504（全为 generated）；词典 119 650 |

上下文漏报 155 条逐条归因（命中在模板里查不到时的第一道拦截）：

| 闸门 | 条数 | 占比 |
|---|---|---|
| `_is_common_char_sequence`（逐字高频字，5000 门槛）`corrector.py:482` | 107 | 69% |
| 词边界（两端非分词边界）`corrector.py:429` | 34 | 22% |
| `_is_known_word`（错形本身是词典词/合法词）`corrector.py:521` | 14 | 9% |
| 其它 | 0 | 0% |

> 结论：低置信召回的主要代价确实来自 v1.8 的"高频常用字"闸门（占漏报 69%），
> 与 `corrector.py:455-463` 注释里的实测表一致；这是**有意识的取舍**，不按缺陷计。

### 2.2 性能

命令：附录脚手架片段 `e5`（热/冷/密集耗时）与 `dedupe`（cProfile 归因）。

| 场景 | 汉字 | 耗时 | ms/千字 |
|---|---|---|---|
| 冷启动（首次，建 4 万条词表） | 9 051 | 498 ms | **55.1** |
| 热态 1 万字 | 9 051 | 48 ms | 5.30 |
| 热态 10 万字 | 90 506 | 440 ms | 4.86 |
| 热态 30 万字（单次调用） | 271 519 | 1 347 ms | 4.96 |
| **最坏：命中密集 `布署`×5000（1 万字）** | 10 000 | **1 023 ms** | **102.3** |
| 上下文规则密集 `截止2026年`×1500 | 4 500 | 217 ms | 48.18 |
| 重复字 `的的`×5000 | 10 000 | 28 ms | 2.78 |
| 超长无换行 50 万字 | 250 000 | 1 904 ms | 7.62 |
| 全 ASCII 1 万字 | 0 | 13.2 ms | — |
| 代码块 1 万字 | 800 | 24.7 ms | 30.9 |
| 书名号内引用 5 700 字 | 3 600 | 34.8 ms | 9.67 |

`_dedupe` 归因（cProfile，4 000 命中）：`check_text` 2.286 s 中 **`_dedupe` 2.177 s = 95%**，
其中 `corrector.py:602` 的生成器被调用 **8 002 000 次**（每个候选与已保留的全部候选逐一比对）：

| 命中数 | `_check_exact` | `_dedupe` | 总耗时 |
|---|---|---|---|
| 1 000 | 9.7 ms | 37.7 ms | — |
| 2 000 | 11.2 ms | 153.2 ms | 173 ms |
| 4 000 | 23.2 ms | **632.4 ms** | 699 ms |

即 `_dedupe` 是 O(命中数²)。5 000 命中 → 约 1 s，8 000 命中 → 约 2.5 s。

### 2.3 边界输入

命令：附录 `race`/`seg` 片段同源（本项为批量输入回归）。**16 类输入全部不抛异常**（空串/空白/单字/emoji/
非 BMP/全英文/代码/HTML/标签/SQL/控制字符/全角空格/零宽字符/20 万字单行/残缺代理对/
混合换行/5 万 emoji 串）。

行为正确性（非提示类命中数，期望 vs 实得）：

| 用例 | 期望 | 实得 | 判定 |
|---|---|---|---|
| 引号内不纠重复字 `他说“这是的的”` | 0 | 0 | OK |
| 书名号内不改文件标题 `见《布署管理规定》` | 0 | 0 | OK |
| 代码围栏内中文 ```` ```print('布署')``` ```` | 0 | **1（报布署）** | 无代码感知（见 P2-3） |
| 正常错字 `必须抓紧布署` | 1 | 1 | OK |
| 英文括号不误改 `README(install.md)` | 0 | 0 | OK |

**异常发现**：`"中"×200000` 产生 **1 条 Correction，其 `wrong` 长度 200 000**（`repeat_rules.py:303-308`
对 `k≥3` 不设长度上限，`wrong=text[i:i+k]`）。列表项文案（`reference_panel.py:364`、
`correct_dialog.py:339`）会把 20 万字塞进一个 `QListWidgetItem`，报告表（`core/report.py`）
同样逐条渲染。

### 2.4 缓存失效与并发

命令：附录 `race`/`seg` 片段同源（本项为批量输入回归） / `p13_cache_invariant.py`

| 项 | 结果 |
|---|---|
| `dao.add_error_pair()` 后**未**调 `invalidate_cache()` | 新规则**不生效**（符合设计：失效由调用方负责） |
| 调 `invalidate_cache()` 后 | 立即生效（`待核用词 → 待核定用词`） |
| 加入忽略名单 + 失效 | 立即生效（0 命中） |
| 8 线程 × 100 次并发 `check_text` | 异常 0、结果与单线程逐条一致（8 000 次调用） |
| 并发 200 次 `invalidate_cache()` + 4 线程查错 | 异常 0、命中数变化 0 次 |
| 不变式「`patterns` 在 ⇒ `ignore` 在」 | **不成立**：手工构造该状态后忽略名单被静默跳过（`布署` 重新报出） |

UI 侧失效接线是完整的：`ui/dict_manager.py:101/166/214/319/362-363/383/431/446/511-529`、
`core/wordlist.py:286`、`core/ruleset.py:149`、`ui/reference_panel.py:484`、
`ui/correct_dialog.py:434` 全部调用了 `invalidate_cache()`。

### 2.5 评测门禁（E1–E6）实测

命令与结果：
```powershell
# 默认（内置 8 句负样本 + 全部层）
.\.venv\Scripts\python.exe scripts\eval_corrector.py
# 输出：通过 5/6 项；未达标：E4   → exit code 1
```
| 项 | 值 | 阈值 | 判定 |
|---|---|---|---|
| E1 | 448/448 = 100.0% | =100% | PASS |
| E2 | 645/800 = 80.6%（孤立 84.9%） | ≥75% | PASS |
| E3 | 0 处 / **224 汉字** = 0.00 处/万字 | ≤0.50 | PASS |
| E4 | **1/23 = 4.3%** | ≥5% | **FAIL** |
| E5 | 2 ms / 224 汉字 = 10.0 ms/千字 | ≤100 | PASS |
| E6 | 新增 0 处 / 224 汉字（增强包未装） | ≤1.0 | PASS（空过） |

```powershell
# 指到真实语料（本仓库 doc/*.md，4 695 行 / 117 658 汉字）
.\.venv\Scripts\python.exe scripts\eval_corrector.py --corpus doc --layers base
# 输出：E3 = 1590 处 / 117658 汉字 = 135.14 处/万字（FAIL）；E4 4.3%（FAIL）
```

### 2.6 误报来源拆解（doc/*.md，逐行口径）

| 类别 | 条数 | 备注 |
|---|---|---|
| 标点规范（提示类） | 1 202 | 其中 **1 078 条**来自 ASCII 引号规则；括号未配对 120 |
| 标点（可自动替换） | 94 | 含文件名 `(…md)` 被要求改全角 |
| 错别字(生成) | 80 | 全为 conf 0.55（`显式→显示`/`例用→利用`/`列维→列为`/`不弹→不但`/`一版→一半`） |
| 错别字 | 50 | 多为文档自述的示例词（污染，见下） |
| 易混词 | 46 / 规范用字 34 / 数字用法 30 / 语义冗余 18 / 搭配 10 / 重复字 8 / 机构沿革 5 / 表达提示 5 / 禁用词 5 / 关联词 3 | |

派生指标：

| 口径 | 处/万字 |
|---|---|
| 全部命中（E3 当前口径） | 135.14 |
| 剔除提示类（`ADVISORY_CATEGORIES`） | **30.00** |
| 其中可自动替换且 conf≥0.8 | 15.21 |

**语料污染度**：把命中落在"含 `→`/错别字/误报/审计/缺陷…"元话语行上的部分算作污染，
**604 / 1 590 = 38.0%** 的命中来自"文档在讨论这些错别字本身"。典型的非提示类命中原文：
`…1.8.0增强与修复记录.md](v1.8.0增强与修复记录.md)。`（文件名）、`[错别字] 布署→部署   …工作必须抓紧布署…`（文档自述）。

自造干净公文小样（20 段 / 740 汉字，人工核对无错字）：
**0 处命中 = 0.00 处/万字**（含提示类也为 0）。样本量偏小，见 §6 待验证 T-4。

### 2.7 词库与规则集

命令：附录 `wl`/`json` 片段

| 格式 | 条目 | 提示/问题 |
|---|---|---|
| CSV UTF-8 带表头 | 1 | 识别表头、列名映射 ✓ |
| CSV **GBK**（Excel 另存） | 1 | 自动探测编码 ✓ |
| TSV | 1 | 分隔符=制表符 ✓ |
| TXT 分号 + `#` 注释 | 2 | 分隔符=`;`、注释行跳过 ✓ |
| TXT 字段内含逗号（制表符分隔） | 1 | 未误选逗号 ✓ |
| JSON 分组 + aliases | 3 | 1 对 + 2 条异名展开 ✓；**`"words": ["词",…]` 0 条（见 P2-4）** |
| xlsx（项目自带 OOXML reader，不依赖 openpyxl） | 1 | ✓ |
| docx 表格 | 1 | ✓ |
| TBX（两种 xml:lang） | 1 | 提示"多语言按首个为规范名" ✓ |
| XLIFF 1.2 | 1 | ✓ |
| 不支持扩展名 / 空文件 / 假 xlsx | — | 均给出可读 `ValueError` ✓ |

语义与事务：

| 项 | 结果 |
|---|---|
| 预检五分类 | 新增/覆盖/冲突/跳过/无效 计数正确 |
| 同一「错→对」重复导入 | 计入"覆盖"而非重复行 |
| 同错词不同正形（默认） | **两条并存不覆盖**（`冲突词` 仍为正形A） |
| `accept_conflicts=True` | 旧条 `enabled=0` + 新条启用，建议切换为正形B |
| 写入中途失败（注入第 2 条异常） | `ok=False`，库内 39 508 → 39 508、该来源残留 0 条 → **整批回滚成功** |
| 导入后立即生效 | `wordlist.apply` 末尾 `invalidate_cache()`；实测导入后同一句立刻报出新对 ✓ |
| 保护词角色 | 同时写 `dictionary` + `ignore_words`，`布署` 立刻不再报 ✓ |
| 规则集停用 `generated` | 39 504 条停用后，样例 `一严→一眼` 不再报；**内置精标 `布署`/`截止` 仍生效** ✓ |
| 库内 `source='curated'` 快照 | 被忽略，建议仍是内置的 `布署→部署` ✓（v1.8 语义成立） |
| CSV 导出→导回 | 表头 6 列、含来源/置信度/启用，导回 1 条 ✓ |

规模（5 万条 CSV）：

| 阶段 | 耗时 |
|---|---|
| parse | 1.02 s |
| precheck | 1.36 s |
| apply | 1.31 s |
| 峰值内存（tracemalloc） | **33.0 MB** |
| 导入后**首次** `check_text`（重建 8.9 万条词表） | **773 ms** |

### 2.8 交互链路

命令：附录 `race` 片段（offscreen Qt，模态对话框打桩）

参考面板 `ui/reference_panel.py`：

| 用例 | 结果 |
|---|---|
| `run_check()` 是否真后台 | 返回时 `_check_worker.isRunning() == True`，完成后回调填表 ✓ |
| 「全部替换」排除提示类 | 7 处命中 / 提示类 3 处 → 实际改 4 处，正文未出现"阿拉伯数字年份"等标签 ✓ |
| 正文已改动后的偏移守卫 | 改文本后点「全部替换」→ 提示重算、**正文未被错改** ✓ |
| 忽略本次 | 2 → 1 处，列表同步 1 行 ✓ |
| 永久忽略（写忽略名单） | 该词 0 命中；名单落库 ✓ |
| 逐处应用后重扫 | 应用 1 处 → 正文变 `部署`，重扫剩 2 处（已改项消失、其余保留）✓ |

任意文档纠错 `ui/correct_dialog.py`：

| 用例 | 结果 |
|---|---|
| 表格单元格定位 | `[表格r1c1] 布署→部署`、`[表格r1c2] 截止→截至`、`[表格r2c2] 布署→部署`，`_cell=(r,c)` 正确 ✓ |
| 整篇应用跳过提示类 | 应用后文本无"年份/删其一"等标签 ✓ |
| 忽略此词 → 重扫 | 走 `dao.add_ignore_word` + `invalidate_cache` + `start_check` ✓ |
| 校对报告导出 | 39 692 B DOCX，25 段 + 1 表；含校对对象/时间/依据/结论摘要（按类别、按置信度）/逐处明细/修正后正文 ✓ |
| **扫描中改输入框** | **偏移错位并把无关文字改成建议词（见 P0-1）** |

性能（主线程）：

| 操作 | 规模 | 耗时 |
|---|---|---|
| 「整篇应用」`apply_all_blocks` | 16 364 汉字/100 块 | 143 ms |
| 同上 | 81 820 汉字/500 块（5 970 处） | **737 ms** |
| 标记视图 `to_marked_html` | 80 704 汉字/5 262 命中 | 16 ms |
| 标记视图 `QTextBrowser.setHtml` | 同上 | **846 ms** |
| 波浪线 `set_corrections + rehighlight` | 68 000 汉字/12 000 命中 | 15.8 ms（可接受） |

撤销粒度（复刻 `main_window._apply_edit`，`QTextCursor` 逐个替换）：
6 处命中 → 6 次 `Ctrl+Z` 逐条撤回（`main_window.py` 无 `beginEditBlock`），
而 `reference_panel.py:456` 的确认文案是"替换后可随时用 Ctrl+Z 撤销"。

### 2.9 一致性层（`core/consistency.py`）

| 用例 | 结果 |
|---|---|
| 数字·同值两种写法 `3个部门…三个部门` | 1 条 `数字` ✓ |
| 数字·概数不同值 `3个…五个` | 0 条（按实现判据：值不同不报）✓ |
| 单位·法定/俗称 `5公里…5千米` | 1 条 `单位` ✓ |
| 单位·单一写法 | 0 条 ✓ |
| 标点·中英逗号混用 | 1 条 `标点形式` ✓ |
| 括号·全半角混用（两侧形式都存在时） | ✓（需全文同时出现 `（`） |
| 日期·同值异格式 | 1 条 `日期` ✓ |
| 机构·新旧并存 / 仅新称 | 1 条 / 0 条 ✓ |
| 真实语料 doc/*.md 前 8 篇 | 26 条 / 38 773 汉字，61 ms = 1.58 ms/千字 |
| 10 万字单篇 | 545 ms = **7.19 ms/千字** |

---

## 3. 缺陷与风险

> 分级：P0 = 会改坏正文/使门禁失真；P1 = 明显影响可用性或精度结论；P2 = 局部/可降级问题。

### P0-1 任意文档纠错：扫描进行中改输入框 → 偏移错位 → 把无关文字改成建议词

**证据**（附录 race 片段，可复跑）：
```
旧快照 blocks = ["这项布署工作必须抓紧。", "填充文字。"×300]
start_check() 后立刻把输入框改成 "改后的新文本，这里也有问题。"（扫描期间输入框未置灰）
扫描回调后 _corrs=[1,0]（2 块的旧快照）与 _blocks（1 块新文本）不匹配
第 1 条命中 = 布署→部署（start=2,end=4，来自旧块 "这项布署工作…"）
apply_selected() → blocks[0] = "改后部署文本，这里也有问题。"
```
即 `新文` 被替换为 `部署`，**正文被静默改坏**。

**根因**：
- `_set_busy()`（`ui/correct_dialog.py:286-296`）只禁用按钮，**不禁用 `input_box`**；
- `_on_input_changed()`（`:199-218`）在扫描期间重建 `_blocks` 并清 `_hits`，但**不取消/标记本次扫描**；
- `_on_checked()`（`:243-249`）无条件接受旧快照的 `_corrs`，`_render()`（`:299`）按块下标 `_corrs[bi]` 与**新**块配对；
- `apply_selected()`（`:388-406`）直接按旧 `start/end` 拼接新块文本。

**影响**：只要用户在"纠错中…"期间敲键盘（大文档扫描 0.5–2 s，很容易发生），随后点
「应用此修正」就会改错位置。违背红线「不误改正文」。

**复现命令**：
```powershell
$env:QT_QPA_PLATFORM# 按附录 `race` 片段，在 QT_QPA_PLATFORM=offscreen 下执行
```

### P0-2 评测门禁默认即红灯，且 E3/E5/E6 在 224 汉字上"空过" → "三道门禁全绿"不可复现

**证据**：
```
$ .\.venv\Scripts\python.exe scripts\eval_corrector.py
[FAIL] E4 词表外错字召回（基础三层）：1/23 = 4.3%   →  通过 5/6 项；未达标：E4   (exit 1)
```
- E4 阈值 5%（`eval_corrector.py:54`）配 23 个样本（`:60-67` 的 24 条经 `:265-267` 过滤后剩 23），
  **1 条 = 4.35% < 5%，2 条 = 8.7%** —— 阈值落在两个可取值之间，只能靠 ≥2 条命中通过；
- E3 的默认负样本是 8 句 / **224 汉字**（`:71-80`）：1 处命中 = 44.6 处/万字，
  即"只要有 1 处就必然 FAIL"；反过来 0 命中时 95% 置信上界约 **134 处/万字**，
  证明不了"0.5 处/万字"；
- E5 的探针同样只有 224 汉字（`:278`），实测总耗时 2 ms、10.0 ms/千字，误差量级远大于阈值；
- E6 在增强包未安装时 `ready_l4/l5=False`（`:294-296`），跑出来的"新增 0 处"仍判 **PASS**
  而不是 SKIP —— 一项本来用于"拦住增强层崩坏"的门禁，在没装包时永远绿。

**影响**：CI/发布门禁失去拦截力；文档里"三道门禁全绿"的结论在本机**无法复现**
（实际 5/6，且未达标项正是 E4）。

**复现**：上面的命令即复现；`--json` 落盘可对比。

### P1-1 「可自动替换」类标点规则缺少中文语境/代码守卫，整篇应用会改坏代码、文件名与 URL

**证据**（附录 punct 片段，`corrector.correct_block` 即"整篇应用/批量写回"的实现）：

| 输入 | 输出 | 规则 |
|---|---|---|
| `print("恢复")` | `print（"恢复"）` | 括号规则 `corrector_data.py:731` |
| `调用 f(...) 后返回` | `调用 f(……) 后返回` | 省略号规则 `:720`（`\.{3,}` **无任何上下文守卫**） |
| `http://x/(说明)` | `http://x/（说明）` | 括号规则 |
| `见 (v1.8.0增强与修复记录.md)` | `见 （v1.8.0增强与修复记录.md）` | 括号规则（黑名单 `[\[\]{}<>_=/\\|`~@#$%^&*]` 不含 `.` 与 `"`） |
| `若 a[i](说明) 成立` | `若 a[i]（说明） 成立` | 括号规则 |

这几类（`错别字`/`易混词`以外的"标点"）**不在** `ADVISORY_CATEGORIES`（`corrector.py:29-33`），
因此会进 `correct_block` → 「整篇应用」(correct_dialog.py:411-424)、「批量写回」(batch.py:209-219)、
「全部替换」(reference_panel.py:438-461)。

**影响**：产品宣称支持 md/html/txt 的任意文档纠错（`correct_dialog.py:144`），
用户粘贴含中文的代码/JSON/文件名后点「全部应用」，会得到语法错误的正文；且这是
"一次点击改一片"的操作。红线「不误改」在此有实质风险。

**可复跑**：附录 `punct` 片段（或
`.\.venv\Scripts\python.exe -c "import boot;print(boot.boot()['corrector'].correct_block('调用 f(...) 后返回'))"`）。

### P1-2 提示类占真实语料命中的 77.8%，其中 ASCII 引号规则单条贡献 1 078 处

`grammar_rules.py:141` 的 `"[^"\n]{0,20}[\u4e00-\u9fff][^"\n]{0,20}"` 在 markdown/JSON/
配置文本上大量命中（`doc/*.md`：1 078 处）；`check_punct_pairs`（`:220`）对代码里的
`(…)` 报"圆括号未配对" 120 处。两者都是提示类（不会被自动写回），但：
- 它们在「任意文档纠错」的横幅里占 `提示类 N`，在列表里占绝大多数行；
- 用户看到 1 200 条噪声后，真实信号（50 条错别字）被埋在下面；
- E3 的 135.14 处/万字中有 77.8% 由它们贡献，可是**混进"误报"指标里并不能说明正文会被改坏**。

**影响**：功能可信度（首屏体验）+ 评测口径失真。

### P1-3 生成层低置信误报在技术中文上稳定存在，且新增闸门使"同一文本、不同入口"结论不同

**证据 A**（逐行口径，`doc/*.md`）：80 条 conf=0.55 的误报，典型
`显式→显示`（11 次以上）、`例用→利用`、`列维→列为`、`不弹→不但`、`一版→一半`、`一搬→一半`。
上下文均为正常技术文句：`实际「products` 显式传入时**不回填**`。

**证据 B**（附录 seg 片段）：v1.8 的闸门 c「同一文档内重复出现 ≥2 次 → 不报」按
**这一次调用传入的 text** 计数（`corrector.py:422`），于是：

```
正文 = "如果 `products` 显式传入时**不回填**…。\n另一个函数也要求显式传入。\n"
整篇一次 check_text → []            （重复 2 次 → 闸门放行）
逐行两次 check_text → ['显式 → 显示', '显式 → 显示']
```
同一份 `v1.8.0增强与修复记录.md`：整篇低置信 5 处 / 逐行 7 处（差异 2 个错形：
`显式`、`则正则`）；`深审报告_v1.8.md`：整篇 7 处 / 逐行 28 处（差异 10 个错形）。

而 UI 两个入口恰好用了不同粒度：
- 参考面板 = **整篇**（`reference_panel.py:255` 取 `editor_getter()` 全文）；
- 任意文档纠错 = **逐块**（`correct_dialog.py:572-589` 每块一次 `check_text`）。

**影响**：同一份材料换个入口，建议条数从 5 变 7、从 7 变 28；用户会认为"纠错不稳定"。
这条闸门的语义应定义为"按文档"（整篇）或"按段落"，二者择一。

### P1-4 大文档主线程阻塞：整篇应用 0.74 s、标记视图渲染 0.85 s

`correct_dialog.apply_all_blocks()`（`:408-427`）在主线程逐块 `correct_block`：
81 820 汉字/500 块 = **737 ms**（5 970 处），期间 GUI 无响应；随后 `_render()`（`:299-335`）
再对全文 `to_marked_html` + `setHtml`：80 704 汉字/5 262 命中 = **846 ms**。
合计单次「全部应用」≈ **1.6 s 冻结**（20 万字级文档按线性外推 ≈ 4 s）。

对比：扫描本身确实是后台的（`start_check` → `FnWorker(_scan_blocks)`，`:238`），
E2E「工作线程是否真后台」这一项**通过**；问题在于"写回 + 渲染"没有走工作线程。

### P1-5 `_dedupe` 是 O(命中数²)，命中密集文本 102 ms/千字，超过 E5 阈值

见 §2.2：`corrector.py:599-604` 对每个候选与 `taken` 全表比对；4 000 命中 632 ms、
8 000 命中约 2.5 s。最坏情况（1 万字 / 5 000 命中）**102.3 ms/千字**，而
`TH_MS_PER_QIAN_BASE = 100`（`eval_corrector.py:57`）；E5 只在"0 命中"的 224 字样本上测，
**永远发现不了这一项**。

### P2-1 撤销粒度：N 处替换 = N 次 Ctrl+Z

`main_window._apply_edit`（`:643-649`）逐个 `QTextCursor.insertText`，未用 `beginEditBlock/endEditBlock`
分组；实测 6 处 → 6 次 `Ctrl+Z`。文案（`reference_panel.py:456`）却写"替换后可随时用 Ctrl+Z 撤销"，
用户按一次会以为"撤销没生效"。

### P2-2 缓存不变式不成立（`patterns` 在、`ignore` 缺失 → 忽略名单静默失效）

`_load_patterns()`（`corrector.py:66-130`）先写 `_RULES_CACHE["patterns"]`（:121）再写 `["ignore"]`（:124），
`invalidate_cache()` 整体 `clear()`（:150-163），两者之间没有锁。构造该状态后
`check_text("这项布署工作。")` 重新报出 `布署→部署`（附录 wl 片段构造）。
并发压测（8 线程 × 100 次、并发 200 次失效）未触发异常或错误结果，因此评为 P2；
但"忽略名单短暂失效"对用户是可见的（"我刚忽略的词又回来了"）。

### P2-3 无代码/围栏/行内代码感知

`check_text` 是纯文本扫描，不知道 markdown 围栏、行内代码、JSON 字符串。实测
```` ```print('布署')``` ```` 报 `布署→部署`。对公文正文无影响，对"任意文档纠错"
（尤其 md/技术类）是稳定的噪声源，也是 P1-2 的成因之一。

### P2-4 JSON 导入：文档承诺支持的 `"words": ["词1", "词2"]` 实际 0 条入库

`wordfmt/json_fmt.py:8` 明确写"带分组的对象 `{"error_pairs": [...], "terms": [...], "words": [...]}`"，
但 `_entry_from`（`:55-60`）对非 dict 直接 `add_issue("条目不是对象")`，
`parse`（`:145-153`）也不为 `words` 分组做字符串展开。实测
`{"words":["保护词甲","保护词乙"]}` → `entries` 中 0 条 protect，只留 2 条 issue。
用户侧表现是"导入成功但什么都没加"（`dict_manager._import_wordlist:151-153` 会提示
"没有可导入的有效条目"，但不会指出原因）。

### P2-5 重复字 `k≥3` 不设长度上限

`repeat_rules.py:303-308, 342-352`：`wrong = text[i:i+k]`。20 万字同字连会产出
`wrong` 长 20 万的 `Correction`，进入列表文案、报告表、`to_marked_html`。
真实公文几乎不会出现，但粘贴的表格/占位符（`□□□` 不属 CJK，不受影响；`中中中…`、`等等等…`）
可能触发。建议 `k` 超过阈值（如 6）只报"疑似连续重复 N 次"并截断 `wrong`。

### P2-6 评测口径的其它盲区

| 盲区 | 说明 |
|---|---|
| E1 只测"孤立词串" | 448 条在 3 种上下文中也是 100%，所以目前**没有**误导；但它不测精度，且若将来某条被边界门控吃掉，孤立口径最先漏检 |
| E2 判定过松 | `eval_corrector.py:229-231` 用 `if corrector.check_text(...)` 判"召回"，**不校验 suggestion 是否等于 p.correct**；任何别处命中都算召回。实测两种口径数值相同（84.9%/80.6%），说明当前无虚高，但这是一颗地雷 |
| E2 自洽性 | 生成对来自 `scripts/seed_data.py` 的 jieba+pypinyin 程序化生成，E2 抽样自同一批数据、用固定 3 模板 → 闭环自证，不能替代真实语料评测 |
| E3 语料 | 默认 224 字（无判别力）；`--corpus doc` 又**被污染 38%**（文档本身在讲这些错别字），且 77.8% 是提示类 |
| E4 天花板 | 23 条样本 + 5% 阈值 → 阈值不可达中间态；且 E4 也用 `check_text` 非空判召回 |
| E6 空过 | 包未装时判 PASS，见 P0-2 |
| 分段敏感性 | E3 逐行（135.14/万字）vs 整篇（117.22/万字）差 13%；同一门禁换个切分方式结论会变 |

---

## 4. 优化建议

> 每条给：目标 / 改法落点 / 成本 / 风险 / 验收口径 / 红线影响。

### S1（对应 P0-1）扫描结果与"当前文本"绑定，过期结果不许应用

- **目标**：用户在扫描期间改文本后，不可能用旧偏移改新正文。
- **改法落点**：`ui/correct_dialog.py`
  1. `start_check()` 生成一个 `self._token += 1` 并随 worker 闭包带走；`_on_checked` 比对 token，
     不一致直接丢弃并提示"文本已改动，已重新检查"（10 行内）；
  2. `_set_busy(True)` 时 `self.input_box.setReadOnly(True)`，`_set_busy(False)` 恢复（2 行）；
  3. 兜底：`apply_selected()` 里校验 `self._hit_text(bi, ri, ci)[c.start:c.end] == c.wrong`，
     不一致则拒绝并提示（参考 `batch._apply_hits` 的 `text[h.start:h.end] == h.wrong` 守卫，
     `core/batch.py:303` 已有同款写法可复制）。
- **成本**：约 30 行 / 半天（含单测）。
- **风险**：极低；只影响"扫描中编辑"这一路径。
- **验收口径**：新增 `tests/test_correct_dialog.py` 用例 —— 扫描中改文本后
  `apply_selected()` 不得修改 `_blocks`；且 `_hits` 为空或与新文本一致。
- **红线影响**：正向（消除误改）。

### S2（对应 P0-2）把门禁改成"能红、能空过标记、可复现"

- **目标**：`eval_corrector.py` 的 PASS/FAIL 真实反映质量，不再有"空过绿灯"。
- **改法落点**：`scripts/eval_corrector.py`
  1. E4 阈值改为**按样本量标定**：`TH = max(0.05, 2/len(oov))`，或把 `OOV_CASES` 扩到 ≥100 条；
  2. E3/E5 增加 `min_chars` 门槛（建议 ≥20 000 汉字），不足时输出 `SKIP` 并以
     `--allow-small-corpus` 显式放行（默认不 PASS）；
  3. E6 在 `not (ready_l4 or ready_l5)` 时输出 **SKIP** 而不是 PASS；
  4. E3 拆两档：`E3a 可自动替换类`（非提示类）阈值 0.50/万字、`E3b 提示类`阈值 10/万字，
     避免 1 200 条提示噪声把"改坏正文"的指标冲淡；
  5. E2 改为 `any(h.suggestion == p.correct for h in ...)`（与 E1 同口径）。
- **成本**：约 80 行 / 1 天；语料需要业务侧提供 ≥2 万字真实公文（本次审计没有，见 T-4）。
- **风险**：改阈值等于改验收标准，需在提交说明里写理由（脚本头部 `:49-56` 已有此约定）。
- **验收口径**：`--corpus <2万字真实公文>` 时 exit 0；故意注入 10 处已知误报时 exit 1；
  未装增强包时 E6 显示 SKIP。
- **红线影响**：无运行时依赖变化；仅改脚本与阈值。

### S3（对应 P1-1）给"可自动替换"类标点规则加语境守卫

- **目标**：`...`、`(…)`、`"…"` 只在中文行文语境里才给可应用建议。
- **改法落点**：`core/corrector_data.py:710-734`
  1. 省略号规则加前置条件：仅在**相邻字符为汉字/中文标点**时命中
     （`(?<=[\u4e00-\u9fff。！？；])\s*\.{3,}` 之类），或整条降级为 `表达提示`（提示类）；
  2. 括号规则黑名单补 `. " ' : / \` 与"含 URL scheme / 文件扩展名"的负向断言；
  3. 更稳的做法：把 `标点` 类**拆两类** —— 「标点重复/全角化」保留可自动替换，
     「省略号/括号/引号形式」移入 `ADVISORY_CATEGORIES`（一行改动，立即消除写回风险）。
- **成本**：改数据 + 1 处类别表，约 2 小时；补 `tests/test_punct_guards.py` 反例 10 条。
- **风险**：把省略号/括号降级为提示类，会让「全部替换」少改一些本该改的 `(附件1)`
  —— 用"只降级省略号+URL/扩展名排除"可以两全。
- **验收口径**：`correct_block('调用 f(...) 后返回')`、`correct_block('print("恢复")')`、
  `correct_block('见 (a.md)')` 均**不等于**原文改动；`correct_block('见(附件1)')` 仍全角化。
- **红线影响**：正向（不误改）。无新依赖。

### S4（对应 P1-2/P2-3）提示类分层展示 + 代码/围栏免疫

- **目标**：首屏只显示用户真要处理的条目；代码/JSON 片段不产生噪声。
- **改法落点**：
  1. `ui/correct_dialog.py:_show_banner` 已把提示类单列（`:251-279`），把**列表**也按类别分组
     或默认折叠提示类（`listw` 加"仅显示可替换项"开关，默认开）；
  2. 引擎侧最小改动：`corrector.check_text` 前先算"受保护区"（三反引号围栏、行内代码、
     HTML `<script>/<style>`），命中落在保护区内则丢弃。现成的 `repeat_rules._protected_mask`
     （`:152`）就是同款思路，可抽出为 `corrector._code_mask` 复用。
- **成本**：保护区 60 行 + UI 开关 30 行 / 1–1.5 天。
- **风险**：保护规则写宽会漏报（把真错当代码）；建议只认三反引号围栏与行内代码，
  并对公文格式（无围栏）零影响。
- **验收口径**：```` ```print('布署')``` ```` 0 命中；`这项工作必须抓紧布署` 仍 1 命中；
  `doc/*.md` 提示类从 1 202 降到 <100。
- **红线影响**：纯逻辑，无依赖。

### S5（对应 P1-3）把"文档内重复"闸门语义固定为**按文档**，并在两个入口统一

- **目标**：同一份材料，参考面板与任意文档纠错给出相同结论。
- **改法落点**：`core/corrector.py:422`
  1. `check_text(text, *, doc_scope: str | None = None)`：`doc_scope` 传入时用它做
     `doc_scope.count(hit)` 而不是 `text.count(hit)`；
  2. `reference_panel` 传全文；`correct_dialog._scan_blocks` 传**整篇拼接文本**给每块
     （见 `_scan_blocks`，`correct_dialog.py:572`），词边界仍按块判定；
  3. 或者反向选择：删掉闸门 c，改为"同一文档内重复 ≥2 且不是精标错形 → 降为提示类"。
- **成本**：接口加 1 个可选参数 + 2 处调用，约 3 小时。
- **风险**：改变现有判定结果（技术上更稳、少数真错可能被抑制）；需要重跑 E2/E3。
- **验收口径**：新增 E7「同文本逐块 vs 整篇结果一致率 ≥99%」；
  附录 `seg` 的最小复现两个入口都返回 `[]`（或都返回 2 条）。
- **红线影响**：无。

### S6（对应 P1-4）把"写回 + 渲染"移出主线程

- **目标**：20 万字文档点「全部应用」时不冻结窗口。
- **改法落点**：`ui/correct_dialog.py`
  1. `apply_all_blocks` 复制 `_scan_blocks` 的模式：`FnWorker(_apply_all_blocks_worker, blocks_snapshot)`，
     完成回调里 `_blocks = result; _render()`；
  2. `_render` 的 `to_marked_html` 也可进 worker 生成 HTML 字符串（16 ms/8 万字，收益小），
     真正贵的是 `setHtml`（846 ms）——建议**分块增量**：先渲染前 20 块，其余 `QTimer.singleShot(0)` 续渲染，
     保证窗口可响应。
- **成本**：worker 化 60 行 + 增量渲染 40 行 / 1–2 天。
- **风险**：`_blocks` 跨线程读写必须深拷贝（`export_docx` 已有 `deepcopy` 先例，`:492-493`）；
  Qt 控件仍只能在主线程操作，注意只把"纯计算"放进 worker。
- **验收口径**：`apply_all_blocks` 期间主线程无 >100 ms 连续阻塞（可用 `QElapsedTimer` 打点）；
  8 万字文档"全部应用→重扫"总时长不劣于当前 1.6 s。
- **红线影响**：无。

### S7（对应 P1-5）`_dedupe` 换成 O(n log n)

- **目标**：命中密集文本不退化；最坏情况回到 ≤10 ms/千字。
- **改法落点**：`core/corrector.py:582-605`。保留"按 (置信度, 长度) 排序"的语义不变，
  把 `taken` 从列表换成**按 start 排序的数组 + `bisect`**（已保留区间互不重叠，
  所以只需检查左右两个邻居）；`lexicon` 包含消解同理（`lexicon` 也按 start 排序后二分）。
- **成本**：40 行 / 半天 + 单测（保持既有 `test_corrector*` 全绿）。
- **风险**：中低。语义等价性必须用现有用例 + 随机对拍（100 段随机文本，新旧实现输出逐条相等）。
- **验收口径**：5 000 命中 1 万字 ≤150 ms（当前 1 023 ms）；
  E5 新增 E5b「命中密集（每千字 500 命中）≤120 ms/千字」。
- **红线影响**：纯算法，无依赖。

### S8（对应 P2-1）批量替换合并为一次撤销

- **改法落点**：`ui/main_window.py:_apply_edit` 改为接受区间列表，内部
  `cur.beginEditBlock() … cur.endEditBlock()`；`reference_panel._apply_all` 一次性 emit
  （或新增 `apply_edits = Signal(list)`）。
- **成本**：30 行 / 2 小时。
- **风险**：低（单条替换仍走同一函数，行为不变）。
- **验收口径**：6 处替换后 1 次 `Ctrl+Z` 回到原文。
- **红线影响**：无。

### S9（对应 P2-2）缓存改成"一个键、一次赋值"

- **改法落点**：`core/corrector.py:_load_patterns` 末尾一次性
  `_RULES_CACHE.update({...})` → 改为 `_RULES_CACHE["state"] = (patterns, ignore)` 单次赋值，
  `_check_exact` 只读 `state`。
- **成本**：20 行 / 1 小时。
- **风险**：低；需同步改 `_buckets()` / `_known_word_set()` 的失效键。
- **验收口径**：附录 `wl` 的构造状态无法再出现（旧键不再被单独读取）。
- **红线影响**：无。

### S10（对应 P2-4）JSON `words` 分组支持字符串数组

- **改法落点**：`core/wordfmt/json_fmt.py:_entry_from` 开头加：
  若是 `str` 且 `eff_role == "protect"` → 构造 `Entry(role="protect", word=obj)`；
  `parse` 的 groups 分支无需改。
- **成本**：8 行 / 1 小时（含 3 条单测）。
- **风险**：极低。
- **验收口径**：`{"words":["甲","乙"]}` → 2 条 protect，`precheck` 计数=2。
- **红线影响**：无。

### S11（对应 P2-5）重复字的 `wrong` 截断

- **改法落点**：`core/repeat_rules.py:342-352`，`k > 6` 时
  `wrong = text[i:i+3] + f"…（共 {k} 次）"`，`kind="delete"` 语义保留 `start/end` 原区间。
- **成本**：6 行 / 30 分钟。
- **风险**：报告/列表展示变化（可接受）。
- **验收口径**：20 万字同字连的 `Correction.wrong` 长度 ≤ 20。
- **红线影响**：无。

---

## 5. 不建议做的项

| 不建议 | 理由 |
|---|---|
| **为了 E3 变绿而放宽 0.5 处/万字阈值** | 这是唯一直接对应"用户看到假建议"的红线指标；真问题是语料（224 字 / 被污染 38%），不是阈值。降阈值等于放弃验收。 |
| **用 `doc/*.md` 充当正式负样本** | 实测 38% 命中落在"文档在讲解这些错别字"的行上；把它当负样本会同时得到假 FAIL（该报的算误报）和假 PASS（技术文噪声被忽略）。必须用业务侧真实公文。 |
| **扩大生成对规模（39 504 → 更多）** | E2 已达 80.6%，主要漏报来自"高频字/词边界/合法词"三道**精度闸门**，与词表大小无关；继续灌词只会抬高冷启动（已 55 ms/千字）和 `_dedupe` 的规模，不改善召回。 |
| **把 L4/L5 默认打开以冲 E4** | 违反"主包不带模型"红线；且 E6 的口径本身在未装包时空过，先修口径再谈开启。 |
| **引入 jieba 之外的新分词/语言模型来提精度** | 新增运行时依赖 + ARM64/体积/许可风险；现有误报（`显式→显示`）用"文档级重复闸门 + 用户词库逃生通道"已可收敛（用户加一次 0.99 永久生效）。 |
| **给 doc/*.md 这类技术文本做专门规则** | 产品面向公文；为仓库自己的文档调规则属过拟合。技术文本噪声用 S4 的"代码围栏免疫"解决即可。 |
| **在 UI 层再维护一份"提示类类别清单"** | 项目已统一到 `corrector.ADVISORY_CATEGORIES`（`batch.py:96-100` 注释写明两次踩坑），任何第二份清单都会漂移。 |
| **用 Qt 富文本/表格控件接管纠错列表以"美化"** | `_corr_row_widget`（`reference_panel.py:287`）已用 `setItemWidget` 做行内视图；再上复杂控件会放大 §2.8 的渲染阻塞（`setHtml` 846 ms 已是大头）。 |

---

## 6. 待验证项

| 编号 | 待验证 | 建议验证方法 | 本次为何未做 |
|---|---|---|---|
| T-1 | **Py3.9 可运行性** | 在 3.9 环境跑 `scripts/eval_corrector.py` 与 `tests/test_corrector*.py`、`test_repeat_rules.py` | 本机 venv 为 3.13；全仓 `from __future__ import annotations` 覆盖较全，但 `dict[str, tuple[...]]` 这类**运行期**注解（如 `corrector.py:66` 返回值注解、`repeat_rules.py:81`）在 3.9 下需确认未被求值。**未实测，勿当结论** |
| T-2 | ARM64/glibc2.31 上同上 | 麒麟 ARM64 真机跑同一组命令 + `scripts/check_inference_stack.py` | 无环境 |
| T-3 | E3 在**真实公文**上的误报率 | `/eval_corrector.py --corpus <本单位≥2万字公文>`，连续 3 个不同科室的语料 | 本次只有 doc/*.md（污染 38%）与 740 字自造小样（无统计力） |
| T-4 | 自造干净公文语料的 0 误报是否可放大 | 把 740 字扩到 ≥5 万字（可用不同主题的成文），看命中是否仍为 0 | 时间；需要成体系的真实文本 |
| T-5 | `correct_dialog` 竞态在真实机上是否可达 | 用 5 万字文档触发扫描（>1 s），期间敲 3 个字符后点「应用此修正」 | 已在 offscreen 下确认可复现错改；真实键盘时序由人工/自动化 UI 测试确认 |
| T-6 | 并发 `invalidate_cache` 与 `_load_patterns` 的真实交错窗口 | 在 `_load_patterns` 的 patterns/ignore 之间插 `time.sleep(0.05)`，另起线程反复 clear，跑 1 000 轮统计忽略名单失效概率 | 需要改源码加钩子（审计只读）；本次只用构造状态证明不变式不成立 |
| T-7 | 增强包（L4/L5）真实误报 | 安装 csc/cgec 包后跑 `eval_corrector.py --layers base,l4,l5` 看 E6 | 本机未安装增强包（`拼写=未装 语法=未装`） |
| T-8 | 50 万条级词库的冷启动与内存 | 导入 50 万条后测首次 `check_text` 与 `tracemalloc` 峰值（本次 5 万条：773 ms / 33 MB） | 时间；且会污染临时库 |
| T-9 | `_MAX_SHOW_HITS=4000` 截断下的写回一致性 | 构造 >4 000 命中批次，确认"显示截断但执行全量"（`feature_dialogs.py:908-931`） | 需要构造大库 |
| T-10 | 批量纠错漂移重定位的准度 | 预览后手工改正文再执行，统计 `skipped` 与"改到别处"的次数（`_context_matches` 的前后缀各取一半，`batch.py:272-282`，理论上存在误配可能） | 需要 UI 时序；本次只静态审读了算法 |

---

## 附：可复跑脚手架（探针文件已按协议删除，以下内容自包含）

把下面这段存成任意临时脚本（或直接 `python - <<` 喂进去），即可复现本文所有数字
的数据侧（与 `scripts/eval_corrector.py` 同构：临时库 + 播种，不碰真实用户数据）。

```python
# scaff.py —— 用法：.\.venv\Scripts\python.exe scaff.py <片段名>
import sys, os, time, tempfile
from pathlib import Path
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from gwtool import paths
tmp = Path(tempfile.gettempdir()) / "gwtool_audit_v19" / "data"
tmp.mkdir(parents=True, exist_ok=True)
paths._override = tmp
from gwtool.db import connection as dbconn
dbconn.configure(tmp / "audit.db")
from gwtool.app import ensure_database_seeded
ensure_database_seeded()
from gwtool.core import corrector, corrector_data, consistency, repeat_rules
from gwtool.db import dao

def cjk(s): return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
```

| 片段 | 复现内容 | 代码 |
|---|---|---|
| `e1` | 精标全量召回 | `p=corrector_data.CURATED_PAIRS; ok=sum(any(h.suggestion==c for h in corrector.check_text(w)) for w,c,_k,_f in p); print(ok,len(p))` → `448 448` |
| `e2` | 生成对上下文召回 | 取 `dao.all_error_pairs(only_enabled=False)` 中 `source=="generated"`，`random.Random(20260915).sample(gen,800)`，对 `f"各单位要高度重视{w}工作。"` 判 `any(h.suggestion==c …)` → `645 800` |
| `e3` | 语料误报（可换 doc/ 或自造） | 读 `doc/*.md` 按行过滤 `cjk(line)>=8`，逐行 `check_text` 累加 → `1590 处 / 117658 汉字 = 135.14/万字`；`[h for h in hits if h.category not in corrector.ADVISORY_CATEGORIES]` → `353 处 = 30.00/万字` |
| `e4` | 词表外召回 | 见 `scripts/eval_corrector.py:60-67` 的 `OOV_CASES`，过滤后 23 条，实测 1 条命中 → `4.3%` |
| `e5` | 耗时（热/冷/密集） | `t=time.perf_counter(); corrector.check_text("布署"*5000); print((time.perf_counter()-t)*1000)` → 约 `1 023 ms`（先跑一次普通文本预热） |
| `dedupe` | `_dedupe` 占比 | `import cProfile,pstats;cProfile.run('corrector.check_text("布署"*4000)','/tmp/p');pstats.Stats('/tmp/p').sort_stats('cumulative').print_stats(6)` → `_dedupe 2.177s / 2.286s` |
| `race` | P0-1 错改 | 见 §3 P0-1 的 offscreen 步骤（`AnyDocCorrectDialog.start_check()` → `input_box.setPlainText(新文本); _on_input_changed()` → 等 worker → `apply_selected()`） |
| `punct` | P1-1 破坏性 | `c=corrector; print(c.correct_block('调用 f(...) 后返回')); print(c.correct_block('print("恢复")'))` → `('调用 f(……) 后返回', …)` / `('print（"恢复"）', …)` |
| `seg` | P1-3 分段差异 | 同一段两行文本，`check_text(整段)` vs 逐行 `check_text` → 整段 `[]`，逐行各 1 条 |
| `json` | P2-4 | `wordlist.parse(<含 {"words":["词1","词2"]} 的文件>, "pairs")` → `entries` 无 protect，`issues` 2 条 |
| `wl` | 导入/回滚/停用 | `wordlist.parse→precheck→apply`；停用 `dao.set_error_pairs_enabled(False, source="generated")` + `corrector.invalidate_cache()` |
| `cons` | 一致性 | `consistency.check("共有3个部门参加，另有三个部门列席。")` → 1 条 `数字` |

门禁本身（无需脚手架）：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\eval_corrector.py                       # 5/6，E4 FAIL，exit 1
.\.venv\Scripts\python.exe scripts\eval_corrector.py --corpus doc --layers base   # E3 135.14/万字 FAIL
```
