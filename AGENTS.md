# AGENTS.md

本仓库（公文汇编助手 / gwtool）的代理协作说明。面向在本仓库中工作的编码代理。

## 项目速览

- 形态：**Python 桌面应用**（PySide6 + SQLite），单机离线运行，零网络请求。
- 双平台交付：Windows 10/11 x64 与麒麟 V10 ARM64；打包用 PyInstaller（参数唯一来源 `gwtool.spec`）。
- 分层：`gwtool/core/`（纯逻辑，无 UI 依赖）、`gwtool/db/`（数据访问）、`gwtool/ui/`（Qt 界面）、`scripts/`（构建与运维）、`tests/`（pytest）。
- 深读代码前先看 [项目文件结构说明.md](项目文件结构说明.md)；构建/发布细节看 [README.md](README.md)。

## 常用命令

```bat
.venv\Scripts\python.exe -m pytest tests -q          :: 全量测试
.venv\Scripts\ruff.exe check gwtool/ scripts/ tests/ :: 静态检查
python scripts\e2e_check.py                          :: 端到端自检
```

## Agent skills

### Issue tracker

Issues 与规格以 GitHub Issues 承载（仓库 `lzh745200/gwtool`，用 `gh` CLI 操作）。See `docs/agents/issue-tracker.md`.

### Triage labels

沿用默认五角色词汇，标签字符串与角色名完全一致：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

single-context：一个仓库共用一份根目录 `GLOSSARY.md`，架构决定放 `docs/adr/`。See `docs/agents/domain.md`.
