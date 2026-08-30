# 公文汇编助手（单机离线版）

面向党政机关、企事业单位的**单机版智能公文汇编与写作辅助工具**。完全离线运行，
支持 **Windows 10/11 (x64)** 与 **麒麟 V10 (ARM64)**。

核心解决四大痛点：材料收集散乱、格式调整繁琐、错别字难查、写作无参考。

---

## 功能总览

| 模块 | 说明 |
|------|------|
| 材料导入 | 拖拽/选择批量导入 .docx .doc .txt .rtf .pdf .md .html，扫描件经 OCR 识别（可选），内容去重 |
| 新建公文 | 15 种法定文种骨架（决议/决定/命令/公报/公告/通告/意见/通知/通报/报告/请示/批复/议案/函/纪要），填要素即成稿 |
| 文秘工具箱 | 编辑器右键：金额大写、日期大写、数字大写、简繁转换、全半角切换（OpenCC 离线词典） |
| 一键汇编 | 三步向导：选材料（拖拽排序）→选模板→生成；支持批量模式（每份材料独立成文） |
| 文字纠错 | 4 万+ 错别字/易混词、新华社禁用词、机构沿革对照、标点数字规则、持久忽略名单 |
| 格式体检 | GB/T 9704 合规检查：标题编号链条、发文字号、成文日期、结束语与文种匹配、字体字号行距 |
| 模板自定义 | 页边距、字体、行距、标题层级、红头、版记、页码、水印/密级标注，保存即生效 |
| A4 / A3 小册子 | A4 纵向标准公文 PDF；A3 横向骑马钉小册子（自动补页、页序自动排列） |
| 资料分类检索 | 树形分类、标签、SQLite FTS5 全文检索（1 秒内返回命中段落） |
| 写作参考 | 输入词语/主题，从资料库、词典、句式库按相关度检索，双击一键插入 |
| 词典/词库扩充 | 自定义词条、纠错对、常用句式、忽略名单，支持批量导入，立即生效 |
| 文档对比 | 两文档红绿差异视图（增/删/改 + 相似度统计） |
| 相似查重 | SimHash 粗筛 + 字符三元组 Jaccard 精判，找出高度相似的材料对 |
| 排版微调 | 一键处理首行缩进、多余空格、全半角、段间空行、标题编号 |
| 历史版本 | 每 3 分钟自动快照（每文档保留 30 版），差异预览一键回滚 |
| 朗读校对 | 离线 TTS 逐句朗读（Win SAPI / 麒麟 espeak-ng），F9 开停 |
| 便携模式 | `main.py --portable` 数据存程序同级 Data/，U 盘随带随走 |
| 安全 | 启动口令锁（PBKDF2）、AES 加密备份（pyzipper）、退出自动备份+轮转 |
| 系统集成 | Windows 右键菜单（`scripts/install_context_menu.bat`）、剪贴板一键入库 |

离线数据：错别字/混淆对 ≥3 万条（构建期程序化生成+人工精标）；
词典为开源 CC-CEDICT（12 万词条）；简繁转换用 OpenCC（MIT）；全程不发起网络请求。

## 快速开始（Windows 开发机）

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py        # 启动
.venv\Scripts\python -m pytest tests\ -q   # 运行测试
```

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

产物：`dist/gwtool/gwtool`（可直接分发目录），安装 makeself 后生成
`gwtool_kylin_arm64.run` 自解压安装包。

**麒麟前置条件**：`sudo apt install python3 python3-venv python3-pip`
（麒麟 V10 一般自带 Python 3.7+；若系统 Python 低于 3.9，可用 `pyenv` 或源码
编译 Python 3.9，requirements 中所有库均支持 3.9）。PyMuPDF、PySide6 均有
官方 aarch64 wheel；PySide6 在麒麟上需系统存在 Qt 相关运行库
（`sudo apt install libgl1-mesa-dev libxkbcommon0 libxcb-*` 视报错补装）。

## 数据目录

| 平台 | 位置 |
|------|------|
| Windows | `%APPDATA%\gwtool\` |
| 麒麟/Linux | `~/.local/share/gwtool/` |

包含：`gwtool.db`（全部数据）、`backups\`（备份）。程序本体不含用户数据，
重装/升级不影响数据；"备份/恢复"功能可整体迁移到另一台电脑。

## 公文字体说明

汇编默认使用 `仿宋_GB2312 / 黑体 / 楷体_GB2312 / 方正小标宋简体`（党政机关
标准字体，随 WPS/Office 常见安装）。软件不打包字体（版权原因）；若目标机缺
字体，Word 中会以近似字体显示，文档内容与版式参数不受影响，装上字体后恢复。
启动时会自动检测并提示缺失字体。

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
├── main.py                 # 入口
├── gwtool/
│   ├── app.py              # 启动装配 + 首次运行种子导入
│   ├── paths.py            # 数据目录
│   ├── db/                 # schema / connection / dao / tokenize
│   ├── core/               # parsers(6格式) importer template docxgen
│   │                       # compiler corrector booklet pdfrender
│   │                       # formatter differ reference backup
│   ├── ui/                 # main_window 三栏布局 + 各对话框
│   └── resources/data/     # seed.db（词典+纠错库，随包分发）
├── tests/                  # pytest 测试套件
└── scripts/                # 打包脚本（Windows/麒麟）+ Inno Setup
```
