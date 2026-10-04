# 第三方来源与许可说明 · lieflat-gongwen

## 来源

| 项 | 内容 |
| --- | --- |
| 仓库 | `larashero3-dotcom/lieflat-gongwen`（"公文写作 DNA"） |
| 地址 | https://github.com/larashero3-dotcom/lieflat-gongwen |
| 取用日期 | 2026-09-23 |
| 许可 | **PolyForm Noncommercial License 1.0.0**（见同目录 `LICENSE`） |

## 本项目的使用范围

本项目（公文汇编助手）**仅作非商业用途**：党政机关/事业单位内部办公使用，
不对外售卖、不用于付费写作或咨询服务、不集成进商业产品或商业服务。

该许可的 *Noncommercial Organizations* 条款明文规定：

> Use by any charitable organization, educational institution, public research
> organization, public safety or health organization, environmental protection
> organization, **or government institution** is use for a permitted purpose
> **regardless of the source of funding** or obligations resulting from the funding.

因此政府机构使用属许可允许的用途。**若本项目后续转为商业用途，必须立即停止使用
本目录下的派生数据**，改为按本项目自有语料重新统计参数（见规格文档 §0.2「路径 B」）。

## 具体派生内容与相应改动

本目录下的许可证覆盖**其上游内容**。本项目从中派生并改动的内容如下：

| 本项目文件 | 派生自上游 | 改动性质 |
| --- | --- | --- |
| `gwtool/core/style_data.py` 的 `REF` | `参数卡.md` + `scripts/check_params.py` 的 `REF` | 逐项照录七文体的中位数与 p25–p75 区间（**数值未改动**），重组为 Python 数据结构 |
| `gwtool/core/style_data.py` 的 `HARD` / `SOFT` / `FOCUS` / `ID_PARAM` / `BAD_WORDS` | 同上两份文件 | 照录规则与判据、文案原文 |
| `gwtool/core/style_data.py` 的 `SKELETON` | `公文语料/文章结构模板.md` | 摘录七文体的**骨架公式**与重心占比区间 |
| `gwtool/core/style_profile.py` 的量法 | `scripts/check_params.py` 的 `analyze()` / `section_weights()` / `match_genre()` / `bar()` | **重写**为项目风格（类型注解、中文注释、分层返回结构），量法口径保持一致 |

**未取用**：上游语料原文（上游自身因版权未随包分发）、上游范文正文。

## Required Notice

上游未提供 `Required Notice:` 抬头行；本目录按许可要求一并提供许可证全文。
