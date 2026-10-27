# 公文汇编助手（单机离线版）

面向党政机关、企事业单位的**单机版智能公文汇编与写作辅助工具**。完全离线运行，
支持 **Windows 10/11 (x64)** 与 **麒麟 V10 (ARM64)**。当前版本 **v1.20.0**。

> **屏幕口径（v1.11）**：最低分辨率 **1024×640（100% 缩放）**；高 DPI/150% 缩放建议 **1280×720 以上**；对话框超出屏幕时自动夹取到可用区域内。

核心解决五大痛点：材料收集散乱、格式调整繁琐、错别字难查、写作无参考、发文无台账。

> 📖 新接触本项目？请先阅读 [项目文件结构说明.md](项目文件结构说明.md)——
> 逐文件讲解每个目录/文件的职责、代码数据流与"我想改 X 该去哪个文件"速查表。

## 文档索引

**现行（随代码更新）**

| 类别 | 文档 |
|---|---|
| 总览 | [README.md](README.md) · [项目文件结构说明.md](项目文件结构说明.md) |
| 能力基线 | [能力基线_v1.14.md](能力基线_v1.14.md) · [纠错能力校准_v1.14.md](纠错能力校准_v1.14.md) · [写作能力校准_v1.14.md](写作能力校准_v1.14.md) |
| 功能说明 | [一键汇编增强_v1.15.md](一键汇编增强_v1.15.md) · [写作参考库_v1.16.md](写作参考库_v1.16.md) |
| 版本记录 | [v1.20.0修复记录.md](v1.20.0修复记录.md) · [v1.16.0修复记录.md](v1.16.0修复记录.md) · [v1.15.0修复记录.md](v1.15.0修复记录.md) · [v1.14.0修复记录.md](v1.14.0修复记录.md) · [v1.13.0修复记录.md](v1.13.0修复记录.md) · [v1.12.0修复记录.md](v1.12.0修复记录.md) · [v1.11.0修复记录.md](v1.11.0修复记录.md) · [v1.10.0修复记录.md](v1.10.0修复记录.md) · [v1.9.0修复记录.md](v1.9.0修复记录.md) · [v1.8.0增强与修复记录.md](v1.8.0增强与修复记录.md) |
| 构建验收 | [构建验收_v1.15.0.md](构建验收_v1.15.0.md) |

**历史快照（归档、不再更新）**：v1.6–v1.7 的调研/方案/验收/审查报告见 [`archive/`](archive/)；
其中的数字与结论以当时版本为准，不要拿来对照当前实现。

---

## 功能总览

| 模块 | 说明 |
|------|------|
| 材料导入 | 拖拽/选择批量导入 .docx .doc .wps（WPS/Works，内容嗅探路由，装 WPS 机器 COM 保真）.txt .rtf .pdf .md .html，**扫描件 OCR 内置**（Tesseract+中文包随安装包分发，离线开箱即用），内容去重 |
| 新建公文 | 12 种军队机关公文文种骨架（命令/通令/决定/指示/通知/通报/报告/请示/批复/函/通告/纪要），填要素即成稿 |
| 文秘工具箱 | 编辑器右键：金额大写、日期大写、数字大写、简繁转换、全半角切换（OpenCC 离线词典） |
| 一键汇编 | 三步向导：选材料（拖拽排序）→选模板→生成；支持批量模式（每份材料独立成文）。**v1.15 增强**：封面默认生成（要素全空不出页）、封面/目录不编号且正文从 1 起、标题小标宋 + 一级黑体/二级楷体/三级仿宋分层、A4 与 A3 骑马钉同出 |
| 发文登记台账 | 发文字号/文种/主送抄送/密级/成文印发日期/拟核签人/印数全要素登记；按年度·机关·文种·状态组合筛选；统计报表（按文种/机关/状态分布 + 逐月发文量 + 占比）；导出 Excel/CSV；发文字号自动取号（按机关代字与年度流水，避免撞号） |
| 收文登记台账 | 来文的**签收 → 拟办 → 批办 → 承办 → 办结 → 归档**全流程登记：来文机关/来文字号/密级/紧急程度/拟办意见/领导批示/承办部门与承办人/应办结与办结日期/办理结果；按年度·机关·文种·状态筛选；统计报表；导出 Excel/CSV |
| 办理时限督办 | 填了"应办结日期"的收文自动进入督办：启动时扫描已逾期与 2 日内到期的件，状态栏常显角标，点开即见清单并可一键跳转台账。**不做后台常驻轮询**（伤续航且对"天"级业务无意义），提前量可在设置里调 |
| 公文归档 | 已办结收文批量归档：自动编档号（按年度流水）、标注保管期限（**永久 / 30年 / 10年**）、生成可签字盖章随卷的《归档移交清单》（DOCX，走公文排版）或 Excel 汇总。**在办件不允许归档**（会造成卷内缺件） |
| 文字纠错 | 4 万+ 错别字/易混词、新华社禁用词、机构沿革对照、标点数字规则（GB/T 15835 数字/日期/计量共 **21 条**）、持久忽略名单；**语法与表达规则层**：语义冗余（`涉及到`→`涉及`）、动宾搭配（`加强力度`→`加大力度`）、关联词呼应（`只有……就`→`才`）、成对标点未配对；人工精标对 **448 条**（检出率 100%）。**v1.8 生成层精度闸门**：同音生成对若"错形"本身就是合法词典词、或该词形在同一文档里反复出现（作者的术语），一律不报——修掉了 `显式→显示`、`一键→一件`、`重试→重视` 这类"把正确用词改成另一个词"的误报 |
| **文内一致性校验**（v1.8 新增，v1.19 补序号/日期区间） | 同一份材料里同一实体出现两种写法：数量（`3个工作日`/`三个工作日`）、日期格式（`2026年10月3日`/`2026.10.3`）、计量单位（`公里`/`千米`）、机构新旧名称（`环保局`/`生态环境局`）、标点全半角。**只看全文横向矛盾、只提示不改正文**；与既有五层纠错（逐处判错）互补 |
| **语法规则库 v2**（v1.19） | 语法规则扩到 **124 条**：搭配 38 / 语义冗余 26 / 关联词 17 / 语序与的得地 22 / 结构杂糅 11 / 成分残缺 10；改法唯一的自动替换，结构类只提示。真实语料自动误报 0.000 处/万字，病句集召回 93.3% |
| **校对分级与撤销**（v1.17） | `Correction.severity` 三级：**错误**（可自动写回）/ **待确认**（同句第 3 处起或低置信）/ **提示**（数字/机构/生成层/受保护片段）；引号引文、书名号、URL、代码、数字+单位等受保护片段只提示不自动改；同句自动修改 ≤2 处；界面按级别着色、显示理由，支持「接受此类」与「撤销上次」；新增 E7 配对精确率门禁（448 精标对 F0.5=100%，正确形零自动改） |
| **校对报告导出**（v1.8 新增） | 「任意文档纠错」里一键导出 DOCX 校对留痕：校对对象/时间/依据、按类别与置信度分档的结论摘要、逐处明细表（原文/建议/置信度/依据）、可选"修正后正文"。提示类条目显式标注"建议人工确认"，不会让读者误当替换文本 |
| 格式体检 | 军队机关公文格式合规检查：标题编号链条、发文字号、成文日期、结束语与文种匹配、字体字号行距，**并含引文规范、数字用法、文内一致性、公文文风四类**；结果可一键导出为规范 DOCX 报告或 Excel 整改清单（可加"处理意见"列逐条销号） |
| 模板自定义 | 页边距、字体、行距、标题层级、红头、版记、页码、水印/密级标注，保存即生效 |
| A4 / A3 小册子 | A4 纵向标准公文 PDF（595×842pt）；A3 横向骑马钉小册子（1190×842pt、自动补页到 4 的倍数、页序 `[n,1][2,n-1]`、装订边 6pt、印张推导） |
| 资料分类检索 | 树形分类、标签、SQLite FTS5 全文检索（1 秒内返回命中段落） |
| 写作参考 | 输入词语/主题，从资料库、词典、句式库按相关度检索，双击一键插入 |
| **写作参考库**（v1.16） | 加载公版/开放授权语料包（目录或 zip，只读离线），按「论点/论据/过渡/结尾/模板」检索并插入正文；语料包 655 篇 / 119.8 万汉字 / 6.08 万句标注 / 354 条模板（程序加载精选 201 条），入口：工具 → 写作参考库… |
| 写作提示与灵感 | 按文种（12 种军队机关文种骨架）给写作提示，**v1.8 增至 16 项检查**：缺要素、层次序号（跳号/重号/层级颠倒）、标题过长、结构分层、逐段过长、平均句长、单句过长、句式疲劳（连续同字开头）、标点密度（感叹号/问号/省略号过量）、口语化/网络化表达、空泛模糊表述、同段重复短语、自称混用（我单位/本单位）、**文内写法不一致**；并按写作环节推荐可套用的公文句式（自撰 67 条）。**只给建议、绝不改动正文**（模块内没有任何写入入口），全程离线。另附"这篇更像哪个文种"的量化识别 |
| **段落参考**（新增） | 写作参考可选**段落**粒度：命中到"某篇的第几段"，多选累积成「参考清单」，可**溯源**（文档+段序号+正文偏移+段落指纹）、可**跳回原文并高亮**；据此做**内容对齐**（并排比出缺失/多余/章节顺序颠倒/序号跳号重复）与**骨架生成**（把参考段落里的单位名、事项名、日期、时限、数量、引用公文抽成槽位，你填完拼成新稿）。全程离线、纯规则、可解释；生成只做**结构复用**，不编造任何未提供的内容 |
| **公文风格校验**（新增） | 把写作风格量成**可测量、可验收**的指标（篇幅/句长/层级/顿号密度/「一是二是」/力度词配额/引号概念/长引语/百分比/重心分布共 14 项），对照七种文体的语料指纹判断"像不像这个文种"。判定分三层：**硬冲突**（文种识别错误）判错、**软提示**只提醒、**参数对照**只展示。右侧同时给出该文体的**骨架公式**与写作约束，可把结构占位一键填入编辑器 |
| 任意文档纠错 | 任意格式文件/粘贴文本全文纠错：按类别底色**标记视图**、点击定位、逐处/整篇修正、保结构导出 DOCX/TXT |
| 资料移交包 | 按分类/标签/时间段批量导出为移交包（ZIP）：文档按目录树归档、附件可选、`manifest.json` 含每份文档的原文件名与 sha256 校验值。与"整库备份"互补——交接某专题不必把整个数据库交出去 |
| 词典/词库扩充 | 自定义词条、纠错对、常用句式、忽略名单，支持批量导入，立即生效。**多格式词表导入**：CSV/TSV/TXT（自动识别编码与分隔符：UTF-8/GBK/BOM、逗号/制表符/分号）、JSON、Excel(.xlsx)、Word 表格、TBX/XLIFF；导入前先**预检并列出**「新增/覆盖/冲突/跳过/无效」，**确认后才写库**，整批单事务、失败全回滚 |
| 词表的三种用途 | **纠错对**（错→对，直接进纠错）；**行业术语**（规范名+异名 → 按「疑似」档提示，不自动替换）；**保护词**（人名/地名/机构简称等你的合法词：既防止被纠错误判，也能在写作参考里检索到）。每份词表按「来源」成组，可整体启停或一键回退 |
| 纠错规则集 | 纠错对按「来源」成组管理：整体启用/停用（停用后立即不参与纠错，数据保留可随时恢复）、CSV 双向导入导出（UTF-8-BOM，含来源与启用状态，可原样导回或分发到别的电脑），承载「单位内部规范词库」。注：程序内置的人工精标对始终生效，不受该开关影响 |
| 文档对比 | 两文档红绿差异视图（增/删/改 + 相似度统计） |
| 相似查重 | SimHash 粗筛 + 字符三元组 Jaccard 精判，找出高度相似的材料对 |
| 跨文档批量替换 | 支持正则，按全部/当前分类范围，先预览命中再执行 |
| 排版微调 | 一键处理首行缩进、多余空格、全半角、段间空行、标题编号 |
| 历史版本 | 每 3 分钟自动快照（每文档保留 30 版），差异预览一键回滚 |
| 朗读校对 | 离线 TTS 逐句朗读（Win SAPI / 麒麟 espeak-ng），F9 开停 |
| 精度增强包（可选） | 词表外错字的「L4 神经精排」：主包**不带模型**，需要时离线导入独立 `.zip` 增强包（ONNX，含 sha256 校验与 zip-slip/zip-bomb 防护），未导入时纠错行为与三级流水线完全一致 |
| 便携模式 | `main.py --portable` 数据存程序同级 Data/，U 盘随带随走 |
| 安全 | 启动口令锁（PBKDF2·12万次迭代）、AES 加密备份（pyzipper）、退出自动备份+轮转保留 20 份；附件按体积上限随包（手动/自动分别可配），装不下的写进包内清单、恢复时明确提醒，绝不静默丢 |
| 运维自检 | 启动时低频数据库完整性自检（**正常时完全静默**，异常才提示）；「数据库维护」一键碎片整理 + 索引重建；「生成诊断包」把环境信息/能力探测/表计数/运行日志打成一个 ZIP（**绝不含公文正文**，生成前逐项列出将包含的内容）。另有运行期诊断日志（有界轮转、队列异步、不记正文） |
| 系统集成 | Windows 右键菜单（`scripts/install_context_menu.bat`）、剪贴板一键入库 |

离线数据（实测 seed.db，**12.6 MB** 随包分发）：程序化生成的同音混淆对 **39504 条**
（按错形去重 —— 同一错形只给一个建议，结果可复现）；人工精标对 **448 条**在代码内置
（`corrector_data.CURATED_PAIRS` 是唯一事实来源，种子库不再存快照）；机构沿革对照 51 条；
词典 **123393 条**（开源 CC-CEDICT）；简繁转换用 OpenCC（MIT）；全程不发起网络请求。

## 校对能力的已知边界（v1.17 实测口径，别把它当缺陷再"修"一遍）

| 边界 | 现象 | 为什么不修 |
|------|------|-----------|
| 词表外错字召回 | 基础三层对**词表里没有**的错字（如生造搭配）召回为 0 | 词表法的结构性天花板；要靠可选增强包的 L4/L5 神经层补，主包不带模型（三条红线：不引 torch、主包不带模型、零网络） |
| 词表外新词误报 | 只出现一次的现代词/技术词（如"显式"）仍可能被同音生成对报到 | 这类词不在 1990 年代成书的 CC-CEDICT / jieba 词典里，无模型不可判定。生成层（39,504 条，conf 全 0.55）已整层降为**提示级不自动替换**；真实 220 篇语料实测自动替换类误报 **0.00 处/万字**（E3a），提示类 1.27 处/万字（E3b，只报告）；用户加一次词库即永久生效 |
| 数字一致性残留 | 「一个量级」这类不定指与「1 个」计数可能被提示 | 中文里量词+汉字数字的不定指/序数义无法纯规则区分，约每 2 万字 ≤1 条，且只是提示 |
| 标点全半角混用 | 喂 markdown/代码导出文本时行内代码里的半角冒号可能被提示 | 面向 .docx/纯文本正文设计；代码文本本就不是公文语域。**v1.9 起「可自动替换」的标点规则已加语境守卫**：括号内含引号/代码形态、半角省略号前接 ASCII 时不替换，句尾三点按「宁少报不误报」不自动改（六点以上仍归一） |

## 快速开始（Windows 开发机）

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py        # 启动
.venv\Scripts\python -m pytest tests\ -q   # 运行测试（约 2900 个用例）
python scripts\e2e_check.py         # 端到端自检（18 步全流程 PASS/FAIL 清单）
python scripts\smoke_dist.py dist\gwtool   # 打包后校验产物（需先打包）
```

## 持续集成（GitHub Actions）

推送 `v*` 标签或手动触发 `.github/workflows/build.yml` 即自动完成：
两个平台（windows-latest x64 与 ubuntu-24.04-arm **原生 ARM64** runner）各自
执行 ruff 静态检查 → pytest 全量测试 → **端到端自检**（`scripts/e2e_check.py`，
18 步全流程）→ PyInstaller 打包（共用 `gwtool.spec`）→ **产物冒烟校验**
（`scripts/smoke_dist.py`：资源齐全 + 真实启动 + 首启动种子导入）→
产出 2 个离线安装包（Windows Inno Setup 安装 exe、ARM64 deb）并自动创建
GitHub Release（两包均把 Tesseract OCR 引擎与中文包打包在内、零网络依赖；
deb 内置 desktop 文件并声明 Qt 运行库依赖）。

依赖已在 `requirements.txt` **精确锁定**。两处必须分档，不能一刀切：
PySide6 分平台（Windows 6.11.2；Linux/麒麟 6.8.0.2，因官方 aarch64 wheel
自 6.8.1 起要求 glibc≥2.39）；PyMuPDF/markdown/chardet/pytest 分 Python 版本
（麒麟 CI 在 Debian 11 容器内用 Python 3.9 构建，为守住 glibc 2.31 底线不能
升级容器，而这些包的新版已放弃 3.9）。**不要把这些约束改回浮动版本**：v1.2.1
之所以出现"启用口令锁后程序启动即崩"，正是因为 `PySide6>=6.6` 让不同日期
构建出的安装包行为不同，且没有任何环节报警。

## 打包发布

### Windows x64
```bat
scripts\build_windows.bat
```
产物：
- `dist\gwtool\gwtool.exe` —— 目录版（启动最快，≤5 秒；CI 以 Inno Setup 打成唯一安装包 `gwtool_setup_win64.exe`）

### 麒麟 V10 ARM64

CI（推 v* 标签）在 **Debian 11 容器（glibc 2.31）** 内打包，并锁定
`PySide6==6.8.0.2`（Qt 6.8 LTS）。版本底线由此决定：**ARM64 包要求
glibc ≥ 2.31**（麒麟桌面 V10 / Ubuntu 20.04 底层即可运行）。官方 PySide6 的
aarch64 wheel 自 6.8.1 起要求 glibc ≥ 2.39（麒麟全系不满足），故不追新。

> CI 只交付 **ARM64 一种 Linux 安装包**（`gwtool_<版本>_linux_arm64.deb`），
> 因为唯一的 Linux 目标是麒麟 V10 ARM64。需要 x86_64 的麒麟/Ubuntu 时，
> 请按下面的手动流程自行打包（requirements 里 PyMuPDF 为 manylinux_2_28，
> 即 **glibc ≥ 2.28** 即可）。

手动在麒麟机上打包：

**方式一（有网络）**：把整个项目拷到麒麟机器，执行：
```bash
bash scripts/build_kylin_arm64.sh
```

**方式二（完全离线）**：先在**一台有网络的 ARM64 Linux 机器**（或 arm64 容器）
上下载离线依赖——注意不能在 Windows 或 x86_64 机器上交叉下载，
requirements 里的环境标记按运行主机求值，会选错分档：
```bash
bash scripts/kylin_offline_wheels.sh    # 生成 wheels_aarch64/（含前置环境校验）
```
把项目（含 wheels_aarch64/）拷到麒麟机器，再执行：
```bash
bash scripts/build_kylin_arm64.sh       # 自动检测离线 wheel 并安装
```

产物：`dist/gwtool/gwtool`（目录版，启动器 `gwtool.sh` 附带运行库预检）；
CI 以 dpkg-deb 打成唯一安装包 `gwtool_<版本>_linux_arm64.deb`。

**麒麟前置条件**：`sudo apt install python3 python3-venv python3-pip`
（麒麟 V10 一般自带 Python 3.7+；若系统 Python 低于 3.9，可用 `pyenv` 或源码
编译 Python 3.9，requirements 中所有库均支持 3.9）。PyMuPDF、PySide6 均有
官方 aarch64 wheel；PySide6 在麒麟上需系统存在 Qt 相关运行库
（`sudo apt install libgl1-mesa-dev libxkbcommon0 libxcb-*` 视报错补装）。

## 麒麟安装与启动排障

| 现象 | 原因 | 处理 |
|------|------|------|
| 打开报 `version 'GLIBC_2.3x' not found` | 安装包在比目标机更新的 glibc 上打包（旧版包在 ubuntu-24.04/glibc 2.39 构建，且 PySide6 6.8.1+ 的 aarch64 wheel 需 glibc≥2.39） | 使用 v1.2.1+ 安装包（ARM64 需麒麟桌面版 glibc≥2.31；x86_64 需 glibc≥2.28；终端执行 `ldd --version` 可查） |
| 双击 deb 报 `local variable 'deb' referenced before assignment` | 麒麟自带图形安装器的内部缺陷（该报错措辞为 Python ≤3.10 特征；经逐一核查，本项目 v1.0.0–v1.2.1 全部源码中不存在 `deb` 变量，非本应用问题） | 改用命令行安装：`sudo dpkg -i gwtool_*_linux_*.deb && sudo apt-get -f install`；或直接使用 `.run` 安装包 / 便携版 tar.gz |
| 报 `Could not load the Qt platform plugin "xcb"` | 缺 Qt6 xcb 所需系统库；`dpkg -i` 不会自动装依赖 | `sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libxkbcommon-x11-0 libgl1 libegl1` |
| 报"无法执行二进制文件" | ARM64 包装到了 x86_64 机器（或反之） | 下载与 `uname -m` 一致的安装包（v1.2.1 起 .run 会主动校验架构） |
| 双击无反应 | 错误被桌面入口吞掉 | 运行 `/opt/gwtool/gwtool.sh`（终端可诊断）或看 `~/gwtool_启动诊断.log` |

启动器 `gwtool.sh`（deb / tar.gz / .run 均内置）会在启动前预检：架构是否
匹配、xcb 插件缺哪些库，缺库时把**确切的 apt 安装命令**写入
`~/gwtool_启动诊断.log` 并尝试弹窗提示；deb 的 postinst 也会兜底恢复
可执行位。

## 数据目录

| 平台 | 位置 |
|------|------|
| Windows | `%APPDATA%\gwtool\` |
| 麒麟/Linux | `~/.local/share/gwtool/` |

包含：`gwtool.db`（全部数据）、`attachments\`（文档附件本体）、`backups\`（备份）、
`logs\`（运行期诊断日志，按 2 MB × 5 份轮转；**只记异常与降级事件，不记公文正文**）。
程序本体不含用户数据，重装/升级不影响数据；"备份/恢复"功能可整体迁移到另一台电脑。

## 公文字体说明

汇编默认使用 `仿宋_GB2312 / 黑体 / 楷体_GB2312 / 方正小标宋简体`（党政机关
标准字体，随 WPS/Office 常见安装）。软件不打包字体（版权原因）；若目标机缺
字体，Word 中会以近似字体显示，文档内容与版式参数不受影响，装上字体后恢复。
启动时会自动检测并提示缺失字体。

**零中文字体兜底（v1.3.0 起）**：若目标机上一个中文字体都没有（麒麟最小安装、
精简字体镜像等），程序启动时会自动注入 PyMuPDF 自带的 **Droid Sans Fallback**
（Apache-2.0，随既有依赖离线分发，不额外装字体、不涉及版权问题）作为兜底。
此前这种情况下界面与 PDF 的中文会**整篇渲染成空白且不报任何错**——用户拿到的
是一份看起来正常的空文件，目录页码也全变成"—"。现已由 `pdfrender.ensure_cjk_font()`
兜住，并有 `tests/test_pdf_cjk_font.py` 在 CI 中守住。

## 验收对照

| 需求 | 实现 | 测试 |
|------|------|------|
| 双平台离线运行 | PySide6 + SQLite，零网络调用 | test_app_smoke |
| 50 文件导入<10s | 后台线程批量导入 | test_parsers::test_batch_import_50_files_speed |
| 文字提取>99% | 六格式解析器 + 兜底 | test_parsers |
| 汇编 WPS 不跑版 | 标准 OOXML + 域代码 | test_compile_pdf |
| 截止/截至等 100% 识别 | 三级纠错流水线 | test_corrector::test_jianku_100_curated_pairs |
| 精标集全量检出 100% | `_dedupe` 精标长对优先于短规则（单向消解） | test_grammar_rules::test_dedupe_prefers_lexicon_long_pair |
| 语义冗余/搭配/关联词可检出且零误报 | L2.5 语法与表达规则层（每条规则都有正例 + 27 条负样本守门） | test_grammar_rules::test_no_dead_rules / test_negative_no_false_positive |
| 提示类规则绝不写回正文 | `ADVISORY_CATEGORIES` 单一来源，批量/整篇应用整类排除 | test_grammar_rules::test_correct_block_skips_advisory_categories |
| 写作提示只建议、不改正文 | 模块只产出 Hint/Idea 纯数据，无任何写入入口 | test_writing_hints::test_writing_hints_module_has_no_write_api |
| 写作提示空数据不崩 | 空稿/未知文种/无库/检索异常一律降级 | test_writing_hints::test_inspiration_swallows_reference_error |
| 汇编材料标题不错位 | 标题按**输入槽位**对齐，解析失败/已删材料只跳过自己那一坑 | test_review_round5::TestCompileTitleAlignment |
| 来源清单不虚列出处 | 解析失败的文件不进来源清单（内容根本没进汇编） | test_review_round5::TestCollectSourcesFilter |
| 机构一致性"简称+连接词"场景可检出 | 「县应急局**与**县应急管理局」前导连接字确定性修剪 | test_review_round5::TestEntityLeadTrim |
| 无 Qt 环境字体探测不崩进程 | QFontDatabase 原生崩溃前置拦截（QApplication 判空） | test_review_round5::TestFontcheckNoApp |
| 纠错库 ≥3 万条 | 生成对 39504（按错形去重） | test_corrector::test_seed_db_pair_count |
| **种子库产物契约**（v1.8） | user_version == SCHEMA_VERSION、无遗留/缺失表、无精标快照、错形唯一 | test_seed_artifact |
| 模板即时生效 | JSON 模板实时渲染 | UI 内置预览 |
| A3 骑马钉页序正确 | PyMuPDF 重排 8,1\|2,7… | test_booklet_order_math |
| 检索 1 秒内 | FTS5 + jieba 预分词 | test_fts_search_speed_and_snippet |
| 写作参考可插入 | BM25 三库联合检索 | test_reference_lookup |
| 段落可检索/可溯源 | paragraphs 表 + 段落级 FTS5 | test_paragraph_ref |
| 段落偏移不跳错位置 | 落点不变量：正文字符切片 == 段落原文 | test_paragraph_index::TestAnchorInvariant |
| 段落索引可整表重建 | 索引缺失/过期一律回落现场派生 | test_paragraph_index |
| 段落索引不残留/不重复 | 整体替换 + 哈希判据增量 + 回收站隔离 | test_paragraph_index |
| 对齐规则不误报 | 六维确定性规则（层级/序号/角色/顺序/覆盖/多余） | test_para_align |
| 生成不编造内容 | 槽位示例必须取自参考原文；未填槽位保留占位 | test_para_generate |
| 段落开关可退 | 关掉即回到"整篇"这条原有路径 | test_para_panel::TestActionEnablement |
| 风格量法口径一致 | 14 项指标逐项钉住（含 `不得`/`不得不`、引号内力度词剔除、`【待补】` 里的 `%` 不计） | test_style_profile |
| 风格判定不误判 | 只有硬冲突算错；区间外不等于错（有专门的反例测试） | test_style_verdict::TestHardConflicts |
| 风格检查独立成类 | 不并入 GB/T 9704 版式检查 | test_style_verdict::TestInspectorIntegration |
| 生成不编内容 | 骨架只出【待补】占位，不产生成文内容 | test_style_profile::TestSkeletonDraft |
| 段落检索排对顺序 | FTS5 bm25 越负越相关，词频高的段必须排前 | test_review_round4::TestRetrievalOrder |
| 序号判定不误报 | 重复序号不得引发后续"假跳号"；真跳号仍报 | test_review_round4::TestOrdinalIssues |
| 槽位不误抽单位名 | 普通句不得产出 `{org}`（简称白名单 + 排除集） | test_review_round4::TestOrgSlotFalsePositive |
| 彻底删除级联派生表 | purge 后 paragraphs／段落 FTS／状态表全部归零 | test_review_round4::TestPurgeCascades |
| 骨架文种可选可用 | 下拉必须带 userData；载入需回填 kind | test_review_round4::TestSkeletonKindAndExamples |
| 能力开关真生效 | 关掉后菜单入口一起隐藏（约束 C16） | test_review_round4::TestParaFeatureSwitches |
| 替换预览不可漂移 | 预览期间条件变动即作废，不按新条件改旧清单 | test_review_round4::TestReplacePreviewSnapshot |
| 模态入口全部设防 | 静态文件对话框全局屏蔽（含参数化构造用例） | test_ui_actions_smoke::test_file_dialog_static_methods_blocked |
| 零网络请求 | 全离线设计 | 代码审计：无 socket/requests 调用 |
| 小册子确实生成 | 向导勾选后 worker 真正启动 | test_bugfix_regression::TestBookletWorkerStarted |
| 批量纠错不改错位置 | 上下文锚点重定位 | test_bugfix_regression::TestBatchRelocate |
| 分词不可用不误吞命中 | 门控降级为不过滤 | test_bugfix_regression::TestCorrectorTokenizeFallback |
| 多处日期全报 | 体检遍历全部命中 | test_bugfix_regression::TestInspectorDateAllOccurrences |
| 拖放展开文件夹 | 列表控件自持拖放 | test_bugfix_regression::TestImportDialogDrop |
| 收文可登记/筛选/统计 | `receive_register` 表 + 独立台账面板 | test_receive |
| **旧版本备份仍可恢复** | 只按核心表判定"是不是本程序的库" | test_backup_compat |
| xlsx 导出零新依赖 | 标准库 `zipfile` 手写 OOXML | test_xlsx |
| 诊断包不含正文 | 只输出版本/计数/日志 | test_diagpack::TestPrivacy |
| 督办不打扰 | 只提醒"有应办结日期且未办结"的件 | test_reminder |
| 在办件不许归档 | 服务层与界面层双重拦截 | test_archive |
| 体检能查引文规范 | `_check_citations` 六条规则 | test_inspector_citation |
| 体检不误报文风 | 只收规范公文中不可能出现的口语词 | test_inspector_style |
| **生成层精度闸门**（v1.8）| 错形本身是词典词 / 同文重复出现 → 不报；精标错形永久豁免 | test_precision_gate_v18 |
| **文内一致性**（v1.8）| 数字/日期/单位/机构名/标点五类，只提示不改正文 | test_consistency / test_consistency_integration |
| **校对报告导出**（v1.8）| DocTree 复用公文排版，明细行数 == 命中数，提示类显式标注 | test_correction_report |
| **写作提示 16 项检查**（v1.8）| 每项正/反例；规范范文 0 warn；单类异常隔离 | test_writing_hints_ext |
| **GB/T 15835 数字规范 21 条**（v1.8）| 全部提示级、可自动替换类整类排除；真实语料命中逐条白名单 | test_rules_expansion_v18 |
| **A 批 19 项修复**（v1.9） | 竞态/评测门禁/排版/纠错守卫/写作/台账/快照/恢复/主题/日志，逐项带回归 | test_correct_dialog_race / test_docx_layout_v19 / test_rules_guard_v19 / test_writing_fix_v19 / test_registry_v19 / test_eval_script / test_theme_contrast_v19 / test_data_safety_v19 |
| **B 批 17 项修复**（v1.10） | PDF 行距/分段/页序/水印、.doc 默认纯 Python + 质量兜底、编码探测、控制字符、缩进归一、同名附件、大文档异步、写作提示异步、槽位精确率 9/14→14/14、法定文种识别、发文字号唯一、段落索引体量策略、资料库刷新 229→1.7ms | test_pdf_layout_v19b / test_import_export_v19b / test_ui_perf_v19b / test_slot_extraction_v19b / test_genre_v19b / test_registry_unique_v19b / test_library_refresh_v19b |
| 运行期故障可诊断 | 队列异步日志 + 全局异常钩子 | test_logs |

## 目录结构

```
gwtool/
├── main.py                     # 程序入口（--portable / --import 命令行参数）
├── gwtool.spec                 # PyInstaller 打包配置（双平台共用，参数唯一来源）
├── requirements.txt            # 运行依赖
├── ruff.toml                   # 静态检查（E9+F821：拦截"漏导入即崩溃"类缺陷）
├── gwtool/                     # 主包
│   ├── app.py                  # 启动装配：诊断日志 → 种子导入 → 口令锁 → 主窗口
│   ├── logs.py                 # 运行期诊断日志（队列异步、有界轮转、不记正文）
│   ├── paths.py                # 数据目录（%APPDATA% / ~/.local/share / 便携 Data/）
│   ├── db/                     # 数据层
│   │   ├── schema.py           #   16 张业务表 + 4 个 FTS5 虚表 + 版本迁移（v7）
│   │   ├── connection.py       #   线程本地连接、WAL、迁移前自动备份
│   │   ├── dao.py              #   唯一数据访问入口（文档/词典/纠错对/模板/快照…）
│   │   └── tokenize.py         #   jieba 分词（建索引 + 构造 MATCH 查询）
│   ├── core/                   # 纯逻辑层（不含 UI）
│   │   ├── model.py            #   DocTree/Block 统一中间结构
│   │   ├── importer.py         #   导入调度器（按扩展名分发 + OCR 回退）
│   │   ├── parsers/            #   6 格式解析器：docx/doc/txt/rtf/pdf/md_html
│   │   ├── skeletons.py        #   12 种军队机关公文文种骨架
│   │   ├── template.py         #   军队机关公文格式排版参数模型（默认模板）
│   │   ├── docxgen.py          #   规范 DOCX 生成（TOC 域/奇偶页脚/红头）
│   │   ├── compiler.py         #   汇编编排 + docx→pdf 转换链
│   │   ├── pdfrender.py        #   内置 PDF 渲染器（两遍渲染算目录页码）
│   │   ├── booklet.py          #   A3 骑马订小册子（页序算法）
│   │   ├── corrector.py        #   纠错流水线（L1 词表 / L2 规则 / L2.5 语法表达 / L3 重复字）+ 词边界与精度闸门
│   │   ├── corrector_data.py   #   内置精标对（450）/机构沿革/上下文与标点数字规则（GB/T 15835 共 21 条）
│   │   ├── grammar_rules.py    #   L2.5 语法与表达规则层（语义冗余/搭配/关联词/成对标点）
│   │   ├── consistency.py      #   文内一致性校验（数字/日期/单位/机构名/标点，只提示不改正文）
│   │   ├── csc_neural.py       #   L4 神经精排层（可选 opt-in，依赖缺失即静默降级）
│   │   ├── enhance_pack.py     #   精度增强包导入/校验/卸载（zip 安全 + sha256）
│   │   ├── inspector.py        #   军队公文格式体检（文本级 + docx 级 + 引文/数字/一致性/文风）
│   │   ├── toolbox.py          #   文秘工具箱（金额/日期大写、简繁、全半角）
│   │   ├── formatter.py        #   一键排版微调
│   │   ├── differ.py           #   文档对比（词级 diff → 红绿 HTML）
│   │   ├── simhash.py          #   SimHash + Jaccard 相似查重
│   │   ├── reference.py        #   写作参考（三库联合 BM25 检索）
│   │   ├── writing_hints.py    #   写作提示/文种识别/灵感建议（只给建议，不改正文）
│   │   ├── writing_data.py     #   内置公文句式库与写作提示阈值（自撰，许可干净）
│   │   ├── paragraph_ref.py    #   段落参考（段落检索/引用定位/内容对齐/骨架抽取）
│   │   ├── batch.py            #   批量汇编（单份失败不中断）
│   │   ├── tts.py              #   离线朗读（SAPI/spd-say/espeak-ng）
│   │   ├── watermark.py        #   PDF/DOCX 水印与密级标注
│   │   ├── ocr.py              #   Tesseract OCR（可选，chi_sim 预检）
│   │   ├── receive.py          #   收文登记（来文字号宽松解析、校验、统计、导出）
│   │   ├── archive.py          #   归档与保管期限（批量编档号 + 移交清单 DOCX）
│   │   ├── reminder.py         #   办理时限督办（纯函数计算，不做后台常驻）
│   │   ├── xlsx.py             #   xlsx 导出（标准库手写 OOXML，**零新依赖**）
│   │   ├── dbhealth.py         #   数据库完整性自检与 VACUUM/REINDEX 维护
│   │   ├── diagpack.py         #   一键诊断包（只含版本/计数/日志，绝不含正文）
│   │   ├── exporter.py         #   批量导出与移交包（manifest 与备份同构）
│   │   ├── backup.py           #   备份/恢复（AES 加密、轮转、完整性校验、附件体积上限+缺失清单）
│   │   └── security.py         #   口令锁（PBKDF2-HMAC-SHA256）
│   ├── ui/                     # PySide6 界面层
│   │   ├── main_window.py      #   主窗口（三栏 + 12 动作工具栏 + 5 菜单）
│   │   ├── editor_panel.py     #   编辑/预览/输出预览三页 + 大纲 + 查找替换
│   │   ├── library_panel.py    #   资料库（检索框/分类树/文档列表）
│   │   ├── reference_panel.py  #   纠错结果 + 写作参考双区面板
│   │   ├── import_dialog.py    #   导入对话框（拖拽 + 进度）
│   │   ├── compile_wizard.py   #   一键汇编三步向导
│   │   ├── template_editor.py  #   模板管理（三参数页 + 实时预览）
│   │   ├── dict_manager.py     #   词典/纠错对/句式/忽略名单四页管理
│   │   ├── compare_dialog.py   #   文档对比
│   │   ├── feature_dialogs.py  #   骨架/体检/批量替换/快照/查重/安全/锁屏
│   │   ├── registry_dialog.py  #   发文登记台账（登记/筛选/统计/导出）
│   │   ├── receive_dialog.py   #   收文登记台账（登记/筛选/归档/统计/导出）
│   │   ├── workers.py          #   QThread 工作线程（FnWorker 等 6 类）
│   │   ├── icons.py            #   纯代码内嵌 SVG 图标（零图片资源）
│   │   ├── widgets.py          #   公文字体常量与公共组件
│   │   └── theme.py            #   语义色常量（深浅色兼容）
│   └── resources/data/
│       └── seed.db             # 种子库：词典 12.3 万 + 生成混淆对 3.95 万（12.6 MB，无精标快照）
├── tests/                      # pytest 测试套件（2053 个用例，含性能验收）
├── scripts/                    # 构建与运维脚本
│   ├── build_windows.bat       #   Windows x64 打包（PyInstaller + 便携 zip）
│   ├── build_kylin_arm64.sh    #   麒麟 ARM64 打包（支持离线 wheels）
│   ├── kylin_offline_wheels.sh #   有网机器预下载 ARM64 离线依赖（含可选推理栈）
│   ├── setup_windows.iss       #   Inno Setup 安装包脚本
│   ├── install_context_menu.bat / uninstall_context_menu.bat  # 右键菜单
│   ├── gwtool.desktop          #   Linux 桌面入口
│   ├── seed_data.py            #   构建期生成 seed.db（词典下载+混淆对生成）
│   ├── e2e_check.py            #   端到端自检（55 项全流程）
│   ├── smoke_dist.py           #   打包产物冒烟（含能力面自报断言）
│   ├── check_inference_stack.py #   推理栈自检（双平台 CI 共用，默认失败即非零退出）
│   ├── bench_scale.py          #   大数据量性能基线（临时库，不碰用户数据）
│   ├── eval_corrector.py       #   纠错引擎评测基线（6 项判定 + JSON 落盘）
│   └── api_commit.py           #   GitHub API 提交备援工具（github.com 被阻断时用）
└── .github/workflows/build.yml # CI：双平台测试+打包+自动 Release
```
