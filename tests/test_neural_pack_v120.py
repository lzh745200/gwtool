# -*- coding: utf-8 -*-
"""P2（v1.20）：ONNX 增强包的词表对齐 / 偏移 / 门控回归。

这些用例全部用假会话，不依赖 474MB 的真实模型；真实包的端到端验收见
`scripts/eval_neural_pack.py`（E9）。
"""
from __future__ import annotations

from types import SimpleNamespace

from gwtool.core import csc_neural


def _engine(vocab):
    return SimpleNamespace(vocab=vocab)


def _rows(text, vocab, target=None, det_p1=0.9):
    """构造与模型同形的 det/cor：target 指定 (下标, 新字)；其余位置保持原字。"""
    ids = [vocab.index(ch) for ch in text]
    det = [[1.0 - det_p1, det_p1] for _ in ids]
    cor = []
    for cid in ids:
        row = [0.0] * len(vocab)
        row[cid] = 5.0
        cor.append(row)
    if target is not None:
        i, ch = target
        det[i] = [1.0 - det_p1, det_p1]
        row = [0.0] * len(vocab)
        row[vocab.index(ch)] = 5.0
        cor[i] = row
    return ids, det, cor


def test_parse_vocab_keeps_unicode_line_separator_inside_token():
    # U+2028 是 token 的一部分，不能被 splitlines 切开
    text = "[PAD]\na\u2028b\nc\n"
    vocab = csc_neural._parse_vocab(text)
    assert vocab == ["[PAD]", "a\u2028b", "c"]


def test_parse_vocab_keeps_blank_line_placeholder():
    vocab = csc_neural._parse_vocab("a\n\nb\n")
    assert vocab == ["a", "\u0000", "b"]


def test_decode_no_edit_on_clean_text():
    vocab = ["[PAD]"] + list("天地气真不好田米")
    text = "天气真不好"
    ids, det, cor = _rows(text, vocab)
    eng = _engine(vocab)
    assert csc_neural._decode_window(eng, ids, list(text), det, cor) == []


def test_decode_single_char_correction():
    vocab = ["[PAD]"] + list("天地气真不好田米")
    text = "天气真不好"
    ids, det, cor = _rows(text, vocab, target=(0, "田"))
    eng = _engine(vocab)
    out = csc_neural._decode_window(eng, ids, list(text), det, cor)
    assert out and out[0][0] == 0 and out[0][1] == "田"


def test_decode_respects_output_offset_for_cls_sep():
    """输出 L+2（图内补 CLS/SEP）时，第 i+1 行才对应输入第 i 字。"""
    vocab = ["[PAD]"] + list("天地气真不好田米")
    text = "天气真不好"
    ids, _det, cor = _rows(text, vocab)
    # 伪造 2 个特殊 token：首行/末行占位，中间是"错位"的预测：
    # 第 i 行故意给前一个字的字，如果代码不偏移就会误改。
    shifted = [[0.0] * len(vocab)] + cor + [[0.0] * len(vocab)]
    det = [[0.0, 0.0]] + [[0.0, 0.9] for _ in ids] + [[0.0, 0.0]]
    eng = _engine(vocab)
    out = csc_neural._decode_window(eng, ids, list(text), det, shifted)
    assert out == []          # 偏移正确 → 预测=原文 → 零改动


def test_gate_rejects_low_detection_probability():
    vocab = ["[PAD]"] + list("天地气真不好田米")
    text = "天气真不好"
    ids, det, cor = _rows(text, vocab, target=(0, "田"), det_p1=0.30)
    eng = _engine(vocab)
    assert csc_neural._decode_window(eng, ids, list(text), det, cor) == []


def test_gate_rejects_low_margin_correction():
    vocab = ["[PAD]"] + list("天地气真不好田")
    text = "天气真不好"
    ids, det, cor = _rows(text, vocab, target=(0, "田"))
    # 把田的 logit 降到与 top2 只差 0.1 → 模型不确定，拒改
    cor[0][vocab.index("田")] = 5.0
    cor[0][vocab.index("天")] = 4.9
    eng = _engine(vocab)
    assert csc_neural._decode_window(eng, ids, list(text), det, cor) == []


def test_gate_skips_non_cjk_positions():
    vocab = ["[PAD]", " ", "（", "天", "气"]
    text = "天 气"
    ids = [vocab.index(ch) for ch in text]
    det = [[0.0, 0.99] for _ in ids]
    cor = []
    for cid in ids:
        row = [0.0] * len(vocab)
        row[cid] = 5.0
        cor.append(row)
    # 空格位置想改成"（"，必须被跳过
    cor[1] = [0.0] * len(vocab)
    cor[1][vocab.index("（")] = 5.0
    eng = _engine(vocab)
    out = csc_neural._decode_window(eng, ids, list(text), det, cor)
    assert all(ch != "（" for _i, ch, _c in out)
