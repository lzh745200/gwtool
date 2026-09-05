# 公文汇编助手（单机离线版）

面向党政机关、企事业单位的**单机版智能公文汇编与写作辅助工具**。完全离线运行，
支持 **Windows 10/11 (x64)** 与 **麒麟 V10 (ARM64)**。当前版本 **v1.3.2**。

核心解决五大痛点：材料收集散乱、格式调整繁琐、错别字难查、写作无参考、发文无台账。

> 📖 新接触本项目？请先阅读 [项目文件结构说明.md](项目文件结构说明.md)——
> 逐文件讲解每个目录/文件的职责、代码数据流与"我想改 X 该去哪个文件"速查表。

---

## 功能总览

| 模块 | 说明 |
|------|------|
| 材料导入 | 拖拽/选择批量导入 .docx .doc .wps（WPS/Works，内容嗅探路由，装 WPS 机器 COM 保真）.txt .rtf .pdf .md .html，扫描件经 OCR 识别（可选），内容去重 |
| 新建公文 | 15 种法定文种骨架（决议/决定/命令/公报/公告/通告/意见/通知/通报/报告/请示/批复/议案/函/纪要），填要素即成稿 |
| 文秘工具箱 | 编辑器右键：金额大写、日期大写、数字大写、简繁转换、全半角切换（OpenCC 离线词典） |
| 一键汇编 | 三步向导：选材料（拖拽排序）→选模板→生成；支持批量模式（每份材料独立成文） |
| 发文登记台账 | 发文字号/文种/主送抄送/密级/成文印发日期/拟核签人/印数全要素登记；按年度·机关·文种·状态组合筛选；统计报表（按文种/机关/状态分布 + 逐月发文量 + 占比）；导出 UTF-8-BOM CSV（Excel 直接可读）；发文字号自动取号（按机关代字与年度流水，避免撞号） |
| 文字纠错 | 4 万+ 错别字/易混词、新华社禁用词、机构沿革对照、标点数字规则、持久忽略名单 |
| 格式体检 | GB/T 9704 合规检查：标题编号链条、发文字号、成文日期、结束语与文种匹配、字体字号行距；结果可一键导出为规范 DOCX 报告（复用公文排版，可归档或转交拟稿人整改） |
| 模板自定义 | 页边距、字体、行距、标题层级、红头、版记、页码、水印/密级标注，保存即生效 |
| A4 / A3 小册子 | A4 纵向标准公文 PDF；A3 横向骑马钉小册子（自动补页、页序自动排列） |
| 资料分类检索 | 树形分类、标签、SQLite FTS5 全文检索（1 秒内返回命中段落） |
| 写作参考 | 输入词语/主题，从资料库、词典、句式库按相关度检索，双击一键插入 |
| 词典/词库扩充 | 自定义词条、纠错对、常用句式、忽略名单，支持批量导入，立即生效 |
| 纠错规则集 | 纠错对按「来源」成组管理：整体启用/停用（停用后立即不参与纠错，数据保留可随时恢复）、CSV 双向导入导出（UTF-8-BOM，含来源与启用状态，可原样导回或分发到别的电脑），承载「单位内部规范词库」。注：程序内置的人工精标对始终生效，不受该开关影响 |
| 文档对比 | 两文档红绿差异视图（增/删/改 + 相似度统计） |
| 相似查重 | SimHash 粗筛 + 字符三元组 Jaccard 精判，找出高度相似的材料对 |
| 跨文档批量替换 | 支持正则，按全部/当前分类范围，先预览命中再执行 |
| 排版微调 | 一键处理首行缩进、多余空格、全半角、段间空行、标题编号 |
| 历史版本 | 每 3 分钟自动快照（每文档保留 30 版），差异预览一键回滚 |
| 朗读校对 | 离线 TTS 逐句朗读（Win SAPI / 麒麟 espeak-ng），F9 开停 |
| 便携模式 | `main.py --portable` 数据存程序同级 Data/，U 盘随带随走 |
| 安全 | 启动口令锁（PBKDF2·12万次迭代）、AES 加密备份（pyzipper）、退出自动备份+轮转保留 20 份；附件按体积上限随包（手动/自动分别可配），装不下的写进包内清单、恢复时明确提醒，绝不静默丢 |
| 系统集成 | Windows 右键菜单（`scripts/install_context_menu.bat`）、剪贴板一键入库 |

离线数据（实测 seed.db，15.7 MB 随包分发）：错别字/混淆对 **40220 条**（人工精标 220 +
程序化生成 40000）；机构沿革对照 52 条；词典 **123393 条**（开源 CC-CEDICT）；
简繁转换用 OpenCC（MIT）；全程不发起网络请求。

## 快速开始（Windows 开发机）

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py        # 启动
.venv\Scripts\python -m pytest tests\ -q   # 运行测试（177 个用例）
python scripts\e2e_check.py         # 端到端自检（14 步全流程 PASS/FAIL 清单）
python scripts\smoke_dist.py dist\gwtool   # 打包后校验产物（需先打包）
```

## 持续集成（GitHub Actions）

推送 `v*` 标签或手动触发 `.github/workflows/build.yml` 即自动完成：
两个平台（windows-latest x64 与 ubuntu-24.04-arm **原生 ARM64** runner）各自
执行 ruff 静态检查 → pytest 全量测试 → **端到端自检**（`scripts/e2e_check.py`，
14 步全流程）→ PyInstaller 打包（共用 `gwtool.spec`）→ **产物冒烟校验**
（`scripts/smoke_dist.py`：资源齐全 + 真实启动 + 首启动种子导入）→
产出 4 类安装包（Windows 便携 zip、Windows Inno Setup 安装 exe、ARM64 deb、
ARM64 便携 tar.gz）并自动创建 GitHub Release（deb 内置 desktop 文件并声明
Qt 运行库依赖，推荐安装 tesseract-ocr-chi-sim 以启用 OCR）。

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
- `dist\gwtool\gwtool.exe` —— 目录版（启动最快，≤5 秒）
- `dist\gwtool_便携版.zip` —— 免安装压缩包
- 安装包：用 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 编译 `scripts\setup_windows.iss`

### 麒麟 V10 ARM64

CI（推 v* 标签）在 **Debian 11 容器（glibc 2.31）** 内打包，并锁定
`PySide6==6.8.0.2`（Qt 6.8 LTS）。版本底线由此决定：**ARM64 包要求
glibc ≥ 2.31**（麒麟桌面 V10 / Ubuntu 20.04 底层即可运行）；x86_64 包
manylinux_2_28，**glibc ≥ 2.28 的麒麟桌面/服务器均可**。官方 PySide6 的
aarch64 wheel 自 6.8.1 起要求 glibc ≥ 2.39（麒麟全系不满足），故不追新。
同时提供 **x86_64 与 ARM64 两种架构**。手动在麒麟机上打包：

**方式一（有网络）**：把整个项目拷到麒麟机器，执行：
```bash
bash scripts/build_kylin_arm64.sh
```

**方式二（完全离线）**：先在一台有网络的电脑上下载 ARM64 离线依赖：
```bash
bash scripts/kylin_offline_wheels.sh    # 生成 wheels_aarch64/
```
把项目（含 wheels_aarch64/）拷到麒麟机器，再执行：
```bash
bash scripts/build_kylin_arm64.sh       # 自动检测离线 wheel 并安装
```

产物：`dist/gwtool/gwtool`（目录版，启动器 `gwtool.sh` 附带运行库预检），
安装包 `gwtool_kylin_<架构>.run` 自解压安装（含架构校验 + 桌面入口）。

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
`logs\`（备份等运维日志）。程序本体不含用户数据，
重装/升级不影响数据；"备份/恢复"功能可整体迁移到另一台电脑。

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
| 纠错库 ≥3 万条 | 精标 220 + 生成 40220 | test_corrector::test_seed_db_pair_count |
| 模板即时生效 | JSON 模板实时渲染 | UI 内置预览 |
| A3 骑马钉页序正确 | PyMuPDF 重排 8,1\|2,7… | test_booklet_order_math |
| 检索 1 秒内 | FTS5 + jieba 预分词 | test_fts_search_speed_and_snippet |
| 写作参考可插入 | BM25 三库联合检索 | test_reference_lookup |
| 零网络请求 | 全离线设计 | 代码审计：无 socket/requests 调用 |

## 目录结构

```
gwtool/
├── main.py                     # 程序入口（--portable / --import 命令行参数）
├── gwtool.spec                 # PyInstaller 打包配置（双平台共用，参数唯一来源）
├── requirements.txt            # 运行依赖
├── ruff.toml                   # 静态检查（E9+F821：拦截"漏导入即崩溃"类缺陷）
├── gwtool/                     # 主包
│   ├── app.py                  # 启动装配：种子导入 → 口令锁 → 主窗口
│   ├── paths.py                # 数据目录（%APPDATA% / ~/.local/share / 便携 Data/）
│   ├── db/                     # 数据层
│   │   ├── schema.py           #   9 张业务表 + 3 个 FTS5 虚表 + 版本迁移
│   │   ├── connection.py       #   线程本地连接、WAL、迁移前自动备份
│   │   ├── dao.py              #   唯一数据访问入口（文档/词典/纠错对/模板/快照…）
│   │   └── tokenize.py         #   jieba 分词（建索引 + 构造 MATCH 查询）
│   ├── core/                   # 纯逻辑层（不含 UI）
│   │   ├── model.py            #   DocTree/Block 统一中间结构
│   │   ├── importer.py         #   导入调度器（按扩展名分发 + OCR 回退）
│   │   ├── parsers/            #   6 格式解析器：docx/doc/txt/rtf/pdf/md_html
│   │   ├── skeletons.py        #   15 种法定文种骨架
│   │   ├── template.py         #   GB/T 9704 排版参数模型（默认模板）
│   │   ├── docxgen.py          #   规范 DOCX 生成（TOC 域/奇偶页脚/红头）
│   │   ├── compiler.py         #   汇编编排 + docx→pdf 转换链
│   │   ├── pdfrender.py        #   内置 PDF 渲染器（两遍渲染算目录页码）
│   │   ├── booklet.py          #   A3 骑马订小册子（页序算法）
│   │   ├── corrector.py        #   三级纠错流水线 + 词边界保护
│   │   ├── corrector_data.py   #   内置精标对/机构沿革/上下文与标点规则
│   │   ├── inspector.py        #   GB/T 9704 格式体检（文本级 + docx 级）
│   │   ├── toolbox.py          #   文秘工具箱（金额/日期大写、简繁、全半角）
│   │   ├── formatter.py        #   一键排版微调
│   │   ├── differ.py           #   文档对比（词级 diff → 红绿 HTML）
│   │   ├── simhash.py          #   SimHash + Jaccard 相似查重
│   │   ├── reference.py        #   写作参考（三库联合 BM25 检索）
│   │   ├── batch.py            #   批量汇编（单份失败不中断）
│   │   ├── tts.py              #   离线朗读（SAPI/spd-say/espeak-ng）
│   │   ├── watermark.py        #   PDF/DOCX 水印与密级标注
│   │   ├── ocr.py              #   Tesseract OCR（可选，chi_sim 预检）
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
│   │   ├── workers.py          #   QThread 工作线程（FnWorker 等 7 类）
│   │   ├── icons.py            #   纯代码内嵌 SVG 图标（零图片资源）
│   │   ├── widgets.py          #   公文字体常量与公共组件
│   │   └── theme.py            #   语义色常量（深浅色兼容）
│   └── resources/data/
│       └── seed.db             # 种子库：词典 12.3 万 + 纠错对 4 万（15.7 MB）
├── tests/                      # pytest 测试套件（87 个用例，含性能验收）
├── scripts/                    # 构建与运维脚本
│   ├── build_windows.bat       #   Windows x64 打包（PyInstaller + 便携 zip）
│   ├── build_kylin_arm64.sh    #   麒麟 ARM64 打包（支持离线 wheels）
│   ├── kylin_offline_wheels.sh #   有网机器预下载 ARM64 离线依赖
│   ├── setup_windows.iss       #   Inno Setup 安装包脚本
│   ├── install_context_menu.bat / uninstall_context_menu.bat  # 右键菜单
│   ├── gwtool.desktop          #   Linux 桌面入口
│   ├── seed_data.py            #   构建期生成 seed.db（词典下载+混淆对生成）
│   ├── e2e_check.py            #   端到端自检（9 步全流程）
│   └── api_commit.py           #   GitHub API 提交备援工具
└── .github/workflows/build.yml # CI：双平台测试+打包+自动 Release
```
