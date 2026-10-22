# 04 发文/收文台账、督办、归档 + 资料库检索/回收站/历史版本/查重/对比 —— 逐项实测与优化建议

| 项 | 值 |
|---|---|
| 审计对象 | `<仓库根>`，**v1.8.0**（HEAD `50c6275`，`__version__ = "1.8.0"`） |
| 范围 | `core/registry.py`、`core/receive.py`、`core/reminder.py`、`core/archive.py`、`ui/registry_dialog.py`、`ui/receive_dialog.py`、`ui/library_panel.py`、`db/tokenize.py`、4 张 FTS 虚表、`ui/material_dialogs.py`、`feature_dialogs.py::SnapshotsDialog/SimilarityDialog`、`ui/compare_dialog.py`、`core/simhash.py`、`core/differ.py` |
| 方式 | **只读实测**：全部在 `%TEMP%` 临时数据目录 + 临时库（`paths.set_app_data_dir` + `dbconn.configure`）中造数据；未改动任何源码/测试；未跑全量 pytest/e2e/打包；未联网 |
| 探针 | `doc/_audit_v19/probes/p1…p7*.py`（用后已删，关键片段内联在本文；备份在 `%TEMP%\audit_v19_backup\`） |
| 结论 | **P0 = 0 条；P1 = 6 条；P2 = 10 条**；另有 9 项实测通过、6 项不建议做、6 项待验证 |

> 说明：本报告按 lead 指定的 P0/P1/P2 三级。**P0 一条也没有** —— 本范围未发现数据丢失、静默改正文或安全漏洞；回收站级联、附件文件删除、FTS 特殊字符防护均实测通过（见 §2.6 与 §5）。

---

## 1 功能清单与入口

| # | 功能 | 入口（UI） | 逻辑层 | 表 / 索引 |
|---|---|---|---|---|
| 1 | 发文登记台账（增删改查、自动取号、组合筛选、统计、CSV/XLSX 导出） | 主窗口「台账 — 发文登记…」`main_window.open_registry` → `RegistryDialog` | `core/registry.py`；`dao.add/update/delete/list_dispatch`、`dispatch_stats`、`dispatch_monthly_counts`、`max_doc_no_serial` | `dispatch_register`（`idx_dispatch_no` 等 3 个**普通**索引，无唯一约束） |
| 2 | 汇编产物回填台账（去重、幂等、同号拦截） | 汇编向导完成后弹 `RegisterFromCompileDialog` | `ui/compile_wizard.py:866-905`、`registry.from_compile` | 同上 |
| 3 | 收文登记台账（登记、办理状态、筛选、统计、导出） | 「台账 — 收文登记…」→ `ReceiveDialog` / `ReceiveForm` | `core/receive.py`；`dao.add/update/list_receive`、`receive_stats`、`max_reg_no_serial` | `receive_register` |
| 4 | 督办提醒（逾期 / N 日内到期；状态栏角标；跳台账） | 状态栏「督办：…」按钮 `main_window.refresh_reminders/open_reminders` | `core/reminder.py`、`dao.pending_receive`、`receive.days_left/split` | `receive_register`（`due_date`、`status`） |
| 5 | 归档（批量编档号、保管期限、移交清单 DOCX/XLSX） | 收文台账工具栏「归档」（`receive_dialog.py:464-509`） | `core/archive.py`（`suggest_batch`/`batch_archive`/`build_manifest_tree`） | `receive_register.archive_no/retention/archive_date` |
| 6 | 资料库列表 / 分类树 / 类型过滤 / 排序 / 多选批量 | 左侧 `LibraryPanel` | `dao.list_documents`、`count_documents_by_category`、`attachment_counts`、`bulk_update_tags` | `documents`、`categories` |
| 7 | 全文检索（文档级 FTS5，jieba 预分词） | 资料库搜索框（回车/`F3`/`Esc`） | `db/tokenize.build_match_query`、`dao.search_documents/_fts_search` | `documents_fts`（另有 `dictionary_fts`/`phrases_fts`/`paragraphs_fts`） |
| 8 | 段落检索（参考段落） | 右侧参考面板 | `dao.search_paragraphs_fts` | `paragraphs`、`paragraphs_fts`、`fts_index_state` |
| 9 | 回收站（软删除、恢复、彻底删除、清空） | 资料库「回收站」按钮 / 主菜单 | `dao.delete_document/restore_document/purge_document`、`core/attachments.purge_documents` | `documents.deleted_time` + `attachments` + `snapshots` + `paragraphs*` |
| 10 | 历史版本（每文档 30 版、diff 预览、回滚、删除单条） | 主菜单「历史版本…」→ `SnapshotsDialog` | `dao.add_snapshot/_prune_snapshots/list_snapshots/get_snapshot`、`differ.diff_to_html` | `snapshots` |
| 11 | 相似查重（SimHash 粗筛 + Jaccard 精算） | 主菜单「相似查重…」→ `SimilarityDialog` | `core/simhash.find_similar`、`dao.all_simhashes` | `documents.simhash`、`idx_documents_simhash` |
| 12 | 文档对比（资料库文档 / 本地文件，双栏红绿） | 主菜单「文档对比…」→ `CompareDialog` | `core/differ`、`core/importer.parse_any` | — |

---

## 2 实测记录

环境：`.\.venv\Scripts\python.exe`（Python 3.13.14）、`$env:PYTHONIOENCODING='utf-8'`、Qt `offscreen`；本机 Windows 11 x64。所有脚本自建临时库，结束后 `rmtree`。

### 2.1 探针与复现命令

```powershell
cd <仓库根>; $env:PYTHONIOENCODING='utf-8'; $env:QT_QPA_PLATFORM='offscreen'
# 发文/收文/督办/归档
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p1_registry_receive_archive.py
# 检索/FTS 边界/回收站/快照/查重/对比
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p2_library_recycle.py
# 附件级联（真实 attachments.add API）
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p3_attachment_purge.py
# 存储归因 / 分类计数 / 检索上限 / 查重规模
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p4_storage_counts.py
# 分类计数（去重规避）/ 空白查重 / 快照全局列表
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p5_counts_blank_snapshot.py
# 资料库面板 UI 路径耗时（3000 篇 / 100 分类）
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p6_ui_path.py
# 对比/导入编码（GBK/UTF-8/BOM/UTF-16/CRLF）
.\.venv\Scripts\python.exe doc\_audit_v19\probes\p7_encoding.py
# 官方基线（只读临时库）
.\.venv\Scripts\python.exe scripts\bench_scale.py
```

### 2.2 规模与延迟（3000 篇）

| 指标 | 数值 | 来源 |
|---|---|---|
| 批量入库 3000 篇（SQL 直插） | 0.02 s | `scripts/bench_scale.py` |
| `rebuild_fts`（jieba 预分词 + FTS 重建） | 2.21 s（探针）/ 未计入检索指标 | p2 / bench |
| `rebuild_paragraphs`（全量回填 3000 段） | 1.28 s（探针）/ 1.52 s（bench）；二次重切 0 | p2 / bench |
| `dao.list_documents()` 3000 行 | **17.4 ms**（median 16.0） | bench / p2 |
| `dao.count_documents()` | 0.1–0.2 ms | bench |
| 文档检索（命中 50 条） | **10.7 ms**（median 9.4） | bench / p2 |
| 段落检索（命中 30 段） | **15.4 ms**（median 9.0） | bench / p2 |
| 库体积（3000 篇，含段落索引） | 9.7 MB | bench / p2 |
| **资料库面板整刷 `LibraryPanel.reload()`** | **271 ms**（其中 `_fill_categories` **229 ms**、`_reload_docs` **118 ms**，100 个分类） | p6 |
| **面板构造（含首次 reload）** | 906 ms | p6 |
| **UI 检索 100 条**（3000 篇全命中） | **16.1 ms**；列表项 100；文案 `检索到 100 处` | p6 |

结论：**「1 秒内检索」达成且余量很大**（DAO 10.7 ms、UI 16.1 ms）。真正的规模热点是**分类树刷新（229 ms）**，不是检索（详见 P2-1）。

### 2.3 存储归因（回答 "+88.9% 超验收线" 是不是真问题）

`bench_scale.py` 输出（原文）：

```
   3000     3000      1.52     0.018     0       15.4     30     88.9
段落索引体积增量：88.9%（验收线 < 40%）
```

`PRAGMA dbstat`（3000 篇，按表页字节，MB）：

| 表 | 占用 | 说明 |
|---|---|---|
| `documents` | 1.957 | 正文 `content_text` |
| `documents_fts_content` | 1.957 | 同一正文的 FTS 副本（jieba 分词后） |
| `paragraphs` | 1.957 | **同一正文再存一份**（`paragraphs.text`） |
| `paragraphs_fts_content` | 1.957 | **同一正文第三份**（段落 FTS 副本） |
| `documents_fts_data` / `paragraphs_fts_data` | 0.648 / 0.430 | FTS 倒排 |
| `fts_index_state` + 自动索引 | 0.273 | 增量重建状态 |
| 其它（各类 documents 索引） | ~0.14 | — |

- **是真问题**：段落索引把同一份正文存成 4 份；`paragraphs + paragraphs_fts` 占最终库的 **(1.957+0.430+0.035)/(9.62) ≈ 25%**，相对「加索引前」的 5.13 MB 增长 **+87.5%**（VACUUM 校正后实测），两种口径都超 40% 验收线。
- **但 bench 的百分比口径不稳定**：同一脚本 300 篇 / 1000 篇显示 **0.0% / 0.0%**，因为段落数据装进了 FTS 重建留下的空闲页（未 VACUUM），与 3000 篇的 88.9% 不可比。**判定：问题真实，度量口径需要换成"段落索引页数 / 总页数"**（见 P2-3）。

### 2.4 台账与督办关键实测输出

```
-- 连续两次取号（尚未保存）--
next_doc_no#1: ×政办发〔2026〕1号      next_doc_no#2: ×政办发〔2026〕1号
-- 同一字号重复入库 --
index_list: [('idx_dispatch_org',0),('idx_dispatch_sign_date',0),('idx_dispatch_no',0)]
doc_no=×政办发〔2026〕7号 的行数: 2（validate 拦截结果：[]）
-- 4 线程并发取号 → 写入 --
并发写入结果: [('×并发〔2026〕1号', 4)]
-- 督办（today=2026-10-22，due_soon=2）--
pending_receive(2) 返回: [('非法日期','2026-02-30'),('逾期3天','2026-10-19'),
                        ('今天到期','2026-10-22'),('明天到期','2026-10-23'),('2天后到期','2026-10-24')]
status_text: '已逾期 1 件 · 2 日内到期 3 件'
detail_lines: [剩 0 天] 非法日期 …（应办结 2026-02-30）
-- 归档：同年第二批 --
第一批: ['×局2026-0001','×局2026-0002']
第二批: ['×局2026-0001','×局2026-0002']   重复 2 个
-- 批内含已编号件 + 跨年 --
suggest_batch: [('2025 年件','×局2026-0001')]
结果: 已编号件 ×局2026-0001 / 2025 年件 ×局2026-0001（2025 年被编成 2026）
-- 在办件 + 非法期限直接调函数 --
batch_archive(在办件, "5年") -> {'archived': 1}
结果: status=已归档 retention=5年  validate 会报：['保管期限取值异常：5年']
```

分类计数（5 篇分类内 + 1 篇未分类的场景，以及 3000 篇 UI 实测）：

```
count_documents_by_category(): {1: 2, 2: 1}    count_documents() 全部: 6
UI 根节点「全部文档」显示 = sum(by_cat) = 3（实际在库 6 篇）
3000 篇 / 100 分类：分类树「全部文档」计数显示 2000，实际在库 3000
```

### 2.5 检索 / FTS / 查重 / 对比关键实测输出

```
-- FTS 边界（28 个查询串：引号/AND/OR/NEAR/通配/括号/长串/emoji…）--
全部 -> 0 条（未抛异常）；build_match_query('中国 AND') = '("中国") AND ("AND")'
-- 检索条数上限 --
库内 300 篇全部命中 → UI limit=100 → 返回 100 条，文案 '检索到 100 处'
-- 查重规模 --
300 篇全同: 2.49 s / 44,850 对；1000 篇全同: 24.86 s / 499,500 对
-- 对比 --
XSS 已转义: True；CRLF vs LF 视为相同: True；2000 行: 0.01 s / 246 KB / 相似度 97.5%
2 行 -> 5 行替换块: rows=6，统计「修改 4 行」，相似度 28.6%（按序配对）
-- 编码（p7）--
utf8/utf8bom/gbk/utf16/crlf 五种全部 ok=True，首句一致
```

### 2.6 回收站 / 快照实测输出（完整链）

```
[初始]     doc=1 fts=1 para=1 para_fts=1 idx_state=1 snap=2 att=1 文件=True
[软删除]   doc=1 fts=0 para=1 para_fts=1 idx_state=1 snap=2 att=1 文件=True
           list_documents 不含该件、文档检索 0 命中、段落检索 0 命中（deleted_time 过滤生效）
[恢复]     doc=1 fts=1 para=1 …；文档检索与段落检索均恢复命中
[彻底删除] doc=0 fts=0 para=0 para_fts=0 idx_state=0 snap=0 att=0 文件=False
附件删不掉清单: []   （真实 attachments.add 路径，p3）
快照：单文档 35 次 add_snapshot → 保留 30；全局(doc_id IS NULL) 35 次 → 保留 35（不轮转）
list_snapshots(None) 返回 65 条（全部文档 + 全局），而对话框在 doc_id=None 时就是调它
```

---

## 3 缺陷与风险

### 3.1 P0（数据损坏 / 丢失 / 静默改正文 / 安全）

**0 条。** 重点排查并实测通过：软删除/恢复/彻底删除的级联（含附件磁盘文件、段落、FTS、`fts_index_state`）、CSV 公式注入中和、对比视图 HTML 转义（`<script>` 被 `&lt;script&gt;`）、FTS 查询串注入（28 例无异常、无越权命中）。

### 3.2 P1（用户可见错误 / 业务数据错误）

#### P1-1 批量归档档号重复：第二批归档从 0001 重新开始

- **现象**：同一年度归档两批，两批的档号完全相同（`×局2026-0001/0002` 各出现两次）；批内已有档号的记录不参与计数，导致**新编档号与既有档号撞车**；跨年批次统一按"批内第一行"的年份编号（2025 年件被编成 `×局2026-0001`）。
- **证据**（`p1` 实测，见 §2.4）：3 组场景全部复现；根因 `core/archive.py:56-65`：

  ```python
  def suggest_batch(rows, prefix, year=""):
      y = year or (rows[0].receive_date[:4] if rows and rows[0].receive_date else ...)
      used = 0                      # ← 永远从 0 开始，不查库
      for r in rows:
          if (r.archive_no or "").strip():
              continue
          used += 1
          yield r, next_archive_no(prefix, y, used)
  ```

- **影响面**：归档是「移交清单 + 实物卷 + 台账」三处对齐的依据；档号重复会让档案室退卷、实物卷与台账错位。每次"再归档一批"都会发生，属必然触发。
- **复现**：`p1_registry_receive_archive.py` §4.1/4.2（连续两次 `archive.batch_archive(..., "×局", ...)`）。
- **最小修复**：`suggest_batch` 的起始序号改为"该前缀该年度已用的最大序号"，与 `dao.max_doc_no_serial` 同构（新增 `dao.max_archive_seq(prefix, year)`，`LIKE 'prefix+year-%'` + 正则取 `-(\d+)$` 的最大值）；年度按**每条记录自己的** `done_date/receive_date` 计算，不取 `rows[0]`。

#### P1-2 收文日期只校验"形状"不校验日历：非法日期入库且督办静默漏报

- **现象**：`due_date="2026-02-30"`、`"2026-13-01"`、`"2026-00-10"` 全部通过 `receive.validate` 并入库；随后 `receive.days_left()` 因 `date.fromisoformat` 抛 `ValueError` 返回 `None`，`reminder.split()` 把该件丢掉，`status_text()` 返回空 → **状态栏角标直接隐藏**，用户永远收不到这一件的督办。
- **证据**：

  ```
  receive.validate(due_date='2026-02-30') -> []            # 放行
  registry.validate(sign_date='2026-02-30') -> ['成文日期不是一个存在的日期：2026-02-30']
  非法日期已入库 id: 1 回读 due_date: 2026-02-30
  pending 返回该件: True   days_left: None   在 detail_lines: True（显示"剩 0 天"）
  ```

- **根因**：`core/receive.py:73` 只有形状正则 `^\d{4}-\d{2}-\d{2}$`，`validate`（:173-191）未用 `date()` 构造；而同项目的 `registry.validate`（`registry.py:316-334`）已修过同一个坑（注释明确写了"必须与 normalize_date 用同一把尺子"），**两份台账一条严一条松**。
- **影响面**：错误日期进库后，年度筛选（`LIKE '2026%'`）仍会计入，但逐月统计 `substr(...,6,2)` 会把 `2月30日` 当 2 月；督办彻底失明；导出给上级的台账含不存在的日期。
- **复现**：`p1` §2.2、§3.2。
- **最小修复**：`receive.validate` 的五个日期字段改为"正则 + `date()` 构造"双检（与 registry 逐行对齐）；并对**历史库**里已存在的非法日期，在 `receive.days_left` 失败时返回哨兵并在 `reminder` 里以"日期异常，请核对"单列，而不是静默丢弃。

#### P1-3 分类树「全部文档」计数漏掉未分类文档

- **现象**：库内 6 篇（3 篇未分类）时「全部文档」显示 **3**；3000 篇（其中 1000 篇未分类）时显示 **2000**。
- **证据**：`p5` / `p6` 实测（§2.4）。根因两处叠加：
  - `db/dao.py:533-544` `count_documents_by_category()` 的 SQL 带 `AND category_id > 0`，**未分类文档被排除在返回字典外**；
  - `ui/library_panel.py:175` 根节点直接用 `root.setText(1, str(sum(own.values())))`，而不是 `dao.count_documents()`。
- **影响面**：用户据分类树判断库存量；差值是"未分类"部分，越大越离谱（导入后未归类是默认状态）。同时会让用户误以为材料丢失。
- **复现**：`p5_counts_blank_snapshot.py`；或 `p6` 看 `「全部文档」计数显示: 2000  实际在库: 3000`。
- **最小修复**：根节点文案改为 `dao.count_documents()`（1 行）；`count_documents_by_category` 保留 `>0`（子分类语义正确）即可。

#### P1-4 全文检索命中数被硬截断 100，且以"处"为单位汇报

- **现象**：300 篇全部命中时，列表只显示 100 条，底部写 **「检索到 100 处」** —— 既不说明被截断，单位也不对（检索结果是**文档级**，一篇一条）。
- **证据**：`ui/library_panel.py:301` `dao.search_documents(kw, limit=100)`、`:309` `f"检索到 {len(results)} 处"`；`p6` 实测：`列表项=100，计数文案='检索到 100 处'`；`p4` 实测 300 篇全命中时 UI 只能看到 100 篇。每篇只返回 1 条（按文档计）已由 `p4` 验证。
- **影响面**：用户以为库里只有 100 篇相关材料（实际 300/3000），据此判断"没有更多材料"，是**检索可信度**问题；`snippet` 已有，改成篇数不增加成本。
- **最小修复**：dao 增加 `count_search_documents(query)` 或在 `search_documents` 里多取 1 条判断 `has_more`；文案改为「共 N 篇，显示前 100 篇」；单位"篇"。

#### P1-5 「历史版本」在未打开文档时列出全库所有文档的快照，可跨文档回滚

- **现象**：编辑器未打开任何材料时 `LibraryPanel`/主窗口仍可打开「历史版本」：`main_window.py:1190-1193` 直接传 `self.editor.doc_id`（初值 `None`，`editor_panel.py:61`）；`SnapshotsDialog._fill`（`feature_dialogs.py:1015-1021`）调 `dao.list_snapshots(None)`，而 `dao.list_snapshots`（`dao.py:1075-1085`）在 `doc_id is None` 时**不加 WHERE**，返回全库快照（实测返回 65 条：30 条某文档 + 35 条全局 + 其它文档）。
- **影响面**：用户会把 A 文档的历史内容当成"当前文档的历史版本"回滚进编辑器（带未保存标记，不回写库，但会造成串档与误判；若随后另存，错误内容会落成新文档）。涉密材料的快照也会在无关文档的界面里被列出。
- **最小修复**：`SnapshotsDialog` 在 `doc_id in (None, 0)` 时直接禁用/空态（"请先打开一篇材料"）；`dao.list_snapshots(None)` 语义改为只返回 `doc_id IS NULL` 的全局快照（或调用方必须显式传 `include_global=True`）。

#### P1-6 发文字号无唯一性约束、无查重提示；取号存在重号窗口

- **现象**：① `dispatch_register` 只有 3 个**普通**索引（`PRAGMA index_list` 实测 unique 全为 0），同一 `doc_no` 可插入任意多条；② `registry.validate` 不查重，手工台账里手填一个已存在的字号 → 保存成功（实测同号 2 行）；③ `next_doc_no` 是"现算 max+1"，两次取号在保存前都给 `〔2026〕1号`，4 线程并发各自取号再写入 → **4 行同号**；④ `dao.max_doc_no_serial` 的 `LIKE '{prefix}%〔{year}〕%'` 会把 `×政办发函〔2026〕50号` 的 50 算进 `×政办发` 的序列（下一次给 51，跳号），代字含 `%`/`_` 时 LIKE 通配符会取到全库最大值（实测 `next_doc_no('%',2026) = %〔2026〕100号`）。
- **对照**：**汇编回填**路径做得好 —— `ui/compile_wizard.py:886-902` 先按 `final.doc_no` 查重（占用则跳过并提示），再按 `doc_id` 幂等更新（"已有该产物的记录，已更新"）。手工台账表单缺这一层。
- **影响面**：公文重号是业务硬错（收文单位据此归档/引用）。单机单人下"两次取号不保存"或"手填重复号"可达；库层无兜底，任何导入/脚本路径都能写入同号。
- **最小修复**：① `registry.validate` 增加同号查重（新记录 vs 既有记录，排除自身 id），命中给"该发文字号已登记（标题…），请改用 X 号"；② `suggest_batch` 类问题见 P1-1；③ **不建议**直接加唯一索引（历史库可能已有重复，迁移会失败），应用层查重 + 归档时清理脚本更稳；④ `max_doc_no_serial` 的 LIKE 追加 `ESCAPE` 处理并改为精确代字匹配（`doc_no LIKE ? ESCAPE '\'` + 正则二次校验）。

### 3.3 P2（性能 / 体验 / 一致性 / 能力缺口）

#### P2-1 资料库面板刷新 271 ms，热点是分类树而不是检索

- **证据**（p6，3000 篇 / 100 分类）：`LibraryPanel()` 构造 **906 ms**；`reload()` **271 ms**（`_fill_categories` **229 ms** + `_reload_docs` **118 ms**）；UI 检索 100 条 **16.1 ms**。
- **影响面**：每次导入/删除/改分类/从回收站返回都会同步刷新面板 → 3000 篇规模下每次 ~0.27 s 的 UI 卡顿；检索本身达标（无需优化）。`_fill_categories` 每次全量重建树并做"自下而上递归累加"（`library_panel.py:171-182`），分类多时递归 + Qt 节点创建是主因。
- **修复方向**：分类树只在分类集合变化时重建（缓存 `list_categories` 结果的签名），计数变化只更新文本；递归累加改一次遍历（子→父），并考虑 `QTreeWidget.setUpdatesEnabled(False)` 包裹。验收：3000 篇/100 分类 `reload() < 100 ms`。

#### P2-2 查重 O(n²) + 逐篇 `get_document` + 结果无上限

- **证据**：`feature_dialogs.py:1099-1129`：`work()` 先 `dao.list_documents()`，再 **对每篇 `dao.get_document(did)`**（N+1 查询）取正文，然后 `simhash.find_similar(texts, ...)` 两两比较（`simhash.py:99-115`）。实测：300 篇全同 **2.49 s / 44,850 对**；1000 篇全同 **24.86 s / 499,500 对**；按 O(n²) 外推 3000 篇 ≈ **224 s**，最坏可产生 **≈450 万对**，而 `_on_done` 把每一对都塞进 `QListWidget`（无上限、无进度回调）。
- **影响面**：规模一大，"查重"会像卡死（虽有后台线程，但无进度提示、完成后 UI 可能因 45 万条列表项再次冻结）；模板化的重复材料（同一通知多份）正是最坏输入。
- **修复方向**：① 用 `dao.iter_documents_content()`（一次性取 id+正文）替代 N+1；② 结果上限（如 200 对）+ 进度信号；③ 可选：SimHash 分桶（按 64 位高 16 位分桶，只比同桶/邻桶）把 O(n²) 降到近似线性。验收：1000 篇 ≤5 s，结果列表 ≤200 对且有"已截断"提示。

#### P2-3 段落索引把正文存了 4 份，存储超验收线（+87.5% / 占库 25%）

- **证据**：§2.3 dbstat 表；`bench_scale.py:175-178` 输出 `88.9%（验收线 < 40%）`；`VACUUM` 校正后 3000 篇 5.13 MB → 9.62 MB（**+87.5%**）；1000 篇因页复用显示 0.0%（口径不稳）。
- **影响面**：每万篇材料约多 **6.5 MB**；备份/移交包体积同步膨胀；`paragraphs_fts` 是 standalone FTS（`schema.py:346-353`），与 `paragraphs.text` 内容完全重复。
- **修复方向**：`paragraphs_fts` 改为 **external content**（`content='paragraphs', content_rowid='id'`）可去掉 `paragraphs_fts_content`（实测 1.957 MB，约 −20% 库）；进一步可让 `paragraphs` 只存 `char_offset + length`（`char_offset` 已有），文本从 `documents.content_text` 切片，再省 1.957 MB（总增幅降到 ~+11%）。**注意**：目前 `replace_paragraphs` 手工维护 FTS（`dao.py:1646+`），改 external content 需同步改写入与重建两处。验收：`dbstat` 中段落相关页 / 总页 **< 40%**，`search_paragraphs_fts` 命中与 snippet 不变。

#### P2-4 督办角标跨天不刷新，且没有手动刷新入口

- **证据**：`reminder.py` 模块头明确"只在启动时算一次、用户手动刷新时算一次"；实际调用点只有 `main_window.py:209`（启动）、`:804`（从收文台账返回后）、`:828`（点角标且清单为空时）。没有定时器、没有"刷新督办"菜单/按钮；程序跨夜运行后，`pending()` 里的 `date.today()` 不再被重新求值（角标与清单保持昨天的判断）。
- **影响面**：办公室电脑常开是常态；跨天后"2 日内到期"的判定会滞后整整一天，逾期件也不会自己冒出来。**不是"要不要轮询"的问题**，而是"零成本的事件点缺失"。
- **修复方向**：在 `MainWindow.showEvent/activateWindow`（窗口获得焦点）与 `QApplication.applicationStateChanged` 里比较 `date.today()` 与上次计算日期，不同才重算（无定时器、无轮询，仍守续航红线）。验收：改系统日期/跨天后首次切回窗口，角标更新。

#### P2-5 全局快照（`doc_id IS NULL`）不轮转，且与"每文档 30 版"承诺不一致

- **证据**：`dao.py:1066-1073` `_prune_snapshots` 用 `DELETE ... WHERE doc_id=? AND id NOT IN (...)`；当 `doc_id is None` 时 SQL 参数为 NULL，`doc_id = NULL` 永不成立 → 不裁剪。实测：单文档 35 次 → 保留 30（最早 id=6）；全局 35 次 → **保留 35**。同时 `add_snapshot` 在项目内没有任何 `doc_id=None` 的调用点（`editor_panel.py:444/489`、`feature_dialogs.py:409` 均传真实 id），说明全局快照只可能来自历史数据/手工调用。
- **影响面**：若历史库存在全局快照，会无上限增长且只出现在 P1-5 的串档列表里。
- **修复方向**：`_prune_snapshots` 用 NULL-safe 写法（`(doc_id IS ?)` 或显式分支），并让 `add_snapshot` 对 `None` 与 `0` 一律拒绝（当前 0 会当作"id=0 的文档"处理，同样是哑弹）。

#### P2-6 归档函数层缺守卫：在办件、非法保管期限都能被直接写进去

- **证据**：`archive.batch_archive`（`archive.py:68-90`）不检查 `receive.is_closed`、不校验 `retention` 白名单（实测 `"5年"` 写入成功，`receive.validate` 事后才报"保管期限取值异常"）；UI 侧 `receive_dialog.py:464-496` 有守卫（在办件拦截 + 下拉取值），所以**当前唯一调用方是安全的**，但函数是公开 API，任何新入口（批量脚本、未来的"归档到某年度"）都会绕过。
- **修复方向**：`batch_archive` 内加 `if not receive.is_closed(r): raise ValueError(...)`、`if retention not in RETENTION_CHOICES: raise ValueError(...)`；成本 ~8 行，零风险（UI 传值本就合规）。验收：直接调用传在办件/非法期限 → 抛错且不改库。

#### P2-7 收文办理状态无流转守卫

- **证据**：`receive.py:29` 只有状态元组；`receive_dialog.py:138-146` 状态是自由下拉；`update_receive` 直接落库。实测：`签收 → 已归档` 一次写入成功（`validate → []`）；`已办结` 但 `done_date` 为空也允许（`is_closed()` 返回 True，督办从此不再提醒，而台账上没有办结日期）。
- **影响面**：台账状态与"办结日期/档号/保管期限"可能互相矛盾；`is_closed` 以状态或办结日期任一为真，矛盾数据会让督办口径漂移。
- **修复方向**：加 `STATE_FLOW = {"签收": {...}, ...}` 的**提示级**校验（越级保存时 warn 但允许，避免卡住历史数据）；`已办结` 必须填 `done_date`（升为阻断）。验收：越级保存有明确提示；已办结缺日期被拒。

#### P2-8 来文字号解析覆盖 10/14，4 种常见写法退回自由文本

- **证据**（p1 §2.1）：`〔2026〕12号`（无机关代字）、`×政发〔2026〕12号文`（尾缀"文"）、`×政发〔2026〕12-1号`（子号）、`×政发〔2026〕12号（急件）`（后缀）→ 均返回 `("", "", 0)`。按 `receive.parse_incoming_no` 的设计（"拆不出就按自由文本保存"）这不算错误，但**收文登记号取号**依赖解析出的代字（`receive_dialog.py:236` 用 `prefix`），拆不出时回退默认"收"，可能与用户预期不符。
- **修复方向**：正则尾部放宽 `(?:号)?(?:文|（[^）]*）)?\s*$`，并支持 `-子号`（`serial` 取主号、`-1` 存 remark/原样）；对无代字的 `〔2026〕12号`，允许 prefix 为空并把整串作为原样。验收：14 种写法全部能拆出年份+序号。

#### P2-9 台账关键字是 LIKE 通配（`%`/`_` 未转义）

- **证据**：`dao.list_dispatch:1318-1324`、`list_receive:1520-1527` 都是 `LIKE '%kw%'` 直接拼接参数（SQL 注入已由参数化挡住 ✓，但通配符未转义）。实测 `keyword="%"` 返回**全库 10/10 行**，`keyword="_"` 同样全库。
- **影响面**：用户检索含 `%`、`_` 的字面词（如"50%"、"T_"）会得到全库结果；不算安全漏洞，但检索结果不可信。同样的写法也出现在 `list_documents` 的标题搜索（如启用）。
- **修复方向**：`LIKE ? ESCAPE '\'` + 把 `%`/`_`/`\` 转义；`keyword` 长度 <1 时直接忽略。验收：`keyword='%'` 返回 0 行。

#### P2-10 文档对比在"行数不等"的替换块按序号硬配对，展示会错位

- **证据**（p2 §F）：`differ.py:68-74` 对 `replace` 块取 `n=max(旧行数, 新行数)`，逐行按序配对；实测 2 行 → 5 行得到 `修改 4 行`、`相似度 28.6%`，其中"乙→丙、空→丁"属机械配对，用户会误读为"乙被改成丙、后面又新增两行"。
- **影响面**：只影响展示（不改正文），但对比视图的核心价值就是"看清哪里变了"。
- **修复方向**：最小改法是当 `i2-i1 != j2-j1` 时在统计行提示"行数不同，按序对齐"；进阶可用 `difflib.SequenceMatcher` 在行级先按相似度配对替换块。

---

## 4 优化建议（按投入产出排序）

| # | 目标 | 改法落点 | 成本 | 风险 | 验收口径 | 红线影响 |
|---|---|---|---|---|---|---|
| 1 | 档号唯一（P1-1） | `core/archive.suggest_batch` + 新 `dao.max_archive_seq` | S（~30 行） | 低：只影响新编号；已有档号不动 | 连续两批归档档号无交集；跨年各按自身年度 | 无 |
| 2 | 日期合法性（P1-2） | `core/receive.validate` 五字段改"正则+`date()`"；`reminder` 对异常日期单列提示 | S（~15 行） | 低：历史非法数据需一次性核对脚本 | 三个非法日期被拒；非法日期件在督办里以"日期异常"出现而非消失 | 无 |
| 3 | 分类计数（P1-3） | `ui/library_panel.py:175` 根节点改 `dao.count_documents()` | XS（1 行） | 无 | 3000 篇显示 3000 | 无 |
| 4 | 检索不误导（P1-4） | `dao.search_documents` 增加 total/has_more；`library_panel._on_search` 文案 | S（~20 行） | 低：多一次 `count(*)`（FTS 实测 0.2 ms） | 300 篇命中显示"共 300 篇，显示前 100 篇" | 无 |
| 5 | 历史版本不串档（P1-5） | `SnapshotsDialog.__init__` 判空 + `dao.list_snapshots(None)` 语义收紧 | XS（~10 行） | 低 | 未打开文档时入口禁用/空态；对话框只列当前文档快照 | 无（只读展示） |
| 6 | 字号查重（P1-6） | `registry.validate` 加同号查重；`max_doc_no_serial` 加 ESCAPE/精确代字 | S（~25 行） | 中：历史同号数据保存会被拒 → 需"仅提示"开关或仅对新记录生效 | 手填已存在字号 → 拒绝并给出占用标题；`%`/`_` 代字不再通配 | 无（不改库结构） |
| 7 | 面板刷新 <100 ms（P2-1） | `_fill_categories` 缓存+单遍累加+`setUpdatesEnabled(False)` | M（~40 行） | 中：树交互回归 | 3000 篇/100 分类 `reload()` <100 ms | 无 |
| 8 | 查重不假死（P2-2） | `SimilarityDialog.work` 换 `iter_documents_content`、结果上限、进度信号、可选分桶 | M（~60 行） | 中：查全率与阈值交互需回归 | 1000 篇 ≤5 s；列表 ≤200 对并提示截断 | 无 |
| 9 | 段落索引瘦身（P2-3） | `paragraphs_fts` 改 external content（`content='paragraphs'`） | M（~50 行，含写入/重建同步） | 中-高：FTS 同步一致性、旧库需重建索引（已有 `rebuild_paragraphs` 可复用） | dbstat 段落占比 <40%；检索/片段不变 | 无（纯索引层，可整表重建的既定前提） |
| 10 | 督办跨天刷新（P2-4） | `MainWindow.showEvent` 比较日期后调 `refresh_reminders` | XS（~10 行） | 无 | 跨天后切回窗口角标更新 | 无（仍不轮询） |
| 11 | 全局快照轮转（P2-5） | `_prune_snapshots` NULL-safe + `add_snapshot` 拒绝 None/0 | XS（~5 行） | 无 | 35 次全局快照保留 ≤30；None 直接拒绝 | 无 |
| 12 | 归档函数守卫（P2-6） | `batch_archive` 加 `is_closed`/retention 校验 | XS（~8 行） | 无 | 直接传在办件/非法期限抛 ValueError | 无 |
| 13 | 状态流转提示（P2-7） | `receive.validate` + `ReceiveForm` 越级提示；已办结必须有日期 | S（~20 行） | 低：历史数据只提示不阻断 | 越级保存有提示；已办结缺日期被拒 | 无 |
| 14 | 来文字号宽松解析（P2-8） | `receive._INCOMING_RE` 尾部放宽 | S（~10 行） | 低：可能多拆出一些"半结构化"串，保留回退 | 14 种写法全可解析 | 无 |
| 15 | LIKE 转义（P2-9） | `list_dispatch/list_receive` 加 `ESCAPE` | XS（~6 行） | 无 | `keyword='%'` → 0 行 | 无 |
| 16 | 对比行数不等提示（P2-10） | `differ._stats`/`diff_to_html` 加提示 | XS（~5 行） | 无 | 2→5 行对比统计行注明"按序对齐" | 无 |

红线核对：以上 16 条**均不新增运行时依赖、不联网、不改变主包体积**（xlsx/DOCX 已由仓库自带实现），不触碰"不静默改正文"（全部是显式用户动作后的提示/写库），Py3.9 + ARM64 无平台特定 API（仅 `PRAGMA dbstat` 属**审计侧**，若用到产品里需兜底，建议不改产品代码使用）。

---

## 5 不建议做（含理由）

1. **资料库列表分页 / 虚拟列表**：实测 DAO `list_documents` 3000 篇 **17.4 ms**、UI 列表构建合计 **118 ms**；先修分类树（P2-1）即可把整刷降到 <100 ms。上分页要改列表数据流（选中/多选/批量），风险远大于收益。
2. **为 FTS 开放高级检索语法（NEAR/布尔/字段限定）**：28 类特殊字符实测已全部安全兜住；目标用户是办公室人员，语法越开放，"搜不到"的困惑越多。保持"分词 + AND，多词放宽 OR"即可。
3. **内容寻址 / 去重存储正文**：段落索引的重复副本是主要开销（P2-3），用 external content 即可；把 `documents.content_text` 也做内容寻址（同一通知多份共享）收益 ≤6.5 MB/万篇，但要重写全部读写路径与外键，破坏"备份=单文件自洽"的既有前提。
4. **给台账加后台常驻定时器 / 轮询督办**：公文以"天"为粒度，轮询收益为零；在窗口获得焦点时比较日期即可（P2-4）。
5. **在 `dispatch_register.doc_no` 上直接建唯一索引**：历史库很可能已有重复（本项目 `idx_dispatch_no` 一直是普通索引，且外部导入路径不校验）；迁移会失败甚至触发"迁移失败回滚"告警。应用层查重 + 一次性清洗脚本更稳。
6. **把查重阈值改成自适应/机器学习**：无标注数据、不可解释、离线包体积受限；SimHash+Jaccard 的误报/漏报已由 `tests` 覆盖（本报告 §2.5 也验证了阈值行为）。

---

## 6 待验证项（含验证方法）

1. **ARM64 / 麒麟上的 Qt 树与列表性能**：本机 x64 下 `_fill_categories` 229 ms；ARM 上可能 2–4×。验证：在麒麟 V10 ARM64 上用同一 `p6_ui_path.py`（仅临时库）跑一遍，记录 `reload()`/`_fill_categories`。
2. **真实 3000 篇用户库的面板刷新曲线**：本次样本正文 ~120 字/篇、100 分类、附件计数为 0；真实库有附件（`attachment_counts` 批量查询）与更长正文。验证：复制一份真实库到临时目录，用 `p6` 同法测算（不要动原库）。
3. **超大单篇（>2 MB 正文）的段落回填与检索**：本测样本小，`_split_long` 的分块路径未压测。验证：造 1 篇 2 MB 文档 → `rebuild_paragraphs` 耗时、段落检索延迟、`paragraphs` 行数。
4. **全局快照（`doc_id IS NULL`）的真实来源**：代码中未见 `add_snapshot(None)` 调用，可能只存在于历史库/旧版本。验证：`SELECT count(*) FROM snapshots WHERE doc_id IS NULL`（只读）在真实库上查一次。
5. **CI（GitHub windows-latest / Debian ARM64）上的本范围回归**：本报告只在本机实测；CI 上 `--timeout=300` 对"1000 篇查重 24.9 s"仍安全，但若用户库更大需评估。验证：CI 现有测试 `test_*registry*`、`test_receive*`、`test_archive*`、`test_library*` 是否覆盖本报告 6 条 P1（见 §6 附注）。
6. **收文台账导出 xlsx 的文本列覆盖面**：实测 `reg_no/incoming_no` 为 inlineStr（强制文本）；`pages/copies` 为数值。验证：用 Excel/WPS 打开确认长登记号不被转科学计数法（本机无 Excel 自动化，未做真机打开验证）。

> 附注（测试覆盖观察）：现有 `tests/` 对本范围有 `test_registry*`/`test_receive*`/`test_archive*`/`test_library*`/`test_simhash*` 等用例；本报告 6 条 P1 中，P1-1（档号重号）、P1-2（收文日历校验）、P1-3（分类计数）、P1-4（检索截断文案）、P1-5（快照串档）**均无对应断言**——建议把 §3.2 的最小复现句直接补成回归用例（每条 3–8 行），这是零风险的防回归。

---

## 附录：实测通过项清单（明确"不是问题"）

| # | 项目 | 实测证据 |
|---|---|---|
| E1 | 回收站级联完整 | 软删除摘 `documents_fts`、保留段落/快照/附件；恢复补回 FTS；彻底删除清空 `documents/documents_fts/paragraphs/paragraphs_fts/fts_index_state/snapshots/attachments` 且**附件磁盘文件被删**（真实 `attachments.add` API，p3） |
| E2 | 快照 30 版轮转 | 单文档 35 次 → 30 条（最早 id=6），与 `SNAPSHOT_KEEP=30` 一致 |
| E3 | FTS 特殊字符/注入 | 28 个畸形查询串（引号/AND/NEAR/通配/括号/2000 字/emoji）全部返回 0 条且不抛异常；`build_match_query` 对词加引号，`%`/`_` 不影响 FTS |
| E4 | 空白材料查重不构成误报 | `text_hash` 会把纯空白归一 → 两篇空白材料无法同时入库（`add_document` 返回 -1），`find_similar` 的"空 vs 空=1.0"在正常路径**不可达** |
| E5 | 对比安全与稳定 | `<script>` 已转义；CRLF/LF 视为相同；2000 行 0.01 s/246 KB；空→非空正确标 row-ins |
| E6 | 编码路径 | utf8/utf8bom/gbk/utf16/crlf 五种 txt 全部 `parse_any` 成功、首句一致 |
| E7 | 导出安全 | CSV 带 UTF-8 BOM、`=cmd\|'/c calc'!A1` 被中和为 `'=cmd…`、`@SUM` 同理；xlsx 全 inlineStr、发文字号强制文本（样式 s=1） |
| E8 | 移交清单内容 | DOCX 含标题/移交单位/合计 4 件/保管期限分布/签字行/档号；xlsx 7 列与行数正确 |
| E9 | 检索性能达标 | 3000 篇 DAO 检索 10.7 ms、UI 16.1 ms、段落检索 15.4 ms，远低于 1 s |
