# -*- coding: utf-8 -*-
"""文内一致性校验在写作提示层的接入契约（v1.8）。

分工
----
``gwtool/core/consistency.py`` 只做「同一实体在全文出现两种写法」的横向比对，
不碰错误判定、不产出替换文本（见该模块 docstring）。它必须**在界面上看得见**
才有价值；本文件锚定它接进 ``writing_hints.hints()`` 之后的行为：

  · 结论以 ``Hint(scope="一致性")`` 出现，level 恒为 info（提示，不是错误）；
  · 单类结论有条数上限，剩余只报一句「另有 N 处」（避免长文刷屏）；
  · ``consistency`` 不可用/抛异常时，写作提示整体仍然可用（只少这一类）；
  · 不产生任何可替换文本 —— 这是「只能提示、不得写回正文」红线的锚点。

为什么单独成文件：``tests/test_consistency.py`` 是纯模块契约（无 UI、无写作层），
``tests/test_writing_hints_ext.py`` 是十项写作检查契约；本条是**跨模块集成**，
放在任一侧都会让那两个文件的职责变浑。
"""
from __future__ import annotations

from gwtool.core import writing_hints as WH


#: 触发「两种写法并存」的一段真实公文风文本：
#: 同一数量用了「三个部门」与「3个部门」两种写法。
INCONSISTENT_TEXT = (
    "为落实有关要求，局党委成立专项工作组，共三个部门参与，另有3个部门配合。"
    "工作组于2026年10月3日印发方案，2026.10.3完成归档。"
)


def _consistency_hints(text: str):
    return [h for h in WH.hints(text) if h.scope == "一致性"]


def test_hints_surface_doc_inconsistency():
    """不一致的写法必须作为「一致性」提示出现。"""
    labels = [h.title for h in _consistency_hints(INCONSISTENT_TEXT)]
    joined = " ".join(labels)
    assert any("不一致" in lab for lab in labels), labels
    assert "三个" in joined or "3个" in joined, labels


def test_consistent_text_produces_no_hint():
    """写法统一时不得出现「一致性」里的文内比对结论（反例）。"""
    text = ("为落实有关要求，局党委成立专项工作组，共3个部门参与。"
            "工作组于2026年10月3日印发方案。")
    assert _consistency_hints(text) == []


def test_hint_level_is_info_only():
    """一致性结论只能是 info：它是「请看一眼」，不是「你写错了」。"""
    for h in _consistency_hints(INCONSISTENT_TEXT):
        assert h.level == "info", h


def test_hint_count_capped_with_summary_line():
    """条数封顶：超限时最后一条是「另有 N 处」，防止长文刷屏。"""
    from gwtool.core import writing_data as W

    # 构造超过上限的独立不一致：每类各若干种写法（数字/日期/单位/机构名）
    parts = []
    for n in (3, 4, 5, 6, 7, 8, 9):
        parts.append(f"共{_cn(n)}个部门，另有{n}个部门配合。")
    text = "".join(parts) + "2026年10月3日印发，2026.10.3归档，2026/10/03复核。"
    hs = _consistency_hints(text)
    assert len(hs) <= W.DOC_INCONSISTENCY_MAX_HINTS + 1, len(hs)
    if len(hs) == W.DOC_INCONSISTENCY_MAX_HINTS + 1:
        assert "另有" in hs[-1].title


def _cn(n: int) -> str:
    return {3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}[n]


def test_consistency_failure_does_not_break_hints(monkeypatch):
    """consistency 抛异常时，写作提示其余类别必须照常输出（异常隔离）。"""
    from gwtool.core import consistency

    def _boom(_text):
        raise RuntimeError("模拟一致性模块故障")

    monkeypatch.setattr(consistency, "check", _boom)
    hs = WH.hints("各单位要高度重视，扎实推进各项工作部署。", "通知")
    assert isinstance(hs, list)
    assert all(h.scope != "一致性" or "我" not in h.title for h in hs)


def test_no_replaceable_text_in_hints():
    """红线：写作提示不产出可替换文本（Hint 没有 suggestion 字段）。"""
    hs = WH.hints(INCONSISTENT_TEXT)
    assert hs
    assert not hasattr(hs[0], "suggestion")
    # 一致性提示只是一句话，不含「错→对」这种替换形态
    for h in _consistency_hints(INCONSISTENT_TEXT):
        assert "→" not in h.title
