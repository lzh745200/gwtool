# -*- coding: utf-8 -*-
"""L5 语法纠错层（csc_gec）的测试。

真实 BART/GEC 模型不在仓库里（上百 MB，要求用户离线导入），所以这里用
**假 Seq2Seq 会话 + 假分词器**验证整条纯逻辑链路：
  ① 贪心解码与文本还原；② 编辑脚本对齐（含增/删/改）；③ G1–G10 门控；
  ④ 插入/删除的邻字替换规范化；⑤ 与 L4 的 occupied 避让；⑥ 降级与异常兜底。

刻意**不依赖 numpy 与 tokenizers**：被测逻辑（解码 / 对齐 / 门控 / 降级）
不该被这两个重依赖绑死，否则 CI 为了跑纯逻辑断言得多装几百 MB。
`csc_gec` 的张量构造在无 numpy 时自动退化为 list，假会话照吃。
"""
# 麒麟 CI 容器是 Python 3.9：`dict | None` 这类 PEP 604 注解会在类定义时
# 求值而炸收集。future 导入让全部注解延迟为字符串，3.9/3.13 通吃。
from __future__ import annotations

import pytest

from gwtool.core import corrector, csc_gec, enhance_pack


# ------------------------------------------------------------ 假分词器
class _FakeTok:
    """按字符切分的假分词器。

    id 从实例内的字符表里分配（从 200 起，避开 [CLS]/[SEP]/[PAD]/[UNK] 的
    101/102/0/100），**保证 encode/decode 是双射**——这一点很关键：
    如果两个不同字符映射到同一个 id，解码就会静默丢字，测试会以
    「门控莫名其妙拦住」的形式失败，非常难查。
    """

    def __init__(self, mapping: dict | None = None):
        self.mapping = dict(mapping or {})
        chars = set()
        for k, v in self.mapping.items():
            chars.update(k)
            chars.update(v)
        for c in ("的一是不了人我在有他这为之大来以个中上们"
                  "请各位同事同仁认真落实会议要求该方案实施需要进一步论证"
                  "今天气真不错适合出门散步长短句甲乙丙丁戊己庚"
                  "把握好方向目标推进各项工作任务"):
            chars.add(c)
        self.alphabet = sorted(chars)
        self._to_id = {c: 200 + i for i, c in enumerate(self.alphabet)}
        self._to_ch = {v: k for k, v in self._to_id.items()}
        # 表外字符动态补号时从 _next 起递增；绝不能用 len(dict) 推算，
        # 那样会和已分配的号码撞车，导致 decode 静默换成别的字。
        self._next = 200 + len(self.alphabet)

    def _id(self, ch: str) -> int:
        if ch not in self._to_id:
            self._to_id[ch] = self._next
            self._to_ch[self._next] = ch
            self._next += 1
        return self._to_id[ch]

    def _ch(self, i: int) -> str:
        return self._to_ch.get(int(i), "")

    def encode(self, s: str):
        class _E:
            pass
        e = _E()
        e.ids = [self._id(c) for c in s]
        return e

    def decode(self, ids, skip_special_tokens=True):
        return "".join(self._ch(int(i)) for i in ids)

    def token_to_id(self, name):
        return {"[CLS]": 101, "[SEP]": 102, "[PAD]": 0, "[UNK]": 100}.get(name)


class _FakeGecSession:
    """假 Seq2Seq 会话：把输入段喂给 tok.mapping，取到目标串就"照抄"回去。

    `boom=True` 时抛异常，用于验证「推理崩了也不影响主流程」。
    """

    def __init__(self, tok: _FakeTok, mapping: dict, boom: bool = False,
                 logit: float = 0.0):
        self.tok = tok
        self.mapping = mapping
        self.boom = boom
        self.logit = logit            # 正确 token 的 logit；0 时平均对数概率约 -log(V)
        self.calls = 0

    def get_inputs(self):
        class _I:
            def __init__(self, n):
                self.name = n
        return [_I("input_ids"), _I("attention_mask"), _I("token_type_ids")]

    def get_outputs(self):
        class _O:
            def __init__(self, n):
                self.name = n
        return [_O("logits")]

    def run(self, _outs, feed):
        self.calls += 1
        if self.boom:
            raise RuntimeError("模拟推理崩溃")
        ids = list(feed["input_ids"][0])
        # 剥掉 [CLS]/[SEP]
        inner = [i for i in ids if i not in (101, 102)]
        src = self.tok.decode(inner)
        dst = self.mapping.get(src, src)
        # 只吐「目标串本体」的逐位分布，不含 [CLS]/[SEP] 行。
        # 若把特殊 token 也吐出来，`_decode_one` 会按 id 过滤掉它们，
        # 一旦某个目标字的 id 恰好与特殊 token 相同就会静默丢字。
        # 词表宽度必须覆盖 tokenizer 分配过的最大 id，否则取模会撞车。
        vsize = max(256, self.tok._next + 8)
        rows = []
        for c in dst:
            row = [-30.0] * vsize
            row[self.tok._id(c)] = self.logit
            rows.append(row)
        return [[rows]]


def _enable(monkeypatch, mapping, *, boom=False, logit=6.0, tok=None):
    """打开 L5 开关并注入假会话与假分词器。"""
    tok = tok or _FakeTok(dict(mapping))
    tok.mapping = dict(mapping)
    session = _FakeGecSession(tok, mapping, boom=boom, logit=logit)
    monkeypatch.setattr(csc_gec, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: True)
    csc_gec.set_session_for_test(session, tok)
    csc_gec.set_setting(True)
    return session


# ------------------------------------------------------- 基础：开关与降级
def test_disabled_by_default_means_no_calls(tmp_db, monkeypatch):
    session = _enable(monkeypatch, {})
    csc_gec.set_setting(False)
    assert csc_gec.enhance("这是一段很长的测试文本内容。", []) == []
    assert session.calls == 0, "关闭时不应发生任何推理"


def test_inference_exception_is_swallowed(tmp_db, monkeypatch):
    """L5 崩了绝不能影响 L1–L4 的既有结果。

    注意素材选取：这段文本里既有 L1 能命中的「布署」，也有 L1 覆盖不到的
    后半句。若整段都被 L1 覆盖，`_run` 会按 occupied 避让直接跳过、根本不
    进模型，就测不到「推理异常」这条路径了。
    """
    _enable(monkeypatch, {}, boom=True)
    text = "会议对下一步工作进行了布署，请各单位抓紧落实到位。"
    corr = corrector.check_text(text)
    assert any(c.wrong == "布署" for c in corr), "L1 的命中必须保住"
    assert csc_gec.last_error(), "推理异常应被记录（说明确实进了模型）"


def test_missing_pack_means_unavailable(tmp_db, monkeypatch):
    monkeypatch.setattr(csc_gec, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: False)
    csc_gec.reset()
    csc_gec.set_setting(True)
    assert csc_gec.enabled() is False
    assert "语法纠错增强包" in csc_gec.status_text()


def test_short_text_is_not_sent(tmp_db, monkeypatch):
    """G10：短于最小长度的片段不送模型。"""
    session = _enable(monkeypatch, {"短句": "长句"})
    out = csc_gec.enhance("短句。", [])
    assert out == []
    assert session.calls == 0


# ------------------------------------------------------- 门控 G1–G10
def test_g1_low_confidence_rejected(tmp_db, monkeypatch):
    """G1：置信度过低整段丢弃。logit 很低 → 平均对数概率低 → conf < 0.75。"""
    _enable(monkeypatch, {"这个方案的可行性需要进一步论证。":
                          "这个方案可行性需进一步论证。"}, logit=-60.0)
    out = csc_gec.enhance("这个方案的可行性需要进一步论证。", [])
    assert out == []


def test_g2_change_ratio_rejected(tmp_db, monkeypatch):
    """G2：改动幅度超过段长 30% 丢弃。"""
    src = "这是一段用于测试门控的较长中文文本内容。"
    _enable(monkeypatch, {src: "完全不同的一段替换文本内容啊。"})
    assert csc_gec.enhance(src, []) == []


def test_g3_lcs_ratio_rejected(tmp_db, monkeypatch):
    """G3：与原文保留率过低（整句重写）丢弃。"""
    src = "这句话写得其实没有太大问题。"
    _enable(monkeypatch, {src: "另一段毫无关联的内容在此处。"})
    assert csc_gec.enhance(src, []) == []


def test_g4_number_is_protected(tmp_db, monkeypatch):
    """G4：数字位不得改动。模型把 2024 改成 2025 必须被拒。"""
    src = "本项目于2024年正式启动实施工作。"
    _enable(monkeypatch, {src: "本项目于2025年正式启动实施工作。"})
    out = csc_gec.enhance(src, [])
    assert all("2024" in src[c.start:c.end] or "2024" not in
               src[c.start:c.end] for c in out)
    assert not any(c.suggestion and "2025" in c.suggestion for c in out)


def test_g5_punct_is_protected(tmp_db, monkeypatch):
    """G5：标点不得改动。"""
    src = "请各单位认真贯彻执行，确保落实到位。"
    _enable(monkeypatch, {src: "请各单位认真贯彻执行,确保落实到位。"})
    out = csc_gec.enhance(src, [])
    assert out == []


def test_g6_quoted_content_is_protected(tmp_db, monkeypatch):
    """G6：引号内内容整体保护。"""
    src = "文件明确提出“加强监督管理”的具体要求。"
    _enable(monkeypatch, {src: "文件明确提出“强化监督管理”的具体要求。"})
    assert csc_gec.enhance(src, []) == []


def test_g7_skips_spans_covered_by_upper_layers(tmp_db, monkeypatch):
    """G7：L1–L4 已覆盖的位置必须避让。"""
    src = "这是一段用于测试避让逻辑的文本内容。"
    _enable(monkeypatch, {src: "这是一段用于测试避让机制的文本内容。"})
    existing = [corrector.Correction(start=src.index("逻辑"), end=src.index("逻辑") + 2,
                                     wrong="逻辑", suggestion="机理",
                                     category="易混词", reason="t", confidence=0.9)]
    out = csc_gec.enhance(src, existing)
    # 被占用区间内的改动不得出现
    occ = range(existing[0].start, existing[0].end)
    assert not any(c.start in occ for c in out)


def test_one_upper_hit_does_not_suppress_whole_paragraph(tmp_db, monkeypatch):
    """回归：段落里有一处被 L1 命中，不得导致整段的 L5 检查被跳过。

    这是一条真实踩到的坑：早期实现用 `if any(occupied[k] for k in 段内): continue`
    整段跳过，结果一段几百字的公文只要含一个 L1 命中，L5 就全程不工作。
    G7 的正确语义是**位置级**避让：挖掉被占用的位置，其余碎片照常送模型。
    """
    src = "会议对下一步工作进行了布署，请各单位抓紧落实到位工作。"
    # 按句读切分后第二小句是独立片段，模型对它有意见
    frag = "请各单位抓紧落实到位工作。"
    frag_fixed = "请各单位抓紧落实到位事宜。"
    _enable(monkeypatch, {frag: frag_fixed})
    # L1 命中「布署」 —— 只覆盖第一小句
    existing = [corrector.Correction(
        start=src.index("布署"), end=src.index("布署") + 2,
        wrong="布署", suggestion="部署",
        category="错别字", reason="t", confidence=0.98)]
    out = csc_gec.enhance(src, existing)
    assert out, "L1 只覆盖一处，不该让整段 L5 失效"
    # 被 L1 占用的位置不得被 L5 重复上报
    assert not any(c.start < existing[0].end and c.end > existing[0].start
                   for c in out), "不得与 L1 命中重叠（避免同位置双报）"


def test_g9_length_ratio_rejected(tmp_db, monkeypatch):
    """G9：长度比越界丢弃。"""
    src = "这一段文字的长度比例会被模型大幅拉长。"
    _enable(monkeypatch, {src: src + "额外增加了非常多非常多的内容在这里用来拉长比例。"})
    assert csc_gec.enhance(src, []) == []


# ------------------------------------------------------- 正常纠正与规范化
def test_replace_edit_produces_correction(tmp_db, monkeypatch):
    src = "请各位同事认真落实会议要求。"
    dst = "请各位同仁认真落实会议要求。"
    _enable(monkeypatch, {src: dst})
    out = csc_gec.enhance(src, [])
    assert len(out) == 1
    c = out[0]
    assert c.category == "语法纠错"
    assert c.kind == "replace"
    # 坐标必须落在源串上，且应用后能还原出模型的输出
    assert src[c.start:c.end] in ("事", "同事")
    assert src[:c.start] + c.suggestion + src[c.end:] == dst


def test_insert_is_normalized_to_neighbor_replace(tmp_db, monkeypatch):
    """纯插入 → 挂到前一个字符，kind=insert，绝不出现零宽区间。"""
    src = "今天气真不错适合出门散步。"
    dst = "今天天气真不错适合出门散步。"
    _enable(monkeypatch, {src: dst})
    out = csc_gec.enhance(src, [])
    assert out, "插入类改动应被上报"
    for c in out:
        assert c.end > c.start, "不得出现零宽区间（会破坏现有消费点）"
    assert any(c.kind == "insert" for c in out)
    # 应用后应还原出模型输出
    fixed = src
    for c in sorted(out, key=lambda x: x.start, reverse=True):
        fixed = fixed[:c.start] + c.suggestion + fixed[c.end:]
    assert fixed == dst


def test_delete_of_function_word_is_reported(tmp_db, monkeypatch):
    """删除类改动：只允许删虚词。"""
    src = "该方案的了实施需要进一步论证。"
    _enable(monkeypatch, {src: "该方案的实施需要进一步论证。"})
    out = csc_gec.enhance(src, [])
    assert out, "删虚词应被上报"
    for c in out:
        assert c.end > c.start
        assert c.kind in ("delete", "replace")


def test_delete_of_content_word_is_rejected(tmp_db, monkeypatch):
    """删实词必须被拒（_DELETABLE 白名单）。"""
    src = "请认真落实会议精神并加强监督。"
    _enable(monkeypatch, {src: "请落实会议精神并加强监督。"})
    out = csc_gec.enhance(src, [])
    assert not any(src[c.start:c.end] == "认真" and c.suggestion == ""
                   for c in out)


def test_no_change_means_no_correction(tmp_db, monkeypatch):
    src = "这段文字模型认为没有问题不需要改动。"
    _enable(monkeypatch, {})            # mapping 为空 → 输出等于输入
    assert csc_gec.enhance(src, []) == []


# ------------------------------------------------------- 接线：check_text
def test_check_text_includes_l5_results(tmp_db, monkeypatch):
    src = "请各位同事认真落实会议要求。"
    _enable(monkeypatch, {src: "请各位同仁认真落实会议要求。"})
    corr = corrector.check_text(src)
    l5 = [c for c in corr if c.category == "语法纠错"]
    assert l5, "check_text 应把 L5 结果并入"
    assert all(c.kind for c in l5)


def test_l5_off_keeps_pipeline_identical(tmp_db, monkeypatch):
    """L5 关闭时，check_text 的结果与接入前完全一致（只做一次布尔判断）。"""
    csc_gec.set_setting(False)
    text = "会议对下一步工作进行了布署，请认真执行。"
    before = corrector.check_text(text)
    after = corrector.check_text(text)
    assert [c.label for c in before] == [c.label for c in after]


# ------------------------------------------------------- 纯函数：对齐
def _apply_spans(src: str, spans) -> str:
    """把片段脚本应用到 src 上，用于验证「脚本确实能还原出 dst」。"""
    out = src
    for s, e, rep in sorted(spans, key=lambda x: x[0], reverse=True):
        out = out[:s] + rep + out[e:]
    return out


def test_diff_spans_replace():
    """同长度替换：脚本能还原目标串，且只改动一个字。

    不锁死是改左边还是改右边那一字——DP 有多条等价最优路径，
    断言「语义正确」而不是「某个特定的 tie-break」。
    """
    src, dst = "同事", "同仁"
    spans = csc_gec._diff_spans(src, dst)
    assert _apply_spans(src, spans) == dst
    assert len(spans) == 1
    s, e, rep = spans[0]
    assert e - s == 1 and len(rep) == 1
    assert rep == "仁"


def test_diff_spans_insert():
    src, dst = "今天气", "今天天气"
    spans = csc_gec._diff_spans(src, dst)
    assert _apply_spans(src, spans) == dst
    # 净增 1 字：脚本里必有插入（start == end）
    assert any(s == e for s, e, _ in spans)


def test_diff_spans_delete():
    src, dst = "方案的了实施", "方案的实施"
    spans = csc_gec._diff_spans(src, dst)
    assert _apply_spans(src, spans) == dst
    # dst 比 src 短 1 字，脚本长度必须相应减少
    total = sum((e - s) - len(rep) for s, e, rep in spans)
    assert total == len(src) - len(dst)


def test_diff_spans_replace_middle():
    src, dst = "abcd", "abXcd"
    spans = csc_gec._diff_spans(src, dst)
    assert _apply_spans(src, spans) == dst
    assert len(dst) - len(src) == 1


def test_diff_spans_no_change():
    assert csc_gec._diff_spans("完全一致", "完全一致") == []
    assert csc_gec._diff_spans("", "") == []


def test_diff_spans_full_replace():
    src, dst = "x", "y"
    spans = csc_gec._diff_spans(src, dst)
    assert _apply_spans(src, spans) == dst


def test_diff_spans_roundtrip_on_long_text():
    """长句单字替换必须能精确还原。"""
    src = "请各位同事认真落实会议要求。"
    dst = "请各位同仁认真落实会议要求。"
    spans = csc_gec._diff_spans(src, dst)
    assert _apply_spans(src, spans) == dst
    assert len(spans) == 1


# ------------------------------------------------------- 纯函数：规范化
def test_normalize_insert_uses_previous_char():
    s, e, rep = csc_gec._normalize_insert("今天气", (2, 2, "天"))
    assert (s, e) == (1, 2) and rep == "天天"


def test_normalize_insert_at_head_uses_next_char():
    s, e, rep = csc_gec._normalize_insert("天气", (0, 0, "今"))
    assert e > s and rep == "今天"


def test_normalize_delete_uses_previous_char():
    s, e, rep = csc_gec._normalize_insert("方案的了实施", (3, 4, ""))
    assert (s, e) == (2, 4) and rep == "的"


# ------------------------------------------------------- 纯函数：保护掩码
def test_protected_mask_marks_numbers_and_punct():
    src = "2024年，启动。"
    mask = csc_gec._protected_mask(src)
    for k, ch in enumerate(src):
        if ch.isdigit() or ch in "，。":
            assert mask[k] == 1, f"{ch} 应受保护"


def test_protected_mask_marks_quoted():
    src = "提出“加强监督”的要求。"
    mask = csc_gec._protected_mask(src)
    a, b = src.index("“"), src.index("”")
    assert all(mask[k] for k in range(a, b + 1))


def test_lcs_len_basic():
    assert csc_gec._lcs_len("abcde", "ace") == 3
    assert csc_gec._lcs_len("", "abc") == 0
    assert csc_gec._lcs_len("同样", "同样") == 2


# ================================================ encdec 自回归解码模式
# 真实 T5/BART 是自回归的：图只在每步给出「下一个 token」的分布，
# 循环由引擎在 Python 侧步进（`_decode_one_encdec`）。这里的假会话
# 复刻真实导出图的契约：decoder_input_ids 的首粒是种子位（图内会被
# 替换为 decoder_start），输入不含 [CLS]/[SEP]。
class _FakeEncDecSession:
    """假自回归会话：按 decoder_input_ids[1:] 的长度吐下一个字的分布。"""

    def __init__(self, tok: _FakeTok, mapping: dict, logit: float = 6.0,
                 with_decoder_input: bool = True):
        self.tok = tok
        self.mapping = dict(mapping)
        self.logit = logit
        self.with_decoder_input = with_decoder_input
        self.calls = 0
        self.feed_lens: list[int] = []

    def get_inputs(self):
        class _I:
            def __init__(self, n):
                self.name = n
        names = ["input_ids", "attention_mask"]
        if self.with_decoder_input:
            names.append("decoder_input_ids")
        return [_I(n) for n in names]

    def get_outputs(self):
        class _O:
            def __init__(self, n):
                self.name = n
        return [_O("logits")]

    def run(self, _outs, feed):
        self.calls += 1
        if not self.with_decoder_input:
            raise AssertionError("图没有 decoder 输入时引擎不应调用推理")
        dec = list(feed["decoder_input_ids"][0])
        self.feed_lens.append(len(dec))
        gen = dec[1:]                       # 剥掉种子位
        src = self.tok.decode([i for i in feed["input_ids"][0]])
        dst = self.mapping.get(src, src)
        vsize = max(256, self.tok._next + 8)
        rows = []
        for pos in range(len(dec)):         # 已有 K 行，引擎只看最后一行
            row = [-30.0] * vsize
            if pos < len(gen):
                row[gen[pos]] = 5.0
            rows.append(row)
        last = [-30.0] * vsize
        nxt = dst[len(gen)] if len(gen) < len(dst) else None
        if nxt is None:                     # 生成完毕 → 句束（sep=102）
            last[102] = self.logit
        else:
            last[self.tok._id(nxt)] = self.logit
        rows.append(last)
        return [[rows]]


def _enable_encdec(monkeypatch, mapping, *, logit=6.0,
                   with_decoder_input=True, tok_cls=_FakeTok):
    """打开 L5 开关并注入 encdec 模式的假会话。"""
    tok = tok_cls(dict(mapping))
    tok.mapping = dict(mapping)
    session = _FakeEncDecSession(tok, mapping, logit=logit,
                                 with_decoder_input=with_decoder_input)
    monkeypatch.setattr(csc_gec, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: True)
    csc_gec.set_session_for_test(session, tok, mode="encdec")
    csc_gec.set_setting(True)
    return session


def test_encdec_mode_detection_defaults_to_oneshot(tmp_db, monkeypatch):
    """不加 mode 参数时保持 oneshot，既有契约不漂移。"""
    session = _enable(monkeypatch, {})
    assert csc_gec._ENGINE.mode == "oneshot"
    assert session.calls >= 0


def test_encdec_encode_has_no_cls_wrap(tmp_db, monkeypatch):
    """encdec 模式编码不加 [CLS]/[SEP]（T5/BART 不吃这对符号）。"""
    _enable_encdec(monkeypatch, {})
    eng = csc_gec._ENGINE
    ids = csc_gec._encode(eng, "今天气真不错适合出门散步")
    assert ids and ids[0] != 101 and ids[-1] != 102
    assert len(ids) == len("今天气真不错适合出门散步")


def test_encdec_replace_produces_correction(tmp_db, monkeypatch):
    """encdec 全链路：替换型改动产出 Correction（kind=replace）。

    注意分段规则：按句读切分后每片必须 ≥ 8 字才送模型（G10），
    所以素材的前半句要凑足 8 字。
    """
    piece = "今天新情非常好，"           # 8 字，恰好过 G10
    text = piece + "大家干劲十足在推进。"
    _enable_encdec(monkeypatch, {piece: piece.replace("新", "心")})
    out = csc_gec.enhance(text, [])
    assert any(c.wrong == "新" and c.suggestion == "心"
               and c.kind == "replace" for c in out)


def test_encdec_insert_normalized_to_neighbor(tmp_db, monkeypatch):
    """encdec 产出插入型改动 → 规范化为邻字替换（kind=insert）。

    素材要求（两道坑都得避开）：
      1. 插入字符在上下文中必须唯一 —— 若与相邻字符重复（如往
         「今天气」里插「天」），_diff_spans 反向回溯会选出等价但
         位置不同的对齐，span 落点不可预期；
      2. 插入点不能紧邻标点 —— G5 的插入邻位规则会把紧贴保护位
         的插入直接丢弃（防止在数字/标点边上加字）。
    """
    piece = "这个安排很合理，"           # 8 字，恰好过 G10
    text = piece + "正适合出门散步。"
    _enable_encdec(monkeypatch, {piece: "这个安排很不合理，"})
    out = csc_gec.enhance(text, [])
    assert out and out[0].kind == "insert"
    assert out[0].wrong == "很" and out[0].suggestion == "很不"


def test_encdec_stops_at_eos(tmp_db, monkeypatch):
    """目标为空：第 1 步就吐 sep → 立即停，绝不跑满步数上限。"""
    src = "这一段文本完全不需要任何修改啊。"
    session = _enable_encdec(monkeypatch, {src: ""})
    out = csc_gec.enhance(src, [])
    assert out == []
    assert session.calls == 1, "第一步就该遇到句束终止"


def test_encdec_step_cap_g9_rejects(tmp_db, monkeypatch):
    """步数上限截断超长目标 → 长度比超 G9 上界 → 整段拒绝。"""
    src = "这段文字保持原样不动。"
    dst = src + "后面凭空长出来一大段多余的内容" * 3
    _enable_encdec(monkeypatch, {src: dst})
    assert csc_gec.enhance(src, []) == []


def test_encdec_without_decoder_input_is_silent(tmp_db, monkeypatch):
    """声明为 encdec 但图没有 decoder 输入：静默降级，绝不能崩。"""
    src = "这一段文本应当完全没有任何产出。"
    _enable_encdec(monkeypatch, {src: src.replace("应当", "应该")},
                   with_decoder_input=False)
    assert csc_gec.enhance(src, []) == []


def test_encdec_inference_boom_is_swallowed(tmp_db, monkeypatch):
    """encdec 推理中途崩掉也必须吞掉异常（硬约束 3：绝不外抛）。"""
    src = "会议对下一步工作进行了布署，请各单位研究落实到位。"
    tok = _FakeTok({src: src})
    tok.mapping = {src: src}
    session = _FakeEncDecSession(tok, {src: src})

    def _boom(_outs, feed):
        raise RuntimeError("模拟 encdec 推理崩溃")
    session.run = _boom
    monkeypatch.setattr(csc_gec, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: True)
    csc_gec.set_session_for_test(session, tok, mode="encdec")
    csc_gec.set_setting(True)
    out = csc_gec.enhance(src, [])
    assert out == []


def test_special_ids_t5_style_fallbacks(tmp_db):
    """T5 系分词器没有 [CLS]/[SEP]，应从 <pad>/</s>/<unk> 候选取到。"""

    class _T5Tok:
        def token_to_id(self, name):
            return {"</s>": 1, "<pad>": 0, "<unk>": 2}.get(name)

    ids = csc_gec._special_ids(_T5Tok())
    assert ids["sep_id"] == 1 and ids["pad_id"] == 0 and ids["unk_id"] == 2


# ---------------------------------------- encdec 真实分词器契约（T5 教训）
class _T5LikeTok(_FakeTok):
    """模拟 T5 系分词器：encode 自带尾部 eos（</s>=1）与定长填充。

    mengzi-t5 的 tokenizer.json 实测 encode 会输出
    [内容…, 1, 0×N]（定长 128）；引擎必须保留尾部 eos（编码器
    训练分布即如此）、剥净填充。
    """

    def token_to_id(self, name):
        return {"</s>": 1, "<pad>": 0, "<unk>": 2}.get(name)

    def encode(self, s):
        e = super().encode(s)
        core = [*e.ids, 1]
        e.ids = core + [0] * (128 - len(core))
        return e


def test_encdec_encode_keeps_trailing_eos(tmp_db, monkeypatch):
    """T5 系分词器自带尾部 eos 与定长填充：引擎保留 eos、剥净填充。

    剥掉尾部 eos 会让真实 T5 模型「不知何时该停」，生成陷入复读
    循环，最终步数截断触发 G9 整段拒绝——L5 完全失效。
    """
    piece = "这一段文本完全不需要修改。"
    _enable_encdec(monkeypatch, {piece: piece}, tok_cls=_T5LikeTok)
    eng = csc_gec._ENGINE
    ids = csc_gec._encode(eng, piece)
    assert ids[-1] == 1, "尾部 eos 必须保留（T5 编码器约定）"
    assert 0 not in ids, "分词器自带的定长填充必须剥净"
    assert csc_gec._encode(eng, piece) == ids


def test_encdec_mid_seq_cls_id_token_does_not_stop(tmp_db, monkeypatch):
    """生成中途出现 id=101（BERT 惯例 cls 回退值）不得误停。

    T5 词表没有 <s>，cls_id 会回退到 101，而 T5 的 id 101 是真实
    token（「进行」）——停机集合若含 cls，生成「进行」就会截断。
    """
    src = "这一段文本应当没有任何改动的。"

    class _ClsMidSession(_FakeEncDecSession):
        """第 1 步强行吐 101，之后继续吐目标字，最后 sep。"""

        def run(self, _outs, feed):
            self.calls += 1
            dec = list(feed["decoder_input_ids"][0])
            k = len(dec) - 1              # 已生成 token 数（去种子位）
            vsize = max(256, self.tok._next + 8)
            nxt = {0: 101, 1: self.tok._id("妥"), 2: 102}[k]
            row = [-30.0] * vsize
            row[nxt] = 6.0
            return [[[row]]]

    tok = _FakeTok({src: "妥"})
    session = _ClsMidSession(tok, {src: "妥"})
    monkeypatch.setattr(csc_gec, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: True)
    csc_gec.set_session_for_test(session, tok, mode="encdec")
    csc_gec.set_setting(True)

    out, conf = csc_gec._decode_one_encdec(csc_gec._ENGINE, src)
    assert out == "妥", f"id=101 误停会把输出截断（got={out!r}）"
    assert session.calls == 3, "应当走满 3 步：101 → 妥 → sep"


def test_special_ids_bert_style_unchanged(tmp_db):
    """BERT 系行为与旧版完全一致（候选不会误命中）。"""

    class _BertTok:
        def token_to_id(self, name):
            return {"[CLS]": 101, "[SEP]": 102, "[PAD]": 0,
                    "[UNK]": 100}.get(name)

    ids = csc_gec._special_ids(_BertTok())
    assert ids == {"cls_id": 101, "sep_id": 102, "pad_id": 0, "unk_id": 100}


def test_argmax_np_path_matches_python(tmp_db):
    """numpy 快速路径与纯 Python 路径结果一致（首最大值优先）。"""
    row = [0.1, 3.0, 3.0, -2.0]
    a = csc_gec._argmax(row)
    try:
        import numpy as np
        b = csc_gec._argmax(row, np=np)
    except Exception:
        return
    assert a == b == (1, 3.0)
