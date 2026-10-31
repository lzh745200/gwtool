# 公文汇编助手（单机离线版）

面向党政机关、企事业单位的**单机版智能公文汇编与写作辅助工具**。完全离线运行，
支持 **Windows 10/11 (x64)** 与 **麒麟 V10 (ARM64)**。当前版本 **v1.21.0**。

> 📖 **产品文档统一维护在 [doc/](doc/)**，本文件只做入口指引（避免同一份内容两处维护、两处漂移）。
>
> - 功能总览 / 快速开始 / 打包发布 / 数据目录 / 验收对照：**[doc/README.md](doc/README.md)**
> - 逐文件讲解"我想改 X 该去哪个文件"：**[doc/项目文件结构说明.md](doc/项目文件结构说明.md)**
> - 本版改了什么：**[doc/v1.20.2修复记录.md](doc/v1.20.2修复记录.md)**（台账重号与体检假清关两处修复；v1.20.1 的十项修复见 **[doc/v1.20.1修复记录.md](doc/v1.20.1修复记录.md)**）
> - 全功能检测与优化建议（P0/P1/P2 清单）：**[doc/全功能检测与优化建议_v1.9.md](doc/全功能检测与优化建议_v1.9.md)**
> - 只读深度审计（v1.8）：**[doc/深审报告_v1.8.md](doc/深审报告_v1.8.md)**

核心解决五大痛点：材料收集散乱、格式调整繁琐、错别字难查、写作无参考、发文无台账。
全程零网络请求，数据存本地 SQLite；主包不带模型，精度增强包按需离线导入。

## 30 秒上手（Windows 开发机）

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

打包：`pyinstaller --noconfirm --clean gwtool.spec`（双平台同一配置）。

## 质量门禁（提交前必须全绿）

```bash
.venv/bin/python -m pytest tests/ -q     # 2053 passed / 13 skipped
ruff check gwtool/ scripts/ tests/ main.py
.venv/bin/python scripts/e2e_check.py    # 55 项端到端自检
```

## 性能基线（v1.19 起）

写作参考库 / 校对 / 检索的耗时由两处守门，防止退化回全表扫描：

```bash
.venv/bin/python -m pytest tests/test_perf_reference_v119.py -q   # 性能契约
.venv/bin/python scripts/bench_scale.py --sizes 200               # 规模基线
```

| 路径 | 基线（真实 12.3 万词典） |
|---|---:|
| 写作参考库检索 `reference.lookup()` | **9 ms** |
| 词典检索（短词 / 长词） | 24.5 / 8.3 ms |
| 段落检索 | < 1 ms（DB） |
| 校对（万字，600 处命中） | 64 ms |
| 汇编生成 DOCX（200 篇） | 41 ms |

> 关键教训：`word` 列的索引是 BINARY 规则，SQLite **不会**把
> `LIKE '前缀%'` 转成索引范围（只有 `COLLATE NOCASE` 才行，而它对中文无效）。
> 前缀检索必须写成 `word >= q AND word < q+\uffff` 才走索引。
> 详见 [doc/v1.19.0修复记录.md](doc/v1.19.0修复记录.md) 第七节。
