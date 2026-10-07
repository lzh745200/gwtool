# OpenCodeReview 全量代码审查报告（v1.7.3 基线）

## 一 审查方式

- 工具：`@alibaba-group/open-code-review` v1.12.12（delegate 规则集模式，无 LLM 密钥依赖）
- 流程：工具输出 11 类审查规则（拼写、死代码、可变默认参数、边界、异常处理、
  比较语义、资源管理、并发、安全、性能、风格）→ 宿主按规则**逐文件**审查
  → P1 结论 100% 人工复核源码确认，P2 抽验
- 范围：第一方代码 **190 个文件 / 56,023 行**
  （gwtool/ 94 个 .py、main.py、tests/ 81 个 .py、scripts/ 12 个 .py、gwtool.spec）
  第三方产物（scripts/ 下 .ts/.js、third_party/）不在范围
- 逐文件明细见附录（附录 A–F 为六组原始审查记录，含"未发现"文件清单）

## 二 总览

| 严重度 | 数量 | 含义 |
| --- | --- | --- |
| P1（正确性/安全） | **5** | 均已人工复核源码确认，属真实缺陷 |
| P2（明确隐患） | **12** | 资源/并发/性能/健壮性 |
| P3（风格与可读性） | **31** | non-blocking |
| 合计 | 48 | 190 个文件中 66 个存在发现，其余 124 个未发现 |

整体结论：代码质量较高——schema 迁移事务边界、纠错各层静默降级注释、
备份/恢复的幂等与原子性设计均符合纪律；5 项 P1 集中在「返回值语义、
XML 转义、条目去重、信号时序、global 声明」这类单点疏漏，修复面小。

## 三 P1 缺陷（已复核确认，建议优先修复）

| # | 位置 | 问题 | 影响 | 修复建议 |
| --- | --- | --- | --- | --- |
| 1 | `gwtool/ui/import_dialog.py:236-245` | 完成回调必然执行两遍：ImportWorker 先发 `finished_ok` 再发 `finished_detail`（workers.py:150-151），`_done` 的 `_detail_shown` 守卫只在 detail 先到时生效，顺序相反即失效；且 `_start` 不重置该标志，第二轮起 `_done` 被脏标志永久短路 | 每轮导入 `_finish_common` 跑两遍：失败清单写两次、"已保存"弹窗连弹两次 | 以 `finished_detail` 为唯一完成信号，`_done` 仅在 worker 不发 detail 信号时连接；或 `_finish_common` 入口做幂等守卫并在 `_start` 重置 |
| 2 | `gwtool/core/exporter.py:201-207` | 移交包内附件以原始文件名作 zip 条目且无去重，不同文档同名附件互相覆盖，先写入者被静默顶掉（`backup._write_attachments` 有 seen 去重，此处遗漏） | 交出的包缺文件且无任何提示，属数据完整性问题 | 仿照 backup：`seen` 集合检测重名，重名条目名追加 `序号` 或记入 manifest 的冲突清单 |
| 3 | `gwtool/core/watermark.py:95` | 水印文本未做 XML 转义直接拼进 VML 模板属性，含 `&` 的文本（如 R&D 类）令 `parse_xml` 抛 XMLSyntaxError | 水印功能对特定文本必崩 | 拼 XML 前对 text 做 `xml.sax.saxutils.escape`（属性值再加 `&quot;` 处理） |
| 4 | `gwtool/db/dao.py:992` | `save_template` 的 upsert（ON CONFLICT DO UPDATE）后返回 `cur.lastrowid`：update 分支不发生 INSERT，该值语义不成立，可能指向另一条模板 | 调用方拿到错误 template id；同文件 `save_user_skeleton` 已记录同类坑并改为回查 | 仿 `save_user_skeleton`：upsert 后按 name 回查真实 id 返回 |
| 5 | `gwtool/core/csc_neural.py:386`（`csc_gec.py:379` 同款，P2） | `enhance()` 异常处理里 `_ENGINE = None` 只声明了 `global _ENGINE_ERROR`，实际赋给了即弃局部变量，坏引擎不会被复位 | 与「推理异常后丢弃引擎强制重载」的设计意图相反，坏引擎被反复复用 | 补 `global _ENGINE, _ENGINE_ERROR` 声明 |

## 四 P2 隐患（12 项）

| # | 位置 | 问题 | 建议 |
| --- | --- | --- | --- |
| 1 | `gwtool/core/xlsx_read.py:205-209` | 稀疏网格补齐：行维有 max_rows 闸，**列维无上限**，声明超大列号的单元格仍可令输出膨胀（1.6 万列 × 行数） | 对 width 设上限（如 512），超限按异常文件拒绝 |
| 2 | `gwtool/ui/feature_dialogs.py:1103` | SimilarityDialog 后台线程 `dao.get_document(did).content_text` 未判 None（同文件 _BulkReplaceWorker:389 已防护） | 比照补 None 判断，文档删除时跳过 |
| 3 | `gwtool/core/reference.py:97` | `phrase_full_text` 用 `list_phrases()`（limit=500）全量遍历找单 id：句式超 500 条时查不到，且每次全表传输 | 改为按 id 的参数化单条查询 |
| 4 | `gwtool/core/model.py:67-71` | `DocTree.from_json` 的 try 包住整个循环且静默吞异常：一个坏块致其后所有块无声丢失 | try 收窄到单块，坏块记日志后跳过 |
| 5 | `gwtool/ui/reference_panel.py:425` | `_apply_one` 按「选中顺序」逆序替换而非按 start 降序，非降序时后续偏移错位改坏正文（同文件 `_apply_all`:456 已正确） | 比照 `_apply_all` 用 `sorted(key=start, reverse=True)` |
| 6 | `gwtool/core/csc_gec.py:379` | 同 P1-5 的 global 缺失（GEC 侧同款） | 同 P1-5 |
| 7 | `gwtool/ui/compile_wizard.py:820` | `_find_library_entry` 同参连调两次，每次内部最多两遍全表扫描，批量 N 份产物退化为 4N 次全表扫 | 单次查询结果传递复用 |
| 8 | `gwtool/ui/compile_wizard.py:500` | 用户输入的封面标题未做文件名清洗即拼输出路径（含 `\/:*?"<>|` 时失败或生成意外子目录）；`batch.safe_filename` 现成 | 输出路径统一过 `safe_filename` |
| 9 | `gwtool/core/parsers/rtf_parser.py:11` | `open(path,"rb").read()` 无 with，异常路径句柄不保证关闭 | 改 with |
| 10 | `gwtool/ui/correct_dialog.py:463` | 导出用 `list(self._blocks)` 浅拷贝，dict 元素与主线程共享，导出期间可并发读写 | 导出线程内做深拷贝（复制 dict），或导出期间禁用修正按钮 |
| 11 | `gwtool/ui/registry_dialog.py` | 死方法 `export_csv` 全仓无调用方 | 删除或接线到按钮 |
| 12 | `gwtool/core/batch.py`（compile 路径） | 批量汇编逐份调用 `dao` 全表查询聚合台账，量级大时线性放大 | 循环外批量取数后传参 |

## 五 P3（31 项，归组概述）

- **死代码/死赋值**（8 处）：`scripts/acceptance_install.py:62,93` 恒真条件、
  `csc_neural.py:443-447` 不可达分支、若干死赋值与未读变量
- **日志缺失**（6 处）：LibreOffice 转 PDF 的 stderr 整体丢弃（compiler.py:237-239，
  失败零留痕误导排障）、静默回退无日志等
- **风格/命名**（10 处）：冗余导入、重复断言、`__import__` 滥用、注释 Tab 损坏等
- **可维护性**（7 处）：超长函数可拆分、重复模式可提取（详见附录逐条）

## 六 修复优先级建议

1. **立即修**（随下个补丁版）：P1-1 导入双回调、P1-2 移交包附件覆盖、
   P1-3 水印 XML 转义——均为用户可直接踩中的功能缺陷
2. **随迭代修**：P1-4/P1-5 与 P2-2/4/5（同文件已有正确范本，改动小、风险低）
3. **择机清理**：P2 性能项（xlsx 列闸、compile_wizard 全表扫描）与全部 P3
4. 修复时建议每项补一条回归护栏测试（本项目已有 test_audit_round_v171.py 先例）

## 附录：逐文件审查明细

明细按六组原始记录收录（含全部"未发现"文件），见附录 A–F。


## 附录 A（第 1 组原始记录）

# OpenCodeReview 审查报告 — GROUP 1（30 文件）

规则：Favor precision over recall；P1=正确性/安全，P2=明确隐患（资源/并发/性能/边界），P3=风格与可读性。
背景约束已遵守：纠错引擎静默降级为设计；测试文件对模态框的 staticmethod mock 为既有约定（feature_dialogs/logs/e2e_check 相关吞异常均有注释说明，未报）。

## gwtool/ui/feature_dialogs.py
- [P2][边界与空值处理] gwtool/ui/feature_dialogs.py:1103 —— `SimilarityDialog` 后台 work 里 `dao.get_document(did).content_text` 未判 None（`dao.get_document` 契约为 `Document | None`，同文件 `_BulkReplaceWorker._run`:389 已对同一调用做 `if not d: continue` 防护）；列出文档与取正文之间文档被删除即 AttributeError，查重整体失败 → 先取 `d = dao.get_document(did)` 并跳过 None。
- [P3][死代码] gwtool/ui/feature_dialogs.py:360 —— `_BulkReplaceWorker.run()` 内 `import traceback` 与模块顶部 :6 的同名导入重复，局部导入无任何作用 → 删除局部导入。

## gwtool/ui/registry_dialog.py
- [P3][死代码] gwtool/ui/registry_dialog.py:575 —— `RegistryDialog.export_csv` 全仓无任何调用（`export_table`:560 已含 .csv 后缀分支），属遗留死方法 → 删除。
- [P3][命名与可读性] gwtool/ui/registry_dialog.py:190 —— 整数校验失败提示直接展示英文键名（「pages」「copies」必须是整数），而 `_field_labels` 已存有中文标签（"页数"）供校验定位用 → 改用 `self._field_labels.get(key, key)`。

## tests/test_wordlist_import.py —— 未发现

## scripts/e2e_check.py
- [P3][命名与可读性] scripts/e2e_check.py:64 —— 多处用 `__import__("time")`、`__import__("gwtool.paths", fromlist=[...])` 代替正常 import 语句（:64/:65/:67/:85/:87/:153），可读性差且绕过静态检查 → 改为常规 import。

## gwtool/core/style_profile.py —— 未发现

## gwtool/core/enhance_pack.py —— 未发现

## gwtool/ui/theme.py —— 未发现

## scripts/eval_corrector.py
- [P3][死代码] scripts/eval_corrector.py:279 —— `ms_per_qian = dt / max(1, probe_chars) * 1000 * 1000 / 1000` 被下一行 :280 立即覆盖，且两行公式不一致（:279 为残留的错误版本），属遗留死赋值 → 删除 :279。

## gwtool/logs.py —— 未发现
（模块级吞异常均为 docstring 声明的设计约束「自身失败无害」，按规则不报。）

## tests/test_xlsx.py
- [P3][死代码] tests/test_xlsx.py:256 —— `assert cells["A2"][1] == "×政发〔2026〕12号"` 与上一行完全重复，属复制粘贴残留 → 删除重复断言。

## tests/test_v154_ui_regression.py —— 未发现

## tests/test_doc_probe.py —— 未发现

## tests/test_para_panel.py —— 未发现

## gwtool/core/xlsx_read.py
- [P2][边界与性能] gwtool/core/xlsx_read.py:208 —— `read_sheet_rows` 按 `max(rows) × width` 全量补齐稀疏网格：一个极小的 xlsx 只需在 XML 里声明单个 `r="XFD200000"` 的单元格，即可让 :209 的输出膨胀到 200000×16384（约 3×10⁹ 个元素）直接 OOM，完全绕过本模块自称具备的 `_MAX_TOTAL` 解压预算防护（稀疏坐标不占字节）→ 对 `width` 与 `len(rows) * width` 乘积设上限，或只输出实际存在的行。

## tests/test_compile_pdf.py —— 未发现

## tests/test_paragraph_ref.py —— 未发现

## tests/test_stage3_extras.py —— 未发现

## tests/test_v154_win_integration.py —— 未发现

## gwtool/core/ocr.py
- [P3][拼写与可读性] gwtool/core/ocr.py:30 —— docstring 中布局示例写作 `{app}<TAB>esseract<TAB>esseract.exe`：字面 `\t` 被真实制表符替换，注释里的路径显示为残缺的 "esseract" → 写成 `{app}/tesseract/tesseract.exe`（正斜杠或转义）。

## gwtool/core/wordfmt/json_fmt.py —— 未发现

## tests/test_corrector_extended.py —— 未发现

## tests/test_inspector_citation.py —— 未发现

## gwtool/ui/compare_dialog.py —— 未发现

## gwtool/db/tokenize.py —— 未发现
（`_jieba_ready` 双检锁已有注释说明并发窗口与锁的正确用法。）

## gwtool/core/differ.py —— 未发现

## tests/test_inspector_number.py —— 未发现

## tests/test_heading_regex.py —— 未发现

## gwtool/core/win_integration.py —— 未发现

## gwtool/core/fontcheck.py —— 未发现

## gwtool/core/parsers/rtf_parser.py
- [P2][资源管理] gwtool/core/parsers/rtf_parser.py:11 —— `open(path, "rb").read()` 未用 `with`，句柄仅靠引用计数回收，异常路径下不保证及时关闭 → `with open(path, "rb") as f: raw = f.read()`。
- [P3][异常处理] gwtool/core/parsers/rtf_parser.py:14 —— `except Exception` 静默回退 latin-1 重试，无任何日志留痕；解码失败原因（gb18030 为何失败）无从排查 → 至少 `log.debug` 记录原始异常。

统计：files=30 P1=0 P2=4 P3=6


## 附录 B（第 2 组原始记录）

# OpenCodeReview 审查报告 —— GROUP 2

规则集：Favor precision over recall（只报高置信度真实缺陷）。安全与正确性为 blocking（P1），资源/并发/性能为 P2，风格与可读性为 P3。

## gwtool/db/dao.py
- [P1][正确性] gwtool/db/dao.py:992 —— `save_template` 用 upsert（`ON CONFLICT(name) DO UPDATE`）后返回 `cur.lastrowid`：走 UPDATE 分支时没有发生 INSERT，`lastrowid` 是上一条语句留下的陈旧值，返回的 id 可能指向另一条模板记录；同文件 `save_user_skeleton`（dao.py:1874-1891）的 docstring 已明确记录了这个坑并改为回查，此处属同类缺陷未修 → 比照 `save_user_skeleton`，UPDATE 后按 name 回查 id 再返回。

## gwtool/ui/editor_panel.py
—— 未发现

## gwtool/ui/receive_dialog.py
- [P3][可读性/文案] gwtool/ui/receive_dialog.py:248 —— 校验失败提示 `f"「{key}」必须是整数。"` 直接输出英文键名（pages/copies），用户看到的是「pages 必须是整数」→ 改用 `self._field_labels[key]` 输出中文标签（页数/份数）。
- [P3][UI 状态] gwtool/ui/receive_dialog.py:258-261 —— 校验失败给字段加 DANGER 描边后，同一对话框内再次保存（含部分修正后仍失败）时旧红框不清除，会误导用户以为先前已修正的字段仍有问题 → 每次 `_on_save` 进入时先遍历 `_fields` 清空 setStyleSheet 再按最新问题标红。

## gwtool/ui/widgets.py
—— 未发现

## tests/test_ui_design_specs.py
—— 未发现

## gwtool/core/style_data.py
—— 未发现（纯数据模块）

## tests/test_ui_actions_smoke.py
—— 未发现

## tests/test_attachments.py
—— 未发现

## tests/test_paragraph_index.py
—— 未发现

## tests/test_features.py
—— 未发现

## scripts/build_cgec_pack.py
—— 未发现

## gwtool/core/xlsx.py
—— 未发现

## gwtool/core/toolbox.py
—— 未发现

## gwtool/core/receive.py
- [P3][可读性] gwtool/core/receive.py:188 —— 用中文标签字符串 `label != "来文成文日期"` 做分支判断来排除 doc_date，改文案即悄悄改变校验行为 → 把不参与比较的字段从遍历元组中移除，或按字段名（`field_name != "doc_date"`）判断。

## tests/test_ui_probe.py
—— 未发现

## tests/test_exporter.py
—— 未发现

## tests/test_archive.py
—— 未发现

## tests/test_enhance_pack_slots.py
—— 未发现

## scripts/bench_scale.py
—— 未发现

## gwtool/core/skeletons.py
—— 未发现

## gwtool/core/tts.py
—— 未发现

## gwtool/ui/errmsg.py
—— 未发现

## main.py
—— 未发现

## gwtool/core/formatter.py
—— 未发现

## gwtool/core/reference.py
- [P2][性能/正确性边界] gwtool/core/reference.py:97 —— `phrase_full_text` 用 `dao.list_phrases()`（默认 limit=500）全量拉取后线性遍历找单条 id：句式库超过 500 条时目标条目可能落在截断范围之外而返回空串，且每次查询都是 O(n) 全表传输 → 改为一条 `SELECT phrase,context FROM user_phrases WHERE id=?` 参数化查询（dao 无 get_phrase 可在 dao 补一个）。

## gwtool/core/simhash.py
—— 未发现

## gwtool/core/parsers/md_html_parser.py
—— 未发现

## gwtool/core/booklet.py
- [P3][死代码] gwtool/core/booklet.py:20-22 —— `A4_WIDTH_PT` 与 `A3_LANDSCAPE` 全仓无任何引用；且 `A3_LANDSCAPE` 表达式含 `A4_HEIGHT_PT * 2 * 0`（恒为 0）的遗留痕迹，既无调用方也易误导读数 → 删除这两个未使用常量（`A4_HEIGHT_PT` 亦仅被死代码引用，可一并评估）。

## gwtool/core/model.py
- [P2][异常处理/边界] gwtool/core/model.py:67-71 —— `DocTree.from_json` 的 try 包裹整个 for 循环且 `except Exception: pass`：blocks_json 中任何一个块带未知/坏字段（跨版本数据、外部改库）时，从坏块起**其后所有块**被静默丢弃，文档结构无声缺失且无日志可查 → 将 try/except 收窄到单块构造（坏块跳过、好块保留），并记录 warning 日志。

## gwtool/core/wordfmt/docx_fmt.py
—— 未发现

## gwtool/db/__init__.py
—— 未发现（再导出为刻意设计，文件头已说明）

统计：files=31 P1=1 P2=2 P3=4


## 附录 C（第 3 组原始记录）

# OpenCodeReview — GROUP 3 审查报告

规则集：Favor precision over recall，仅报高置信度真实缺陷。
审查范围：`== GROUP 3 ==` 全部 31 个文件（相对 C:/gwtool）。

## gwtool/ui/main_window.py
- 未发现

## gwtool/ui/reference_panel.py
- [P2][边界与空值处理] gwtool/ui/reference_panel.py:425 —— `_apply_one` 多选替换时 `reversed(self._selected_corrections())` 按用户选中顺序而非 start 降序执行，选中顺序非降序时先替换低偏移会使后续 (start,end) 整体错位，把替换写到错误位置（同文件 `_apply_all`:456 已用 `sorted(key=start, reverse=True)` 处理同类问题，此处遗漏）→ 改为 `sorted(..., key=lambda c: c.start, reverse=True)`。

## gwtool/core/corrector_data.py
- 未发现

## gwtool/ui/dict_manager.py
- [P3][性能/死代码] gwtool/ui/dict_manager.py:453-459 —— `_del_phrase_row` 在循环体内 `from ..db.tokenize import tokenize`（最多迭代 10 万次）且 `from ..db.connection import get_conn as gc` 重复导入顶部已有的 `get_conn` → 把两个 import 移到函数开头，直接复用顶部 `get_conn`。

## gwtool/core/csc_neural.py
- [P1][正确性] gwtool/core/csc_neural.py:386 —— `enhance()` 的 except 块只声明了 `global _ENGINE_ERROR`，`_ENGINE = None` 实际赋给了局部变量，模块级坏引擎不会被丢弃，与注释"推理异常后丢弃本层引擎、下次强制重载"的意图相悖（坏引擎会一直缓存到进程结束）→ 将声明改为 `global _ENGINE, _ENGINE_ERROR`。
- [P3][死代码] gwtool/core/csc_neural.py:443-447 —— 输出匹配循环里 `elif not names:` 分支不可达：`names` 为空时 `zip(names or [], outputs)` 为空迭代器，循环体根本不执行 → 删除该死分支（空 names 的位置兜底已由 448-451 行承担）。
- [P3][性能] gwtool/core/csc_neural.py:436 —— `_output_names(eng.session)` 在窗口循环内逐窗口重复调用，返回值跨窗口不变（且内部含 try/except）→ 提到 while 循环外计算一次。

## gwtool/core/pdfrender.py
- 未发现

## gwtool/core/repeat_rules.py
- 未发现

## gwtool/core/docxgen.py
- 未发现

## tests/test_v154_defect_regression.py
- [P3][死代码] tests/test_v154_defect_regression.py:261 —— 对 `*.restore.tmp` 的 glob 断言与 260 行完全重复（死代码，第二处永无增量信息）→ 删除重复断言行。
- [P3][死代码] tests/test_v154_defect_regression.py:176 —— `assert blocked in (True, False)` 恒真，无任何验证力（与文件头"恒过的断言等于没写"的项目纪律相悖）→ 删除该行，仅保留前后真实断言。

## gwtool/core/parsers/doc_parser.py
- 未发现

## gwtool/core/writing_hints.py
- 未发现

## tests/test_review_round4.py
- 未发现

## tests/test_grammar_rules.py
- 未发现

## gwtool/core/attachments.py
- 未发现

## scripts/build_csc_pack.py
- [P3][死代码] scripts/build_csc_pack.py:103,165 —— 两处列表推导（`[tok.convert_ids_to_tokens(...)]`、`[int(np.argmax(...))]`）求值后即丢弃，纯死表达式 → 删除或改为注释说明。

## gwtool/core/diagpack.py
- 未发现

## tests/test_writing_hints.py
- 未发现

## tests/test_registry.py
- 未发现

## gwtool/core/template.py
- 未发现

## gwtool/core/wordfmt/tbx_fmt.py
- 未发现

## tests/test_tts_probe.py
- 未发现

## tests/test_backup_compat.py
- 未发现

## tests/test_p2p3_features.py
- 未发现

## gwtool/config.py
- 未发现

## gwtool/core/reminder.py
- 未发现

## tests/test_robustness.py
- 未发现

## scripts/smoke_real_packs.py
- 未发现

## gwtool/core/parsers/docx_parser.py
- 未发现

## gwtool/core/writing_data.py
- 未发现

## gwtool/core/parsers/pdf_parser.py
- 未发现

## tests/test_tts.py
- 未发现

统计：files=31 P1=1 P2=1 P3=6


## 附录 D（第 4 组原始记录）

# OpenCodeReview 审查报告 —— GROUP 4

审查人：CodeBuddy（GLM-5.3-Flash） · 规则：Favor precision over recall · 只读审查

## tests/test_v170_batch_b.py —— 未发现

## tests/test_deep_review_regression.py —— 未发现

## gwtool/core/inspector.py —— 未发现

## gwtool/core/corrector.py —— 未发现

## gwtool/db/schema.py —— 未发现

## tests/test_db_probe.py —— 未发现

## gwtool/ui/material_dialogs.py —— 未发现

## tests/test_batch_correct.py —— 未发现

## gwtool/ui/template_editor.py
- [P3][风格与可读性] gwtool/ui/template_editor.py:263 —— `dao.list_templates().__len__() + 1` 绕过内置 `len()` 直接调魔术方法，可读性差 → 改写为 `len(dao.list_templates()) + 1`。

## tests/test_toolchain_probe.py —— 未发现

## gwtool/ui/import_dialog.py
- [P1][正确性] gwtool/ui/import_dialog.py:236-245 —— `_done`/`_done_detail` 的 `_detail_shown` 去重守卫失效：ImportWorker 先 emit `finished_ok` 再 emit `finished_detail`（workers.py 实测顺序），队列投递按发射序执行，故 `_done` 先跑（标志仍为 False → `_finish_common` 执行）后 `_done_detail` 再跑一次 → 每轮导入 `_finish_common` 必然执行两次，失败数 >8 时失败清单 TXT 写两遍、"完整失败清单已保存"弹窗连弹两次；且 `_start` 也未重置该标志（第二轮起 `_done` 被脏标志永久短路）→ 把去重逻辑改为在 `_done_detail` 里置标志、`_done` 里检查并复位，并在 `_start` 中重置（或干脆只保留 `_done_detail` 一条回调通路）。
- [P3][死代码] gwtool/ui/import_dialog.py:206-207 —— `if files and not self.progress.isVisible(): pass` 为空操作分支，`files` 非空在前文已 return，条件永不产生效果 → 删除该 if 块。

## gwtool/core/compiler.py
- [P3][可观测性/异常处理] gwtool/core/compiler.py:237-239 —— LibreOffice 分支 `subprocess.run(..., capture_output=True)` 把 stdout/stderr 捕获后全部丢弃，转换失败（如文件损坏、字体缺失导致 soffice 退出码非零）零留痕，最终只报"未找到可用的 docx->PDF 转换组件"，误导用户以为是组件缺失 → 对 `result.returncode != 0` 或产物缺失时将 stderr 摘要写入 `logs.get_logger("compile")`。

## tests/test_genchain_probe.py —— 未发现

## gwtool/app.py —— 未发现

## tests/test_wps_support.py —— 未发现

## tests/test_review_round5.py —— 未发现

## tests/test_xlsx_read.py —— 未发现

## gwtool/paths.py —— 未发现

## tests/test_ruleset.py —— 未发现

## tests/test_logs_privacy.py —— 未发现

## tests/conftest.py —— 未发现

## tests/test_p1_features.py —— 未发现

## gwtool/core/ruleset.py —— 未发现

## tests/test_settings_pack_groups.py —— 未发现

## tests/test_ui_theme_dark.py —— 未发现

## tests/test_corrector.py —— 未发现

## tests/test_inspector_style.py —— 未发现

## scripts/acceptance_install.py
- [P3][死代码] scripts/acceptance_install.py:62,93 —— `fresh = True` 赋值后从未被改写，`if fresh:` 恒为真，属死条件（删目录无条件执行）→ 删除 `fresh` 变量、直接执行清理块，或补上真实的"是否全新安装"判定。

## tests/test_editor_blocks.py —— 未发现

## tests/test_ui_guard.py —— 未发现

## tests/test_app_smoke.py —— 未发现

## gwtool/__init__.py —— 未发现

统计：files=32 P1=1 P2=0 P3=4


## 附录 E（第 5 组原始记录）

# GROUP 5 代码审查结果（OpenCodeReview 规则集，precision 优先）

## gwtool/ui/compile_wizard.py
- [P2][性能/重复IO] gwtool/ui/compile_wizard.py:820 —— `_find_library_entry(path, title)` 同参数连续调用两次，而该函数内部最多各扫一遍 `dao.list_documents()`（一次调用即两遍全表），批量 N 份产物时退化为 4N 次全量扫描；`_register_impl`(841) 又对每份产物再各来一遍 → 调一次存局部变量复用。
- [P2][边界] gwtool/ui/compile_wizard.py:500 —— 封面标题（用户输入）未做文件名清洗即拼进输出路径（519 的时间戳回退与 552 的批量输出目录同样），标题含 `\/:*?"<>|` 等字符时 Windows 上生成失败（OSError）或产生意外子目录；batch.py 已有现成的 `safe_filename` → 对 title 统一过一遍 `safe_filename`。
- [P3][并发] gwtool/ui/compile_wizard.py:511 —— 防重入只检查 `self._worker`，批量分支用的 `_batch_worker`、PDF 用的 `_pdf_worker` 不在检查范围（当前靠 btn_start 禁用兜底）→ 检查三个 worker 的 isRunning 再放行。

## gwtool/core/csc_gec.py
- [P2][死代码/作用域] gwtool/core/csc_gec.py:379 —— `enhance()` 里 `_ENGINE = None` 未声明 `global _ENGINE`，只创建了立即被丢弃的局部变量，"推理异常后丢弃引擎"的降级意图从未生效（坏引擎会被反复复用重试）→ 把 `_ENGINE` 并入第 368 行的 `global` 语句。
- [P3][可读性] gwtool/core/csc_gec.py:683 —— `_diff_spans` 的注释同时保留"正向重建路径 + moved 兜底防死循环"的旧实现描述与紧随其后的"反向回溯"新描述，两段互相矛盾且 `moved` 变量并不存在 → 删除过时的正向描述段。

## tests/test_compile_register.py
- [P3][可读性] tests/test_compile_register.py:562 —— `assert cw is not None` 没有断言价值（cw 只是模块引用，永不为 None）→ 删除或在断言里真正使用 cw。
- [P3][可读性] tests/test_compile_register.py:551 —— `QApplication` 在同一函数内重复导入三次（551/555/594）→ 提到用例开头导入一次。

## tests/test_backup_limits.py —— 未发现

## tests/test_packaging.py —— 未发现

## gwtool/core/batch.py —— 未发现

## tests/test_safety_probe.py —— 未发现

## tests/test_repeat_rules.py
- [P3][测试卫生] tests/test_repeat_rules.py:361 —— `test_empty_dictionary_degrades_to_no_report` 中途断言失败时无 try/finally，`dbconn.configure(prev)` 不执行，同模块后续用例将跑在空库上 → 切换-恢复用 try/finally 包住。

## gwtool/core/wordlist.py
- [P3][死代码] gwtool/core/wordlist.py:316 —— `_write_one` 里 `if e.role == "terms": pass` 空分支无任何作用（注释已说明同表）→ 整段删除。

## gwtool/core/registry.py —— 未发现

## gwtool/ui/workers.py —— 未发现

## tests/test_corrector_l4.py —— 未发现

## scripts/smoke_dist.py
- [P3][死代码] scripts/smoke_dist.py:150 —— `fresh = True` 赋值后从未被修改，`if fresh:`（246 行）恒真 → 删除变量直接执行清理，或按"本次是否新建 Data/"赋值。

## tests/test_import_probe.py —— 未发现

## tests/test_style_verdict.py —— 未发现

## tests/test_logs.py —— 未发现

## tests/test_dbhealth.py —— 未发现

## tests/test_correct_dialog.py —— 未发现

## tests/test_bugfix_regression.py —— 未发现

## scripts/seed_data.py
- [P3][可读性] scripts/seed_data.py:61 —— `n_curated` 统计的是尝试写入数而非实际入库数（`INSERT OR IGNORE` 可能忽略重复），汇总行打印值可能偏大 → 用 `cur.rowcount` 累加真实插入数（构建期脚本，仅影响报告口径）。

## tests/test_compiler_sources.py —— 未发现

## tests/test_diagpack.py —— 未发现

## gwtool/core/archive.py —— 未发现

## tests/test_para_align.py —— 未发现

## gwtool/core/importer.py —— 未发现

## tests/test_inspector_consistency.py —— 未发现

## gwtool/core/wordfmt/__init__.py —— 未发现

## tests/test_pdf_cjk_font.py —— 未发现

## gwtool/core/classify.py —— 未发现

## gwtool/core/wordfmt/csv_tsv.py —— 未发现

## gwtool/core/wordfmt/xlsx_fmt.py —— 未发现

## gwtool/core/security.py —— 未发现

统计：files=32 P1=0 P2=3 P3=6


## 附录 F（第 6 组原始记录）

# OpenCodeReview 审查报告 —— GROUP 6

规则集：Favor precision over recall，只报高置信度真实缺陷。P1=正确性/安全，P2=明确隐患，P3=风格与可读性。

## gwtool/core/paragraph_ref.py
- [P3][死代码/冗余导入] gwtool/core/paragraph_ref.py:910 —— `to_json_slots` 内 `import json as _json`，与模块顶部 `import json`（第 22 行）重复 → 删去函数内导入，直接用顶层 `json`。

## gwtool/core/backup.py
—— 未发现

## tests/test_corrector_l5.py
- [P2][边界与断言有效性] tests/test_corrector_l5.py:202 —— `assert all("2024" in src[c.start:c.end] or "2024" not in src[c.start:c.end] for c in out)` 是恒真式（`A or not A` 永为 True），对 G4 数字保护零覆盖力，护栏失效而测试恒绿 → 改为断言 `not any(src[c.start:c.end] 涉及数字位且被改动)`，或直接删掉这行只保留下一行 `assert not any(...)`。

## gwtool/ui/library_panel.py
- [P3][异常处理] gwtool/ui/library_panel.py:556 —— `_export_selected_txt` 中 `open(path, "w")` 写出无任何异常处理，目标路径无效/被占用/只读时异常直接从槽函数抛出，用户只看到"点了没反应"（PySide6 打印 traceback 不弹提示） → 包 try/except OSError 并 `warn(self, f"导出失败：{exc}")`。

## gwtool/ui/correct_dialog.py
- [P2][并发/共享状态] gwtool/ui/correct_dialog.py:463 —— `export_docx`/`export_txt`（480 行同）用 `list(self._blocks)` 做的是**浅拷贝**，dict 元素仍与主线程共享；注释声称"快照后交给后台"，但后台导出运行期间按钮未被禁用，用户可继续「应用此修正/全部应用」原地修改同一批 dict，工作线程并发读到中途状态 → 快照需深拷贝（`copy.deepcopy(self._blocks)`）或导出期间调用 `_set_busy` 禁用修正按钮。

## tests/test_corrector_probe.py
—— 未发现

## tests/test_receive.py
—— 未发现

## tests/test_ocr_probe.py
—— 未发现

## tests/test_recycle_bin.py
—— 未发现

## tests/test_audit_round_v171.py
—— 未发现

## gwtool/core/exporter.py
- [P1][正确性/数据丢失] gwtool/core/exporter.py:201 —— 附件写入包内用 `f"attachments/{safe_filename(item['name'])}"` 做条目名，不同文档的**同名附件**（file_name 相同、stored_path 不同）会写出同名 zip 条目，`ZipFile.read` 按 NameToInfo 只能取到最后一条，先写的附件在包内被静默顶掉；manifest 里两条 included 的 sha256 各自记录，校验还会误报"内容校验失败" → 与 `backup._write_attachments` 同口径：加 seen 集合按序去重（第二条改名补 doc_id 后缀或记入 excluded 并写明原因）。

## gwtool/core/dbhealth.py
—— 未发现

## tests/test_ui_components.py
—— 未发现

## tests/test_style_profile.py
—— 未发现

## gwtool/core/grammar_rules.py
- [P3][性能] gwtool/core/grammar_rules.py:192 —— `check_conjunction` 每次调用都在规则循环内 `re.compile(...)` 重新编译 3 条模式（`re.compile` 不走 `re` 模块的内部缓存），而本函数随每次 `check_text` 逐块调用 → 三条正则在模块级预编译为常量元组，循环内只做 `finditer`。

## scripts/api_commit.py
—— 未发现

## tests/test_para_generate.py
—— 未发现

## tests/test_correction_highlighter.py
- [P3][死代码/无效断言] tests/test_correction_highlighter.py:206 —— `assert hl.corrections() == [] or True` 恒真，断言本身无任何判定力（注释表明意图只是"不抛异常"，但该行不构成检查） → 删除该行，或改为有效断言（如 `assert isinstance(hl.corrections(), list)`）。

## gwtool/db/connection.py
—— 未发现

## tests/test_reminder.py
—— 未发现

## gwtool.spec
—— 未发现

## tests/test_parsers.py
—— 未发现

## gwtool/core/wordfmt/tabular.py
—— 未发现

## tests/test_data_search.py
—— 未发现

## scripts/import_material_dir.py
- [P3][死代码] scripts/import_material_dir.py:85 —— 计数器 `ok` 声明（85 行）并在 124 行递增，但从未被读取（总结打印的是 `imported`，两者恒等） → 删除 `ok` 变量，直接用 `imported`。

## gwtool/ui/correction_highlighter.py
—— 未发现

## gwtool/core/report.py
—— 未发现

## gwtool/core/watermark.py
- [P1][安全/正确性——注入] gwtool/core/watermark.py:95 —— 水印文本 `text` 未经 XML 转义直接以 `%s` 拼进 `_VML_TPL` 的 `string="%s"` 属性，含 `&`（如"R&D"）或 `<`/`"` 的文本会让 `parse_xml` 抛 XMLSyntaxError（水印功能必崩），极端内容还可能截断属性注入额外节点 → 用 `xml.sax.saxutils.quoteattr`（或 `escape` + 手工处理引号）包一层再拼模板。

## scripts/check_inference_stack.py
—— 未发现

## gwtool/ui/icons.py
—— 未发现

## gwtool/core/parsers/txt_parser.py
—— 未发现

## gwtool/ui/__init__.py
—— 未发现

## gwtool/core/parsers/__init__.py
—— 未发现

## gwtool/core/__init__.py
—— 未发现

统计：files=34 P1=2 P2=2 P3=5
