# -*- coding: utf-8 -*-
"""词表导入内核：预检 → 预览 → 事务写入。

**为什么把这一步单独成模块**：格式解析（`core/wordfmt/`）与"这些条目与库里
现有数据怎么相处"是两件事。冲突规则、事务性、可回退性只应写一遍 —— 否则
每支持一种格式就要重写一遍冲突逻辑，规则必然漂移。

三步分离（照 `core/batch.py:155-172` 的既有模式）：
  · `precheck()` 只读，算出 `新增 / 覆盖 / 冲突 / 跳过 / 无效` 五类；
  · `preview_lines()` 产出用户可读清单，确认前**不写任何东西**；
  · `apply()` 才写库，且**整批单事务**：任一行失败 → 全部回滚。

角色（role）与落点
------------------
========  ==========================  ==================================
role      落点                        生效方式
========  ==========================  ==================================
pairs     ``error_pairs``             L1 精确词表，confidence 默认 0.99
terms     ``error_pairs``             同上，但默认 0.80（橙「疑似」，不自动改）
protect   ``dictionary`` + 忽略名单   防误纠 + 可检索 + 重复字整段豁免
========  ==========================  ==================================

⚠️ 纠错对**绝不能**只写 `dictionary`：那张表不参与纠错判定（它只被
`repeat_rules` 当作"是不是汉语真词"的证据、以及写作参考检索）。写进去的表现是
"导入成功但校对毫无变化" —— 这正是历史上一个静默无效的通路。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import logs as _logs
from ..db import connection as dbconn
from ..db import dao
from . import corrector
from .wordfmt import ROLE_LABELS, Entry, ParseResult, parse

# 各角色的默认置信度。
# terms 刻意落在 0.80（橙「疑似」档，区间 0.55–0.85）而不是红档：
# 行业术语的"异名"经常是合法简称，不该进「一键修正」被自动替换。
DEFAULT_CONFIDENCE = {"pairs": 0.99, "terms": 0.80, "protect": 0.0}
# 各角色默认的 category（同时是界面上"这一类规则"的标签）
DEFAULT_CATEGORY = {"pairs": "用户导入", "terms": "术语", "protect": ""}
# 忽略名单里记录来源的 note 前缀，供按来源回退时精确删除
# 公开面。`parse` 是**刻意的再导出** —— 调用方（UI/脚本）只需要 import
# `wordlist` 一处就能完成"解析→预检→写入"，不必自己去找 wordfmt。
__all__ = ["parse", "precheck", "preview_lines", "apply",
           "delete_source", "supported_hint", "DEFAULT_CONFIDENCE"]

IGNORE_NOTE_PREFIX = "词表导入:"

_log = _logs.get_logger("wordlist")


@dataclass
class Change:
    """一条待处理条目 + 它与库中现状的关系。"""
    kind: str                  # add | overwrite | conflict | skip
    entry: Entry
    why: str = ""
    existing: dict = field(default_factory=dict)


@dataclass
class PrecheckReport:
    fmt: str = ""
    role: str = "pairs"
    source: str = ""
    adds: list = field(default_factory=list)
    overwrites: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    skips: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def counts(self) -> dict:
        return {"新增": len(self.adds), "覆盖": len(self.overwrites),
                "冲突": len(self.conflicts), "跳过": len(self.skips),
                "无效": len(self.issues)}

    @property
    def total_valid(self) -> int:
        return len(self.adds) + len(self.overwrites) + len(self.conflicts)


@dataclass
class ApplyReport:
    ok: bool = True
    error: str = ""
    role: str = ""
    source: str = ""
    words_added: int = 0
    pairs_added: int = 0
    overwritten: int = 0
    skipped: int = 0
    conflicts_kept: int = 0
    ignored: int = 0
    issues: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return "导入失败：%s" % self.error
        bits = []
        if self.pairs_added:
            bits.append("新增规则 %d 条" % self.pairs_added)
        if self.words_added:
            bits.append("新增词条 %d 个" % self.words_added)
        if self.overwritten:
            bits.append("覆盖 %d 条" % self.overwritten)
        if self.ignored:
            bits.append("加入忽略名单 %d 个" % self.ignored)
        if self.conflicts_kept:
            bits.append("保留原有写法 %d 处（未覆盖）" % self.conflicts_kept)
        if self.skipped:
            bits.append("跳过 %d 条" % self.skipped)
        return "；".join(bits) or "没有需要写入的条目"


# ------------------------------------------------------------------ 预检
def precheck(parsed: ParseResult, source: str, role: str = "pairs",
             extra_issues=None) -> PrecheckReport:
    """只读比对：算出每一条会"新增/覆盖/冲突/跳过"，**不写库**。"""
    rep = PrecheckReport(fmt=parsed.fmt, role=role, source=source)
    rep.warnings.extend(parsed.warnings)
    rep.issues.extend(parsed.issues)
    if extra_issues:
        rep.issues.extend(extra_issues)

    conn = dbconn.get_conn()
    seen_keys: set = set()
    for e in parsed.entries:
        # 同批次内重复：直接跳过（不是错误，用户表里常有）
        if e.key in seen_keys:
            rep.skips.append(Change("skip", e, "本批次内重复"))
            continue
        seen_keys.add(e.key)
        if e.role == "protect":
            _precheck_word(conn, e, source, rep)
        else:
            _precheck_pair(conn, e, source, rep)
    return rep


def _precheck_word(conn, e: Entry, source: str, rep: PrecheckReport) -> None:
    same = conn.execute(
        "SELECT id FROM dictionary WHERE word=? AND COALESCE(source,'')=?",
        (e.word, source)).fetchone()
    if same:
        rep.skips.append(Change("skip", e, "该来源下已存在"))
        return
    row = conn.execute(
        "SELECT id, COALESCE(source,'') AS source FROM dictionary WHERE word=?",
        (e.word,)).fetchone()
    if row is not None:
        # 别的来源已有该词：并存（dictionary 无唯一索引，且不同来源本就该各自独立）
        rep.adds.append(Change("add", e, "其他来源已存在，将并存"))
        return
    rep.adds.append(Change("add", e, ""))


def _precheck_pair(conn, e: Entry, source: str, rep: PrecheckReport) -> None:
    same = conn.execute(
        "SELECT id, COALESCE(source,'') AS source, confidence FROM error_pairs "
        "WHERE wrong=? AND correct=?", (e.wrong, e.correct)).fetchone()
    if same is not None:
        rep.overwrites.append(Change(
            "overwrite", e,
            "已有同一对（来源 %s）" % (same["source"] or "未标注"),
            dict(same)))
        return
    # 同错词、不同对 → 冲突：默认**两条并存**，绝不静默覆盖既有写法。
    # 覆盖"错→对"是危险动作：用户原有的规则会被悄悄改掉，表现为
    # "昨天还报的错今天不报了"，且无从追溯。
    others = conn.execute(
        "SELECT id, correct, COALESCE(source,'') AS source FROM error_pairs "
        "WHERE wrong=?", (e.wrong,)).fetchall()
    if others:
        names = "、".join(sorted({r["correct"] for r in others}))
        rep.conflicts.append(Change(
            "conflict", e, "「%s」已有写法：%s" % (e.wrong, names),
            {"others": [dict(r) for r in others]}))
        return
    rep.adds.append(Change("add", e, ""))


# ------------------------------------------------------------------ 预览
def preview_lines(rep: PrecheckReport, limit: int = 8) -> list:
    """生成给用户看的纯文本清单（直接喂现有 ask/info 对话框）。"""
    c = rep.counts
    lines = [
        "格式：%s　　角色：%s　　来源：%s"
        % (rep.fmt or "?", ROLE_LABELS.get(rep.role, rep.role), rep.source),
        "预检结果：新增 %(新增)d　覆盖 %(覆盖)d　冲突 %(冲突)d　"
        "跳过 %(跳过)d　无效 %(无效)d" % c,
    ]
    for w in rep.warnings:
        lines.append("· " + w)

    def _sample(title, items):
        if not items:
            return
        lines.append("")
        lines.append("%s（共 %d，列前 %d）：" % (title, len(items), limit))
        for ch in items[:limit]:
            e = ch.entry
            if e.role == "protect":
                lines.append("　· %s　%s" % (e.word, ch.why))
            else:
                lines.append("　· %s → %s　%s" % (e.wrong, e.correct, ch.why))
        if len(items) > limit:
            lines.append("　…… 其余 %d 条略" % (len(items) - limit))

    _sample("将新增", rep.adds)
    _sample("将覆盖（同一「错→对」已存在）", rep.overwrites)
    _sample("冲突（同错词已有别的写法，默认并存不覆盖）", rep.conflicts)
    _sample("跳过", rep.skips)
    if rep.issues:
        lines.append("")
        lines.append("无效行（共 %d，列前 %d）：" % (len(rep.issues), limit))
        for line_no, why in rep.issues[:limit]:
            lines.append("　· 第 %s 行：%s" % (line_no, why))
        if len(rep.issues) > limit:
            lines.append("　…… 其余 %d 行略" % (len(rep.issues) - limit))
    return lines


# ------------------------------------------------------------------ 写入
def apply(parsed: ParseResult, rep: PrecheckReport, source: str,
          role: str = "pairs", *, accept_conflicts: bool = False,
          label: str = "") -> ApplyReport:
    """按预检结果写库。**整批单事务**：任一行失败 → 全部回滚。

    ``accept_conflicts=False``（默认）：冲突条目**不写入**（保持库里原样），
    只在报告里计数；置 True 表示"以本次为准"，把原有的同错词条目停用后写入新对。

    ⚠️ 这里直接写 SQL 而**不复用 dao 的 add_* 系列**：那些函数各自
    `conn.commit()`，一旦中途提交，前面的行就落盘了 —— 整批回滚随即失效。
    事务的原子性必须集中在一处才可审计。
    """
    out = ApplyReport(role=role, source=source, issues=list(rep.issues))
    conf_default = DEFAULT_CONFIDENCE.get(role, 0.99)
    category_default = DEFAULT_CATEGORY.get(role, "用户导入")
    conn = dbconn.get_conn()
    try:
        for ch in rep.adds:
            _write_one(conn, ch.entry, source, role, conf_default,
                       category_default, out, accept_conflicts)
        for ch in rep.overwrites:
            _write_one(conn, ch.entry, source, role, conf_default,
                       category_default, out, accept_conflicts)
        if accept_conflicts:
            for ch in rep.conflicts:
                _write_one(conn, ch.entry, source, role, conf_default,
                           category_default, out, accept_conflicts)
        else:
            out.conflicts_kept = len(rep.conflicts)
        out.skipped = len(rep.skips)
        # 来源登记：让 repeat_rules 能把"用户词"与"汉语证据集"分开取用
        if out.words_added or out.pairs_added or out.overwritten:
            conn.execute(
                "INSERT INTO wordlist_sources(source,role,label,fmt,"
                "entry_count,imported_at,enabled) VALUES(?,?,?,?,?,?,1) "
                "ON CONFLICT(source) DO UPDATE SET role=excluded.role, "
                "label=excluded.label, fmt=excluded.fmt, "
                "entry_count=excluded.entry_count, imported_at=excluded.imported_at",
                (source, role, label or source, parsed.fmt,
                 out.words_added + out.pairs_added + out.overwritten,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception as rb_exc:
            # 同 ruleset：回滚失败不能让原始错误被掩盖，但库可能停在半途，
            # 这条日志是事后判断"要不要从备份恢复"的唯一线索。
            _log.warning("词表写入失败后回滚也失败（%s），库可能处于中间状态",
                         rb_exc)
        out.ok = False
        out.error = "%s: %s" % (type(exc).__name__, exc)
        out.words_added = out.pairs_added = out.overwritten = 0
        out.ignored = 0
        return out
    # 写入成功后失效纠错缓存（否则新规则本次会话内不生效）
    corrector.invalidate_cache()
    return out


def _write_one(conn, e: Entry, source: str, role: str, conf_default: float,
               category_default: str, out: ApplyReport,
               accept_conflicts: bool) -> None:
    if e.role == "protect":
        conn.execute(
            "INSERT INTO dictionary(word,pinyin,definition,example,source) "
            "VALUES(?,?,?,?,?)",
            (e.word, e.pinyin, e.definition, "", source))
        conn.execute(
            "INSERT OR REPLACE INTO ignore_words(word,note) VALUES(?,?)",
            (e.word, IGNORE_NOTE_PREFIX + source))
        out.words_added += 1
        out.ignored += 1
        return

    if accept_conflicts:
        # "以本次为准"：把同错词的既有条目停用，避免两条规则打架
        conn.execute(
            "UPDATE error_pairs SET enabled=0 WHERE wrong=? AND correct<>?",
            (e.wrong, e.correct))
    conf = e.confidence if e.confidence else (
        DEFAULT_CONFIDENCE["terms"] if e.role == "terms" else conf_default)
    conn.execute(
        "INSERT OR REPLACE INTO error_pairs(wrong,correct,category,confidence,"
        "enabled,source) VALUES(?,?,?,?,1,?)",
        (e.wrong, e.correct, e.category or category_default, conf, source))
    if e.role == "terms":
        pass       # 术语与纠错对同表，仅 category/置信度不同
    out.pairs_added += 1


# ------------------------------------------------------------------ 便捷入口
def delete_source(source: str) -> dict:
    """整体移除一个导入来源（词条 + 纠错对 + 忽略名单 + 登记）。"""
    res = dao.delete_wordlist_source(source)
    conn = dbconn.get_conn()
    n_ignore = conn.execute(
        "DELETE FROM ignore_words WHERE note=?",
        (IGNORE_NOTE_PREFIX + source,)).rowcount or 0
    conn.commit()
    res["ignore_words"] = int(n_ignore)
    corrector.invalidate_cache()
    return res


def supported_hint() -> str:
    """给 UI 用的文件过滤器与提示文案（单一来源，避免两处各写一份）。"""
    from .wordfmt import SUPPORTED_EXTS
    return ";;".join([
        "词表文件 (%s)" % " ".join("*" + x for x in SUPPORTED_EXTS),
        "所有文件 (*.*)",
    ])


def source_label(source: str) -> str:
    return Path(source).name if source else source
