# v1.8.0 只读审计 · 01 文档接入 → 汇编 → 排版输出链路

- 审计对象：`<仓库根>` v1.8.0（只读审计，未改任何源码）
- 审计员：audit-pipeline
- 实测环境：Windows 10 x64 / Python 3.13.14（`.venv`）/ PyMuPDF 1.28.2 / python-docx 1.2.0 / PySide6 6.11.2
  **本机装有 WPS Office 12.0（COM `kwps.Application` 可用）**，无 LibreOffice、无 Tesseract。
  因此「COM 通路」与「WPS 渲染核对」为真机实测；「LibreOffice 降级」与「OCR 成功路径」在本机不可触发（见 §6）。
- 数据隔离：所有实测都在 `%TEMP%\gwaudit`（`paths.set_app_data_dir` + `dbconn.configure` + `GWTOOL_EXPORT_DIR`），
  未写 `%APPDATA%\gwtool`，未改仓库任何文件。
- 门禁抽查（未跑全量）：`tests/test_parsers.py` 9 passed（9.95s）；
  `tests/test_compiler_sources.py tests/test_compile_pdf.py` 25 passed（4.42s）。

## 0. 复跑约定

探针脚本放在 `%TEMP%\gwaudit\`（不入库，随 TEMP 清理）。任何片段都可按下述骨架复跑：

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:QT_QPA_PLATFORM='offscreen'
$env:GW_AUDIT_TMP="$env:TEMP\gwaudit"
cd <仓库根>
.\.venv\Scripts\python.exe -c "<片段>"      # 或把片段存成 $env:GW_AUDIT_TMP\p.py 后执行
```

公共前置（下文片段默认已执行）：

```python
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, r"<仓库根>")
BASE = Path(tempfile.gettempdir()) / "gwaudit"; (DATA := BASE/"data").mkdir(parents=True, exist_ok=True)
os.environ["GWTOOL_EXPORT_DIR"] = str(BASE/"exports"); os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from gwtool import paths; from gwtool.db import connection as dbconn
paths.set_app_data_dir(DATA); dbconn.configure(DATA/"audit.db")
```

WPS 真机渲染（本机唯一可用的 docx→PDF 通路，用于核对版式）：

```python
import win32com.client as w
app = w.Dispatch("kwps.Application"); app.Visible = False; app.DisplayAlerts = 0
d = app.Documents.Open(str(docx.resolve()))
# d.Fields.Update()  # 目录域：不加这行 WPS 不会更新（见 P1-3）
d.SaveAs2(str(pdf.resolve()), FileFormat=17)   # 17 = wdFormatPDF
d.Close(False); app.Quit()
```

---

## 1. 功能清单与入口

| 功能 | 入口（file:line） | 实测 |
|---|---|---|
| 扩展名分发 / 单文件解析 | `gwtool/core/importer.py:34` `parse_any` | 通过 |
| 批量解析（不入库） | `gwtool/core/importer.py:122` `batch_import` | 通过 |
| TXT 多编码探测 | `gwtool/core/parsers/txt_parser.py:21` `read_text_smart` | 通过（9 编码全对） |
| DOCX 解析（含表格/标题层级） | `gwtool/core/parsers/docx_parser.py:16` | 通过 |
| DOC 四级降级链 | `gwtool/core/parsers/doc_parser.py:150` `_extract_text` | 通过（L1/L3/L4 实测，性能见 P1-9） |
| WPS 双形态（ZIP/OLE）嗅探 | `gwtool/core/importer.py:42-54` | 通过 |
| RTF 解析 | `gwtool/core/parsers/rtf_parser.py:9` | 通过 |
| PDF 文字层解析 | `gwtool/core/parsers/pdf_parser.py:17` | 部分通过（P1-7 拆段） |
| 扫描件 PDF → OCR 回退 | `gwtool/core/importer.py:57-62` → `gwtool/core/ocr.py:133` | 通过（缺包提示正确；成功路径本机不可测） |
| 图片 → OCR | `gwtool/core/importer.py:63-73` | 通过（缺包提示） |
| MD / HTML 解析 | `gwtool/core/parsers/md_html_parser.py:10,58` | 部分通过（P1-1 编码） |
| 导入对话框（拖拽/批量/进度/取消） | `gwtool/ui/import_dialog.py:58`；`main_window.py:1058` | 通过 |
| 导入后台线程 + 去重入库 | `gwtool/ui/workers.py:93` `ImportWorker` | 通过 |
| 失败清单展示/落盘 | `gwtool/ui/import_dialog.py:244-309` | 通过（覆盖问题见 P2-8） |
| 汇编向导（材料/模板/输出） | `gwtool/ui/compile_wizard.py:50`；`main_window.py:1145` | 通过 |
| 一键汇编 → docx | `gwtool/core/compiler.py:177` → `docxgen.py:123` | 通过（版式见 P1-4/5） |
| 模板参数 / 序列化 | `gwtool/core/template.py:71-188` | 通过（脏 JSON 容错通过） |
| 目录域 + 页码域 | `gwtool/core/docxgen.py:104-111,151-161,198-222` | 部分通过（P1-3/P2-5） |
| 封面 / 红头 / 版记 / 水印 | `docxgen.py:142-150,225-276,322-354`；`watermark.py:85` | 部分通过（P1-4/P1-10） |
| 内置 PDF 渲染（两遍算页码） | `gwtool/core/pdfrender.py:343`；`workers.py:190` | 通过（页码 120/120 正确；行距见 P1-6） |
| PDF 外侧页码盖章 | `gwtool/core/pdfrender.py:380` | 通过 |
| A3 骑马钉小册子 | `gwtool/core/booklet.py:44`；`workers.py:225` | 通过（死参数 P2-11） |
| 批量模式（每份独立成文） | `gwtool/ui/compile_wizard.py:553` → `core/batch.py:45` | 通过（P2-9） |
| 产物命名 / 防覆盖 | `compile_wizard.py:494-527`；`batch.py:27` `safe_filename` | 通过 |
| 产物落库 `documents` | `compile_wizard.py:747-830` | 通过（幂等找回 doc_id） |
| 台账回填 `dispatch_register` | `compile_wizard.py:832-905`；`registry.py:147` `from_compile` | 通过（-1 重复、按 doc_id 幂等更新） |
| 移交包导出 | `main_window.py:853` → `exporter.py:132` `build` | 通过（P1-11 同名附件） |
| 移交包回读校验 | `main_window.py:916` → `exporter.py:275` `verify_package` | 通过（篡改可检出） |
| 附件随包 / 体积上限 / 缺失清单 | `exporter.py:90-113`；`attachments.py:131` | 通过 |
| 归档批量编号 + 移交清单 | `receive_dialog.py:475-509` → `archive.py:68,131,141` | 通过 |
| XLSX 写（零依赖） | `core/xlsx.py:214` `write_table` | 通过 |
| XLSX 读（零依赖） | `core/xlsx_read.py:232` `read_sheet` | 通过 |
| 智能分类建议 | `core/classify.py:45`；`library_panel.py:414` | 通过 |
| 一键排版微调 | `core/formatter.py:108`；`editor_panel.py:597` | 部分通过（P1-8/P2-7） |
| 文秘工具箱（金额/日期/全半角/简繁） | `core/toolbox.py:20-256`；`editor_panel.py:578` | 通过（负号/超长金额行为见 §2） |
| 字体探测 | `core/fontcheck.py:24,49`；`compile_wizard.py:945` | 通过（无 QApplication 时静默，见 P2-12） |

---

## 2. 实测记录

### 2.1 解析与导入

**A. TXT 编码探测**（样张 10 个，`importer.parse_any`）

| 样张 | 结果 | 数字 |
|---|---|---|
| UTF-8 无 BOM / UTF-8 BOM / GBK / GB18030(含「䶮」「𠮷」) | 全对 | 71/71/71/28 字符，blocks 5/5/5/2 |
| UTF-16LE 带 BOM / UTF-16LE 无 BOM / UTF-16BE 带 BOM | 全对 | 71 字符，blocks 5 |
| Big5（繁体） | 对 | 「標題：繁體公文測試…」19 字符 |
| 空文件 / 只有空白 | 拒绝且文案正确 | `ok=False`「未提取到文字」 |

`read_text_smart` 对 UTF-16 无 BOM 也正确（靠 chardet）——结论：**TXT 编码探测这一环是可靠的**。

**B. MD / HTML 编码（缺陷 P1-1）**

```
doc.md  内容以 GBK 存储 -> parse_any 得到：'���ڿ�չ��\u0e64����֪ͨ'（整篇乱码，ok=True）
doc.html 内容以 GBK 存储 + 自带 <meta charset="gbk"> -> '����'（同样乱码）
```
源码：`md_html_parser.py:11` / `:59` 均写死 `encoding="utf-8", errors="replace"`，与 `txt_parser.read_text_smart` 的探测能力不对称。

**C. DOCX**（python-docx 造样张）

| 样张 | 结果 |
|---|---|
| 正常（含 Heading1/List Paragraph/表格/`core.title`） | ok，4 blocks，21.3ms，表格按文档顺序保留 |
| 截断 50% / 全零 2KB / ZIP 完好但缺 `word/document.xml` / OLE 加密壳 | 全部 `ok=False`，异常经 `errmsg.friendly_parse_error` 译为「文件可能已损坏…请另存为 .docx」 |
| 文件不存在 | `PackageNotFoundError` → **误报为「文件可能已损坏」**（应为「找不到文件」，P2-2） |

**D. DOC 四级降级链**（真机 WPS 生成 99KB `real.doc`）

| 级别 | 单篇耗时 | 文本覆盖 |
|---|---|---|
| L1 COM（kwps.Application） | **1032.2 ms** | 7/7 行（+1 重复标题） |
| L2 LibreOffice | 本机无 soffice，跳过 | — |
| L3 纯 Python olefile 分片表 | **0.7 ms** | 7/7 行 |
| L4 raw_scan（FIB 魔数被破坏后） | 1.0 ms | **3/7 行**（丢「一、总体要求」「（二）严格审核」） |

```
5 份 .doc 批量：整链（COM 优先）8.53s，仅 L3 0.003s —— 2405x
外推 50 份：COM ≈ 85s，L3 ≈ 0.04s（3000 段 .doc：整链 5.48s vs L3 0.02s）
```
FIB 破坏后 `_looks_like_word_doc=False` → 跳过 COM → L3 抛错 → L4 出 3/7 行（`ok=True`，**静默降级为部分内容**，无「内容可能不完整」提示）。

**E. WPS 双形态嗅探**：OLE 形态 `.wps` → 7 blocks；ZIP 形态 `.wps` → 7 blocks；纯文本改名 `.doc` → 「未提取到文字」；全零 `.doc` → 「未提取到文字」。

**F. PDF**

| 样张 | 结果 |
|---|---|
| 文字版单页 | ok，63.4ms |
| 无文字层（纯图片） | `ok=False`「未提取到文字（扫描件需安装 Tesseract OCR 后重试）」 |
| AES-256 加密 | `ok=False`，异常 `ValueError: document closed or encrypted` → UI 显示「内部错误」（P2-3） |
| 截断 400 字节 | `FileDataError` → 同上「内部错误」 |
| 公文样张（标题 22pt + 正文 16pt，手工按 27 字折行） | **28 个 block**（样张只有 12 个自然段）；「一、总体要求」被判为 paragraph |

**G. 大文件**：2.77MB TXT（80001 行）138.4ms；12000 段 DOCX（0.07MB 压缩后）**6778.1ms**（0.56ms/段）。

**H. 批量 50 文件（核心指标）**

```
58 个文件（50 份 40 段 docx + 5 份截断坏件 + 2 份内容重复 + 1 份空 txt）
总耗时 4.40s，成功 50，跳过 8，平均 76 ms/文件（含入库）
失败清单逐条可读：
  bad0~4.docx → 「文件可能已损坏，或不是有效的 Word 文档。」
  dup0/1.docx → 「内容与已有材料重复，未重复入库」（dao.add_document 返回 -1）
  empty.txt   → 「未提取到文字」
```
→「50 文件」这一档（docx 主流形态）**性能与去重都达标**；风险在 `.doc` 形态（见 P1-9）。

**I. 导入对话框 UI（离屏 Qt 实测）**

- 拖入目录：`_drop_urls` 展开 5/5（含大写 `B.DOCX`、`d.PDF`，排除 `e.exe`，递归到子目录）。
- 12 条失败：面板显示前 8 条 + 「…另有 4 份」，完整清单落盘 `<导出目录>\导入失败清单.txt`（13 行 = 表头 + 12 条）；重复项不计入失败清单 ✓。
- 二次导入会**覆盖**上一份失败清单（无时间戳，P2-8）。

### 2.2 汇编与排版

**J. docx 版式事实（XML 层，正确）**

```
页面 210.0 x 297.0 mm
pgMar: top=2098(right=1474,bottom=1984,left=1587) twips = 37.0/26.0/35.0/28.0 mm  ← 精确命中模板
footer 部件 2 个：footer1 jc=right(奇数页) / footer2 jc=left(偶数页)，均含 PAGE 域
settings: <w:updateFields/> = True，<w:evenAndOddHeaders/> = True
样式：Heading1 x3 / Heading2 x9；eastAsia 字体 {仿宋_GB2312, 宋体, 楷体_GB2312, 黑体, 方正小标宋简体}
段落：w:spacing w:lineRule="exact" w:line="560"（=28pt）、w:ind firstLineChars=200 均按模板写入
```

**K. docx 渲染事实（WPS 转 PDF 后逐页量测，缺陷 P1-5/P1-4）**

```
默认模板（3 份材料/12 标题），WPS 转 PDF 1.5s：
  第2页 行数=19 行距=32.4pt 版心宽=442.3pt(=156.0mm) 最长行=26 字
  第3页 行数=19 行距=32.5pt
  → 模板要求 28pt 固定行距 / 22 行；实测 32.4pt / 19~20 行
A/B 定位（同一份 90 段正文，改 XML 后重渲染）：
  基线（docxgen 原样）              行距 32.4pt，19 行/页
  段落加 w:before="0" w:after="0"  行距 28.0pt，21 行/页   ← 根因确认
  仅清空 docDefaults 的 spacing     行距 28.0pt，21 行/页
  根因：python-docx 默认模板 docDefaults 带 w:spacing w:after="200" w:line="276"
        （w:after=10pt 段后距被 WPS 叠加到行距上），docxgen 只覆盖 line 未写 before/after。
行距参数对照：模板 20/28/40pt -> 实测 24.6/32.4/44.2pt（恒定 +4.4pt）
```

**L. docx 目录域（缺陷 P1-3）**

```
WPS 打开后立即另存 PDF（不调 Fields.Update）：第1页 4 行 = ['— 1 —','目 录','（打开文档后按提示更新目录页码）']
   → 目录内容为空，只有占位提示
调 d.Fields.Update() 后再另存：第1页 15 行，12 条目录 + 真实页码，与正文页一致
   → docx 目录页码本身正确，但**依赖用户手动更新域**
```

**M. docx 要素顺序（缺陷 P1-4）**：全套要素样张 11 页，逐页文本为

```
p1 封面（公文汇编（2026年度）/××单位办公室/二〇二六年八月）  ← 有「— 1 —」
p2 只有红头 2 行（××单位文件 / ×政发〔2026〕1号）            ← 整页除红头全空
p3 目录
p4 起 正文
```
`docxgen.py:148-155` 先 `_build_red_header` 再 `add_page_break` 再目录；而 `pdfrender.py:191-203` 是「目录 → 红头 + 正文同页」。两条通路顺序不一致，且 docx 侧红头孤立成页。

**N. 水印（缺陷 P1-10，A/B 对照）**

```
settings 中 evenAndOddHeaders=True，但 sectPr 只有 <w:headerReference w:type="default">，
header 部件 1 个（word/header1.xml），无 even/first 页眉。
WPS 渲染后按页统计矢量路径（VML 文本水印渲染为矢量，不产生可抽取文字）：
  不加水印：8 页，每页 0 条
  加水印  ：8 页，奇数页 5 条（=「征求意见稿」5 个字形），偶数页 0 条
```
→ **水印只出现在奇数页**。PDF 渲染通路（`stamp_watermark_pdf`）每页都有（文本可抽取，每页 5 处）——两条通路又不一致。

**O. 内置 PDF 渲染器（页码正确 / 行距失控）**

| 场景 | 结论 |
|---|---|
| 3 份材料 15 标题（无封面） | 目录页码 15/15 一致 |
| 同上 + 封面 | 15/15 一致（正文从第 3 页起，前置页计算正确） |
| 同上 + 红头 | 15/15 一致，红头与正文**同页**（p2 首行 = ××单位文件，其后紧跟正文）✓ |
| 30 份材料 120 标题（目录 6 页） | **120/120 一致**，`_toc_chunks` 切片 [20,22,22,22,22,12] 与实际 6 页吻合 |
| 页码位置 | 奇数页 x≈446（右）、偶数页 x≈79（左）= 外侧 ✓ |
| 行距（缺陷 P1-6） | 模板 line_spacing 20/28/40pt 三档**输出完全相同**：实测 29~31 行/页、行距 20.6~21.7pt |
| 每行字数 | 27 字（442.3pt/16pt）✓ 与 docx 一致 |
| 水印 | 每页 5 处 ✓ |

`pdfrender.py:124-126` 用 CSS `line-height:{tpl.line_spacing_pt}pt`，Qt 富文本不吃该声明（20/28/40 输出逐页行数相同即为证据）。

**P. 大表格跨页**：60 行表格 → docx 无 `<w:tblHeader/>`（跨页不重复表头，WPS 渲染第 3/4/5 页首行均为数据行）；内置 PDF 里表格行被页面切断（第 4 页首行是半行单元格文字）。

**Q. 小册子**（8 页源，每页打 `PAGE{n}`）

```
booklet_order: 8 页 -> [[8,1],[2,7],[6,3],[4,5]]（4 张 A3）✓ 与 docstring 一致
实际产物：4 页，尺寸 1190x842pt = A3 横向 ✓
逐页：p1[PAGE8|PAGE1] p2[PAGE2|PAGE7] p3[PAGE6|PAGE3] p4[PAGE4|PAGE5] ✓ 左右位正确
5 页源 -> 4 张 A3（8 个位，3 个空位）✓ 补齐 4 的倍数
损坏源 -> FileDataError（上层 errmsg 未覆盖，见 P2-3）
```

**R. 批量模式**：4 个输入（3 有效 + 1 不存在的 id）→ 成功 3 / 失败 1（`('文档99999','文档不存在')`），progress 回调 (1..4/4) 齐全；重名材料 → `SameName.docx` + `SameName_1.docx`；每份独立成文且含封面/目录。产物落库 → `add_document` 首次返回 54、二次 -1；`add_dispatch` → id=1，`find_dispatch_by_document` 回填一致（幂等）。

**S. 控制字符致汇编失败（缺陷 P1-2）**

```
样张 ctrl.txt 含 \x01\x02（GBK/UTF-8 解码都保留控制符）
parse_any -> ok=True，blocks 含控制字符
docxgen.generate_docx -> ValueError: All strings must be XML compatible:
                          Unicode or ASCII, no NULL bytes or control characters
好材料 + 坏材料同批 -> 同样整批失败（renderer 不支持"跳过坏段落"）
```

**T. 首行缩进叠加（缺陷 P1-8）**：正文已带 2 个全角空格（一键排版 `ensure_indent_placeholder` 的产物）+ 模板 firstLineChars=200 → WPS 渲染实测

```
普通段落        x0=111.4pt  （= 79.3 版心左边界 + 32pt = 2 字）✓
已带缩进段落    x0=143.4pt  （= +64pt = 4 字）✗ 不合 GB/T 9704 的 2 字缩进
```

### 2.3 导出、移交与工具箱

**U. 移交包**：3 文档（原件缺失 → 回落为 `documents/<标题>.txt`）+ 附件上限 2MB → 1 个纳入、4 个排除（3MB 超限 / 文件已丢 / 同名 x2），6ms；`verify_package` → `ok=True`；手工篡改包内 txt → `ok=False`，报「字节数不符」「sha256 不符」✓。

**V. 附件命名安全**：`报:告?.pdf`→`报_告_.pdf`；`..\..\etc\passwd`→`passwd`；`C:\tmp\x.docx`→`x.docx`；`../../../evil.txt`→`evil.txt`；`.hidden`→`hidden`；空→`附件`。重名落盘加 `_2`/`_3`（实测 `附件甲.pdf` → `附件甲_2.pdf`）；doc_id=0、源文件不存在分别抛中文 `ValueError`/`FileNotFoundError` ✓。

**W. 归档**：3 条收文批量归档 → 档号 `XX局2026-0001..0003`、期限「永久」、状态「已归档」，一行一档且不重编；`retention_summary={'永久':3}`；docx 移交清单 39267 B；xlsx 清单 3 行、2583 B 且读回正确。

**X. XLSX 往返**：sheet 名 `q"uote[and]star` → `quoteandstar`（Excel 禁字符已剔）；`<>&"'`、`-3`、`True/False`→`是/否`、空值、换行全部正确；控制字符 `\x00\x01` 被清洗后写入（2204 B）且可读回；假 xlsx（OLE 头）→ `BadZipFile`（未包成 `XlsxError`，P2-3）。

**Y. 分类建议**：安全类词频画像 vs「安全生产隐患排查」→ `[(2,'安全类',0.125)]`；「部门预算经费」→ `[(3,'财务类',0.1129)]`；无关文本 → `[]`；空画像 → `[]` ✓。

**Z. 工具箱**：`amount_to_cn` 10050000.30→人民币壹仟零伍万元叁角整、1005000→壹佰万零伍仟元整、12345678.9→壹仟贰佰叁拾肆万伍仟陆佰柒拾捌元玖角整、0.05→伍分、0→零元整、10^16→壹亿亿元整、`1.999`/`abc`/`1,234.5x` 均抛中文 ValueError、`-5`→伍元整（绝对值，已注释为设计）；`digits_to_cn_date` 两种写法都对；`number_to_upper_cn('2026')`→贰零贰陆；`full_to_half`/`half_to_full` 对位正确；简繁双向正确。

**AA. 一键排版微调**（`run_full_cleanup`）：日志 `['清理多余空格/空行 6 处','全角数字转半角 2 处','去除段首多余空白 1 行','规范化标题编号 1 处','补全首行缩进 3 段']`；
`'全角数字：２０２６年８月３０日'` → `'全角数字：2026年８月30日'` —— 单个全角「８」**未转换**（P2-7）。

---

## 3. 缺陷与风险

> 未发现 P0 级（数据损坏 / 静默改用户正文 / 产物不可用）缺陷。以下按影响面排序。

### P1（明显体验或性能问题，建议本迭代修）

**P1-1 MD/HTML 完全不做编码探测，GBK 文件整篇乱码**
- 现象：`.md` / `.html` 以 GBK/GB18030 存储时，导入得到整篇乱码，且 `ok=True`（用户看到"导入成功"但内容是`����`）。
- 证据：§2.1-B 实测 `'���ڿ�չ��…'`；源码 `gwtool/core/parsers/md_html_parser.py:11`、`:59`。
- 影响面：内网大量 .md/.html 材料来自 GBK 环境（老 OA、Word 另存网页）；乱码入库后会进查重库、纠错库、汇编产物。
- 复现：`printf` 一份 GBK 的 md → `parse_any` → 看 `tree.plain_text()`。同目录下 TXT 走 `read_text_smart` 不受影响，形成"同一次拖拽、两种结果"的不一致。

**P1-2 正文含控制字符时整篇（整批）汇编失败，且报英文异常**
- 现象：材料里有一个 `\x01`，`docxgen.generate_docx` 抛 `ValueError: All strings must be XML compatible…`；批量汇编时**一份坏材料拖垮全部**；`errmsg.friendly` 对 ValueError 原样透传，用户看到英文。
- 证据：§2.2-S 实测（单篇失败 + 好/坏同批失败）；`docxgen.py:285,296`（`run.text = text` 无清洗）；对照 `xlsx.py:39-57` 已有 `_ILLEGAL_XML` 清洗，docx 侧没有。
- 影响面：转换工具导出的 txt、老 .doc raw_scan、用户粘贴的表格数据都可能带控制符。
- 复现：`Path("a.txt").write_bytes("一、要求\n正文\n".encode()+b"\x01\x02x\n")` → `parse_any` → `generate_docx`。

**P1-3 docx 目录域在 WPS 下不自动更新，用户拿到"空目录"**
- 现象：生成的 docx 目录页只有一行占位文字「（打开文档后按提示更新目录页码）」；WPS 打开不更新，须手动"更新域"。
- 证据：§2.2-L 实测（update=False 首页 4 行 / update=True 首页 15 行 12 条目录）；`docxgen.py:104-111`（`w:updateFields=true`）、`:160`（占位串）。
- 影响面：国产化环境主力是 WPS；"目录为空"是汇编类公文的一级验收项（无目录=不能交）。
- 复现：`generate_docx` 后 WPS `Documents.Open` → 直接 `SaveAs2(17)` → 读第 1 页文本。

**P1-4 docx 侧红头独占一页，且与 PDF 侧要素顺序不一致**
- 现象：全套要素产物页序为 封面 → **红头（独占整页）** → 目录 → 正文；PDF 通路为 封面 → 目录 → 红头+正文同页。
- 证据：§2.2-M 实测（p2 仅 2 行）；`docxgen.py:142-155` vs `pdfrender.py:163-207`。
- 影响面：红头（发文机关标志+发文字号+红色分隔线）与正文分离在公文里属版式错误；同一模板导出 docx 与 PDF 版式不一致，用户无法二选一交付。
- 复现：`tpl=default_template(); tpl.cover.enabled=True; generate_docx(...)` → WPS 转 PDF → 看 p2。

**P1-5 docx 行距被 docDefaults 段后距污染：32.4pt / 19~20 行（模板声称 28pt / 22 行）**
- 现象：XML 里 `w:line="560" lineRule="exact"` 正确，但渲染行距 32.4pt，每页只有 19~20 行。
- 证据：§2.2-K 实测 + A/B 根因实验（加 `w:before="0" w:after="0"` 或清 docDefaults → 28.0pt / 21 行/页）；`docxgen.py:51-54`（`_set_line_exact` 只设 line/lineRule）、`:123-140`（`generate_docx` 未处理 docDefaults 的 after 段后距）。
- 影响面：GB/T 9704 / GJB 5100A 的「22 行×28 字」版心对 docx 成品不成立（实测 19~20 行）；排版节奏整体被拉大。
- 复现：见 §2.2-K A/B 脚本；或 `python -c` 生成后 unzip 看 docDefaults。

**P1-6 内置 PDF 渲染器完全忽略行距参数（20/28/40pt 输出相同）**
- 现象：`tpl.line_spacing_pt` 在 PDF 通路上无效，恒为 Qt 默认行高（20.6~21.7pt），每页 30~31 行。
- 证据：§2.2-O 实测三档参数逐页行数/行距完全相同；`pdfrender.py:124-126`（CSS `line-height`）。
- 影响面：内置渲染器是唯一不依赖 Office 的 PDF 通路（麒麟环境无 Word/WPS 时是唯一通路），版心行数比标准多 8~9 行，成品不能用。
- 复现：`render_compiled_pdf([tree], DocTemplate(line_spacing_pt=28), out)` 后逐页统计 y 坐标差。

**P1-7 PDF 解析按「行」拆段，汇编后每行都成一段**
- 现象：一个 90 字自然段（3 行）解析为 3 个 paragraph 块；汇编成品里每行独立成段、每段都首行缩进 2 字。
- 证据：§2.1-F 实测（样张 12 个自然段 → 26 个段落块，块长度 27/26 字交替，全文 28 块）；`pdf_parser.py:42-46`（每 `line` 一个 Block，无合并）。
- 影响面：PDF 是"只能拿到 PDF 的来文"的常见形态；成品版式错乱程度足以导致返工。
- 复现：见 §2.1-F 样张构造（`insert_text` 按 27 字折行）。

**P1-8 一键排版「补全首行缩进」与模板缩进叠加 = 4 字缩进**
- 现象：编辑器执行「一键排版微调」后正文带 2 个全角空格，汇编时模板再给 `firstLineChars=200` → 实测首行 x0=143.4pt（4 字）。
- 证据：§2.2-T 实测（111.4pt vs 143.4pt）；`formatter.py:57-75`（`ensure_indent_placeholder`）；`editor_panel.py:597-604`（结果直接替换正文）；`docxgen.py:289-297`。
- 影响面：用户按提示做过"一键排版"的材料，汇编后缩进全部翻倍；一篇 50 段的材料 50 段都错。
- 复现：`run_full_cleanup("正文段落。")` → 拿 `tree` 生成 docx → 量首行 x0。

**P1-9 .doc 链路 COM 优先：1032ms/篇（小文件）、外推 50 份 ≈ 85s，而纯 Python 0.7ms/篇**
- 现象：`_extract_text` 先起 WPS/Word COM 转 docx（每次拉起一个办公进程），而 `parse_doc` 最终只要纯文本 —— COM 的格式收益在 `_text_to_tree` 里被丢掉。
- 证据：§2.1-D 实测 5 份 8.53s vs 0.003s（2405x），外推 50 份 ≈ 85s；`doc_parser.py:155-180`（顺序）、`:22-24`（`parse_doc` → `_text_to_tree`）。
- 影响面：Windows 用户批量导入 .doc 历史材料（常见 50~200 份）会卡到"以为死机"；且 COM 依赖 Office 进程稳定性（注释自陈曾因 SEH 崩进程）。
- 复现：把同一份 .doc 复制 5 份 → `[parse_any(p) for p in files]` 计时；再 `[_extract_text_olefile(p) for p in files]` 计时。

**P1-10 docx 水印只出现在奇数页**
- 现象：开启水印后，WPS 渲染的偶数页无水印（空白）。
- 证据：§2.2-N A/B 实测（无水印全页 0 条矢量路径；有水印奇数页 5 条、偶数页 0 条）；`docxgen.py:137` 打开 `evenAndOddHeaders`，`watermark.py:96-102` 只往 `sec.header`（default=奇数页）注入。
- 影响面：「征求意见稿」「秘密★1年」这类密级标注缺页属实质性缺陷（涉密件漏标）。
- 复现：§2.2-N 脚本（同一文档加/不加 `tpl.watermark_text`，比对每页 `get_drawings()` 数）。

**P1-11 移交包内同名附件被排除（缺件）**
- 现象：两篇文档各挂一个「附件甲.pdf」（内容不同，落盘已唯一化为 `附件甲.pdf` / `附件甲_2.pdf`），导出时第二份以「包内已有同名附件」进 excluded，**没进包**。
- 证据：§2.3-U 实测（`excluded` 4 条中 2 条为此原因）；`exporter.py:202-210`（arc 名用 `item['name']` 而非落盘唯一名）。
- 影响面：移交/交接场景缺附件=交付不完整；虽然 manifest 有记录，但接收方默认只解压文件。
- 复现：同一文档连挂两次同名附件 → `exporter.build` → 看 `manifest.attachments.excluded`。

### P2（改进项）

| # | 现象 | 证据 / 落点 | 影响 |
|---|---|---|---|
| P2-1 | TXT/DOC/无 core title 的 DOCX，`plain_text()` 里标题重复一次（`[title] + blocks` 且首块就是标题） | 实测 txt `plain_text` 标题出现 2 次；`model.py:32-39`；`txt_parser.py:53-58`、`docx_parser.py:38-39` | 字数统计偏 1 行、FTS 命中片段重复、`documents.content_text` 首行重复（成品不受影响，`effective_blocks` 会去重） |
| P2-2 | 文件不存在时 .docx 报「文件可能已损坏」而非「找不到文件」 | `python-docx` 抛 `PackageNotFoundError`；`errmsg.py:110-112` | 误导排查方向 |
| P2-3 | PDF 加密/截断、booklet 损坏源、xlsx 非 zip 的异常未映射，统一显示「内部错误」；xlsx 相关文案还写「Word 文档」 | `errmsg.py:114`（兜底）、`:31-33`；实测 `ValueError: document closed or encrypted` → 内部错误 | 用户拿不到「去掉口令/另存为 xlsx」这类可执行动作 |
| P2-4 | PDF 标题识别只按字号：与正文同字号的编号标题（「一、总体要求」）永远判为正文；字号众数并列时大标题反而丢 | 实测 §2.1-F；`pdf_parser.py:39-46`（未复用 `_HEADING_RE`） | PDF 材料汇编后没有层级、目录为空 |
| P2-5 | 封面/目录被编页码（封面=第1页）；`stamp_page_numbers(skip_pages=0)` 参数存在但从未使用 | 实测 docx 与 PDF 封面均带「— 1 —」；`pdfrender.py:380`（参数未用）、`:392`（从 1 起全页盖） | 汇编类公文惯例封面不编号；与"正文首页为第 1 页"的口径不同 |
| P2-6 | 大表格跨页不重复表头 | docx 无 `<w:tblHeader/>`（实测 0 处，WPS 渲染第 3~5 页无表头）；内置 PDF 表格行被页面横向切断 | 3 页以上表格可读性差 |
| P2-7 | 全角数字只在连续 ≥2 位时才转换，`２０２６年８月３０日`→`2026年８月30日` | `formatter.py:16-21`（`[０-９]{2,}`） | 日期混合全角/半角 |
| P2-8 | 导入失败清单固定写 `<导出目录>\导入失败清单.txt`，多轮互相覆盖、无时间戳/批次 | `import_dialog.py:298-309`；实测第 2 轮覆盖第 1 轮 | 上一轮谁失败的记录丢失 |
| P2-9 | 批量模式每份产物都带「目录」页（仅 1 条标题），且封面标题与正文一级标题重复 | 实测批量产物 p1 封面 M1 / p2 目录 / p3 又见 M1；`batch.py:73-85`，`compile_wizard.py:558` | 每份多 1 页、观感像"套模板没套好" |
| P2-10 | `docx_to_pdf` 走 COM 时不 `Fields.Update()` → 导出的 PDF 目录页仍是占位文字（实测 p1 只有占位串） | `compiler.py:259-268`；实测 §2.3 备注 | 当前无 UI 调用点（仅 `tests/test_genchain_probe.py:125`），一旦接线即成 P1 |
| P2-11 | `booklet.make_booklet(a3_landscape=True)` 参数与 `A3_LANDSCAPE` 常量均未使用；无装订线/折痕标记；不校验源页是否 A4（任意尺寸都按 2×宽 并排） | `booklet.py:20-22,44` | 死代码 + 非 A4 源会得到怪异页面，靠 `keep_proportion` 缩小居中 |
| P2-12 | `fontcheck.missing_fonts()` 在无 QApplication 时返回 `[]`（静默"字体齐全"） | `fontcheck.py:37-45`；本机实测返回 `[]` | 脚本/服务化调用方拿不到真实缺字体告警（UI 侧正常） |
| P2-13 | `xlsx._cell` 对 float 直接写 `<v>{value}</v>`，`inf`/`nan` 会产出非法 XML；`xlsx_read` 对非 zip 抛 `BadZipFile` 未包 `XlsxError` | `xlsx.py:124-125`；`xlsx_read.py:232-238` | 边界输入产生"Excel 报损坏" |
| P2-14 | 批量/单份产物同名判定只覆盖 `.docx`/`.pdf`，小册子固定名 `{stem}_小册子A3.pdf` 不参与防覆盖 | `compile_wizard.py:522-527`、`:673` | 上一轮小册子可能被覆盖（无提示） |

---

## 4. 优化建议

成本口径：XS ≤ 半小时、S ≤ 2 小时、M ≤ 1 天、L > 1 天（含测试）。

| # | 目标 | 具体改法（落点） | 成本 | 风险 | 验收口径 | 红线 |
|---|---|---|---|---|---|---|
| R1 | 消灭 MD/HTML 乱码（P1-1） | `md_html_parser.py:11,59` 改用 `from .txt_parser import read_text_smart`；能复用 BOM/chardet/回退链，零新依赖 | XS | 极低（TXT 链路已长期验证） | GBK/Big5/UTF-16 的 .md/.html 导入后文本与源一致；`tests/test_parsers.py` 增 3 例 | 不动 |
| R2 | 控制字符不再炸汇编（P1-2） | `docxgen.py` 入口加 `_sanitize_text()`（照搬 `xlsx.py:39-57` 的 `_ILLEGAL_XML` 正则），在 `_add_body/_add_heading/_add_table` 写 run 前统一清洗；**只删非法 XML 字符，不改可见正文** | S | 低：删的是 XML 1.0 根本不允许的字符（Word 也存不下） | 含 `\x01` 的材料可生成；正文可见字符 diff 为 0（用 `plain_text` 比对） | 不静默改正文 → 仅剔除 XML 非法码位，并在日志记剔除数量 |
| R3 | docx 目录不再依赖手动更新域（P1-3） | 方案 A（推荐、跨平台）：`tpl.toc_enabled` 时在**占位段落**里放明确中文指引并在汇编完成提示里加一句"WPS 请右键目录→更新域"；方案 B（Windows 且装了 Office/WPS）：生成后 `docx_to_pdf`/新函数里 `d.Fields.Update()` 只用于导出 PDF，docx 本体仍交给用户 | M | 方案 B 引入 COM 依赖，麒麟不可用 → 只能作为可选增强，不能作默认 | WPS 打开 docx：要么目录已生成，要么界面提示"如何一键更新"；PDF 导出目录 100% 有页码 | 不新增依赖（B 走已有 pywin32 可选分支） |
| R4 | 修正 docx 要素顺序（P1-4） | `docxgen.py:142-161`：红头移到目录之后、正文之前；只有 `not tpl.toc_enabled` 时红头才紧随封面 | S | 低：与 `pdfrender` 既有顺序对齐，测试可对比两侧页序 | 产物页序 = 封面 → 目录 → 红头+正文首页；`tests/test_compile_pdf.py` 增页序断言 | 不动 |
| R5 | 版心行距达标（P1-5） | `docxgen.py:51-54` `_set_line_exact` 同时写 `w:before="0" w:after="0"`；`generate_docx` 里对 `docDefaults` 的 spacing 显式清零（或改 styles 的 Normal） | S | 中低：会改变所有段落垂直节奏（现有测试若按旧行数断言需同步） | WPS 渲染行距 28.0±0.5pt；每页 ≥21 行（22 行需再核首行起点，见 §6-V3）；`test_compile_pdf` 增几何断言 | 不动 |
| R6 | PDF 行距可控（P1-6） | `pdfrender.trees_to_html` 生成 HTML 后，改为在 `_render_html_to_pdf` 里用 `QTextCursor` 遍历 block 并 `QTextBlockFormat.setLineHeight(pt, QTextBlockFormat.LineHeightTypes.FixedHeight)`；或对每段输出 `style="line-height:Npt"` 的 `<p>` 并验证 Qt 是否吃（实测不吃，故选前者） | M | 中：改的是 PDF 渲染核心，需回归目录页码（两遍法依赖分页稳定） | `line_spacing_pt` 20/28/40 三档在 PDF 里可观测到不同行距；28pt 时 22 行/页；120 标题目录页码仍 120/120 | 不动 |
| R7 | PDF 解析合并段落 + 识别编号标题（P1-7/P2-4） | `pdf_parser.py:42-46`：按行 y 间距（>1.4×行距视为新段）+ 行首缩进 + 上一行是否以句末标点收尾做合并；标题识别叠加 `txt_parser._HEADING_RE`（同字号也能认「一、」「（一）」） | M | 中：合并规则可能误合并（表格/居中标题），需用 3~5 份真实 PDF 校 | 90 字自然段 → 1 个 block；「一、总体要求」→ heading level 2；现有 PDF 样张目录非空 | 不动 |
| R8 | 消除缩进叠加（P1-8） | 二选一：① `formatter.ensure_indent_placeholder` 只做清理不插全角空格（改 `formatter.py:57-75`，日志文案同步）；② `docxgen._add_body` 写正文前剥掉行首 2 个全角空格（`docxgen.py:289-297`）。推荐①（纯文本视图本身也不需要手工缩进），②作为兜底 | S | 低：①会让"纯文本预览"看起来没缩进，但 docx/pdf 侧本来就有缩进 | 一键排版后汇编：首行 x0=111.4±2pt；纯文本视图不出现 `　　` 前缀 | 不动 |
| R9 | .doc 批量导入提速（P1-9） | `doc_parser.py:150-180` 调整顺序：先 L3 `_extract_text_olefile`，结果 `strip()` 非空且长度 ≥ 阈值（如 200 字或 ≥50% 期望值无法预知时取"非空"）直接返回；否则再走 COM/LibreOffice。另加设置项「高保真 .doc 转换（较慢）」供需要文本框/页眉内容的用户开启 | S | 中：L3 不覆盖文本框/页眉页脚里的文字（红头要素可能在文本框内）→ 必须保留可升级路径与日志 | 5 份 .doc 批量 <0.1s；FIB 损坏件仍能出内容；开启高保真时行为与现在一致 | 不动（复用 pywin32 可选依赖） |
| R10 | 水印覆盖奇偶页（P1-10） | `watermark.py:96-102`：`sec.header` / `sec.even_page_header` / `sec.first_page_header` 三处都注入（按 `settings.evenAndOddHeaders` 判断是否必须偶数页） | S | 低 | A/B 渲染：奇数页与偶数页矢量路径条数一致（5/5）；`test_genchain_probe` 增断言 | 不动 |
| R11 | 同名附件都进包（P1-11） | `exporter.py:202-210`：arc 名改用落盘唯一名（`Path(item['stored']).name`），重名再 `_2` 兜底；manifest 保留原始 `name` 供阅读 | S | 低：包内文件名会出现 `_2` 后缀，需在 manifest 里说明 | 两份同名不同内容附件都进包且 sha256 各自匹配；`verify_package` ok | 不动 |
| R12 | 错误文案补齐（P2-2/3） | `errmsg.py`：为 `ValueError("...encrypted")`、`FileDataError`/`ReaderError`（PyMuPDF）、`BadZipFile` 增分支；`_TEMPLATE_PACKAGE_NOT_FOUND` 拆成 Word/XLSX 两版；`PackageNotFoundError` 里若 `os.path.exists` 为假则改报「找不到文件」 | XS | 低 | 加密 PDF → 「文件已加密，请去除口令后另存」；不存在的 .docx → 「找不到文件」 | 不动 |
| R13 | 失败清单可追溯（P2-8） | `import_dialog.py:298-309` 文件名加时间戳（或用 `w`→`a` 追加并写轮次头） | XS | 低 | 连续两轮导入各留一份清单 | 不动 |
| R14 | 表格表头跨页重复（P2-6） | `docxgen._add_table`（`:310-316`）给第 0 行加 `<w:tblHeader/>`；PDF 侧对超长表按行数切分多张表并每张重复表头 | S | 低 | 40 行表格跨 3 页，每页首行都是表头 | 不动 |
| R15 | 全角数字逐位转换（P2-7） | `formatter.py:20` `re.subn(r"[０-９]{2,}", ...)` → `r"[０-９]+"` | XS | 极低 | `２０２６年８月３０日` → `2026年8月30日`；单元测试补 1 例 | 不动 |
| R16 | 批量模式产物不空洞（P2-9） | `batch.batch_compile_each`（`:73-78`）里 `tpl.toc_enabled = False`（单篇材料无需目录）；`docxgen` 当 `insert_material_titles=False` 且 `tree.title == blocks[0].text` 时跳过重复首块 | S | 低 | 每份产物页数 -1；封面标题与正文不再重复 | 不动 |
| R17 | 小册子工程化（P2-11） | 删除未用参数/常量；在每张 A3 中线加浅灰折痕线（可选开关）；源页非 A4 时在日志告警 | XS | 低 | 开关关闭时产物与现状逐字节一致 | 不动 |

**红线核对**：R1~R17 全部为**已装依赖**内改动（python-docx / PyMuPDF / PySide6 / 标准库），
**不新增运行时依赖**、不联网、不引模型/torch，Python 3.9 语法兼容（无 `X | Y` 类型注解新增，无 match），
arm64 无架构相关代码。R3-B/R9 只在既有 `pywin32`（Windows 可选）分支内使用，麒麟侧走非 COM 路径。

---

## 5. 不建议做的项

1. **不建议为了"docx 与 PDF 版式一致"去让 PDF 渲染器解析 docx / 或在 docx 里塞固定分页**。
   两套渲染器（Qt 富文本 vs OOXML）的分页模型本质不同，强行对齐必然引入更复杂的对齐代码；正确做法是把差异显式化（PDF 用内置渲染器算页码，docx 交给 Word/WPS 更新域），并在 UI 上把"两个格式各自的既有差异"写清楚。触碰红线：会显著增加回归面。
2. **不建议把 `.doc` 的 COM 通路改成默认并扩大使用**（如导入时无脑转 docx 再入库）。
   实测 2405x 的耗时差 + WPS/Word 进程残留/SEH 风险（源码注释自陈曾整进程崩溃），收益（文本框文字）只在少数材料上体现。应反向：默认走纯 Python，按需升级。
3. **不建议引入 openpyxl / pandas / docx2python / pdfplumber 等**解决 xlsx 读、PDF 段落合并。
   已有零依赖实现（`xlsx.py` / `xlsx_read.py`）且实测正确；新增依赖要过体积/许可/ARM64 三条线，违背红线。
4. **不建议把 OCR 的 DPI 从 220 往上调（如 300）来"提高精度"**。
   本机无法验证成功率，但 `ocr_pdf` 是逐页 `subprocess` 调 tesseract（`ocr.py:147-153`），DPI 线形增加渲染与识别时间；精度收益应先有实测对比再谈，且缺少 chi_sim 时提示已覆盖。
5. **不建议把移交包的附件上限（8MB）改成"不限制"或默认负值**。
   移交包常走 U 盘/内网邮件，`exporter.py:106-109` 的排除+manifest 记录已经是正确姿势；需要大附件时应提供"分卷/仅清单"选项而不是无声放大。
6. **不建议在 `docxgen` 里对用户正文做"智能清洗"（如自动去空行、自动改标点）**。
   与"不静默改用户正文"直接冲突；R2 只删 XML 不允许的码位属于存储层不可表达，另当别论。
7. **不建议给"内置 PDF 渲染器"增加表格分页/孤行控制之外的复杂排版（首字下沉、分栏等）**。
   Qt 富文本能力边界清晰（本次实测连 `line-height` 都不吃），投入产出比差；公文场景不需要。

---

## 6. 待验证 / 不确定项

| # | 事项 | 为什么不确定 | 验证方法 |
|---|---|---|---|
| V1 | 麒麟 ARM64 上拖拽目录时**大写扩展名文件漏导** | `Path.rglob(f"*{ext}")` 在 POSIX 下大小写敏感（`os.path.normcase` 为恒等），`A.DOCX` 不会匹配 `*.docx`；本机是 Windows（`normcase` 会转小写，实测 `B.DOCX` 命中），**无法在 Windows 上复现** | 麒麟上：`mkdir -p /tmp/t && touch /tmp/t/A.DOCX /tmp/t/b.docx && python -c "from pathlib import Path; print([p.name for p in Path('/tmp/t').rglob('*.docx')])"`；若只出 `b.docx` 则确认缺陷（落点 `ui/import_dialog.py:184-185`，建议改 `p.is_file() and p.suffix.lower() in EXTS` 的全量枚举） |
| V2 | 无任何中文字体时内置 PDF 渲染的兜底是否真的生效 | 本机字体齐全（`missing_fonts()` 返回 `[]`），`pdfrender.ensure_cjk_font()` 的注入分支（`:83-94`）走不到 | 在精简容器/麒麟最小安装里跑 `render_compiled_pdf`，检查 PDF 是否含文字层（`page.get_text()` 非空） |
| V3 | 修完行距后能否达到「22 行」而非 21 行 | 加 `w:before/after=0` 后实测 28.0pt、21 行/页；差 1 行可能来自首行起笔位置或 WPS 的精确行距实现 | 分别用 MS Word 与 WPS 打开同一 docx，读页内行数；必要时把 `line` 调到 27.5/27pt 或核对 `page_rect` 起点 |
| V4 | docx 目录在 **MS Word**（非 WPS）下是否自动更新 | 本机只有 WPS 12.0（实测不更新）；`w:updateFields=true` 在 Word 里的行为需真机确认 | Windows+Word 打开 `docxgen` 产物，看目录是否自动生成（不按 F9） |
| V5 | OCR 成功路径（chi_sim 识别率、逐页进度、临时目录清理） | 本机无 Tesseract（`ocr.available()==False`），只验证了"缺包提示" | 装 Tesseract+chi_sim 后导入 5 页扫描件 PDF，核对 `ocr_progress_cb` 页序、`%TEMP%\gwtool_ocr_*` 是否清空、识别字数 |
| V6 | LibreOffice 分支（L2 / `docx_to_pdf` 的 soffice 优先） | 本机无 soffice/libreoffice（`shutil.which` 均为空），实测走的是 COM 分支 | 麒麟/容器装 libreoffice 后重跑 `docx_to_pdf` 与 `.doc` 的 L2，核对产物与临时目录清理 |
| V7 | 批量汇编 50 份的端到端耗时（含 WPS 无关的内置 PDF 路径） | 本次只测了 4 份（3 成 1 败）与单份 docx→PDF（1.5s/份）；50 份 docx+PDF 的规模未实测 | 造 50 份 20 页材料，走 `CompileWizard` 批量分支计时（注意会写导出目录） |
| V8 | `pdfrender` 在**真实字体**（非 offscreen 缺字体目录告警）下的行距基数 | 离屏 Qt 会打印 `QFontDatabase: Cannot find font directory`，理论不影响 CJK 度量（实测 27 字/行与理论一致），但 20.6pt 的行距基数可能含平台差异 | 在带完整字体的桌面会话里重跑 §2.2-O 的三档对照，确认"参数无效"结论不变 |
| V9 | 超大 .docx（>12000 段）解析 6.8s 是否会在 UI 上"卡顿感知" | 解析在 `ImportWorker` 线程内，主线程不阻塞；但 50 份 × 该量级 = 数分钟，进度条是否够用未评估 | 造 10 份 12000 段 docx 走 ImportDialog，观察进度文案与取消响应 |

---

### 附：本次审计的探针与产物

- 探针脚本：`%TEMP%\gwaudit\p*.py`（临时目录，随 TEMP 清理；正文 §0/§2 已给出每个结论的最小复跑片段，可独立重建）。
  期间共享目录 `doc/_audit_v19/probes/` 被其他成员误删一次（已与 lead / audit-correction 确认），本报告所有数字均来自删除前已完成的运行记录，并已在 TEMP 重建关键探针复跑核对（P1-5 根因、P1-10 水印 A/B、P1-9 批量耗时、P1-3 目录域）。
- 产物样张：`%TEMP%\gwaudit\out\*.docx|*.pdf`（WPS 渲染版）、`%TEMP%\gwaudit\samples\*`（编码/损坏/大文件样张）。
- 仓库内未留下任何文件改动（`git status` 仅 `?? doc/_audit_v19/`，为本报告目录）。
