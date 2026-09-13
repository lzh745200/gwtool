# -*- coding: utf-8 -*-
"""L4 神经精排层与「精度增强包」的测试。

真实 onnxruntime 与模型文件都不在仓库里（模型上百 MB，且要求用户离线导入），
所以这里用**假推理会话**验证：① 后处理与区间合并；② 与前三层的衔接；
③ 全链路降级与异常兜底；④ 增强包的安全与完整性校验。

刻意**不依赖 numpy**：本模块要测的是「解码 / 合并 / 降级」这条纯逻辑链路，
而 numpy 只是 onnxruntime 的传递依赖（见 requirements-optional.txt）。若测试
硬依赖它，CI 为了跑这些断言就得多装一个 200 MB 的包，与「主包不增长」的
红线背道而驰。假会话直接吐 list-of-list —— `csc_neural` 的解码只按序列消费。
"""
import hashlib
import json
import zipfile

import pytest

from gwtool import paths
from gwtool.core import csc_neural, enhance_pack


# ------------------------------------------------------------ 假推理会话
class _Out:
    def __init__(self, name):
        self.name = name


class _FakeSession:
    """按字符下标标错的假会话：errors = {下标: (替换字, 检测概率)}。

    输出形状与真实 ONNX 一致：`[ [1, L, 2], [1, L, V] ]`（外层是「一个输出
    张量」，第二层是 batch）—— 这样 `_run` 里 `val[0]` 取批次的逻辑也被覆盖。
    """

    output_names = ["detection", "correction"]

    def __init__(self, vocab, errors=None, boom=False):
        self._vocab = vocab
        self._errors = dict(errors or {})
        self._boom = boom
        self.calls = 0

    def get_outputs(self):
        return [_Out("detection"), _Out("correction")]

    def run(self, _outs, feed):
        self.calls += 1
        if self._boom:
            raise RuntimeError("模拟推理崩溃")
        ids = [int(v) for v in feed["input_ids"][0]]
        L, V = len(ids), len(self._vocab)
        det = [[1.0, 0.0] for _ in range(L)]
        cor = [[0.0] * V for _ in range(L)]
        for i in range(L):
            if 0 <= ids[i] < V:
                cor[i][ids[i]] = 1.0
        for idx, (new_ch, prob) in self._errors.items():
            if 0 <= idx < L:
                det[idx] = [0.0, float(prob)]
                cor[idx] = [0.0] * V
                cor[idx][self._vocab.index(new_ch)] = 1.0
        return [[det], [cor]]


# 注意：字符集必须覆盖测试要用到的**全部**替换字，否则假会话在
# `vocab.index()` 处抛 ValueError，会让「占用掩码」这类断言假通过。
_CHARS = "今天气真不好田米地针对部布署工作这是必须解决的问题会议认为"
VOCAB = ["[PAD]"] + [c for c in dict.fromkeys(_CHARS)]


def _info(kind="csc", max_len=64):
    return enhance_pack.PackInfo(name="fake-pack", kind=kind, version="9.9.9",
                                 license="MIT", source="test", backend="onnx",
                                 max_len=max_len)


@pytest.fixture(autouse=True)
def _clean_neural_state():
    csc_neural.reset()
    yield
    csc_neural.reset()


def _enable(monkeypatch, session):
    """让 enabled() 为真，并注入假会话。"""
    monkeypatch.setattr(csc_neural, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: True)
    csc_neural.set_session_for_test(session, VOCAB, _info())
    csc_neural.set_setting(True)


# ------------------------------------------------------------ 后处理逻辑
def test_single_char_correction(tmp_db, monkeypatch):
    session = _FakeSession(VOCAB, {1: ("田", 0.93)})
    _enable(monkeypatch, session)
    from gwtool.core import corrector
    corr = corrector.check_text("今天气真不好。")
    neural = [c for c in corr if c.category == "神经纠错"]
    assert len(neural) == 1
    c = neural[0]
    assert (c.wrong, c.suggestion) == ("天", "田")
    assert 0.5 < c.confidence <= 0.95
    assert "人工确认" in c.reason


def test_contiguous_edits_merged_into_one_span(tmp_db, monkeypatch):
    session = _FakeSession(VOCAB, {1: ("田", 0.9), 2: ("米", 0.9)})
    _enable(monkeypatch, session)
    from gwtool.core import corrector
    neural = [c for c in corrector.check_text("今天气真不好。") if c.category == "神经纠错"]
    assert len(neural) == 1, "连续改动应合并为一个区间"
    assert (neural[0].wrong, neural[0].suggestion) == ("天气", "田米")


def test_positions_already_covered_by_rule_layers_are_skipped(tmp_db, monkeypatch):
    """L1 已命中的段落，L4 不得重复报。"""
    text = "针对布署工作。"
    idx = text.index("布")
    session = _FakeSession(VOCAB, {idx: ("部", 0.95)})
    _enable(monkeypatch, session)
    from gwtool.core import corrector
    corr = corrector.check_text(text)
    assert any(c.wrong == "布署" for c in corr)          # L1 照报
    assert not [c for c in corr if c.category == "神经纠错"]
    # 必须是「占用掩码拦下」，而不是假会话抛异常被兜底吞掉导致的假通过
    assert session.calls >= 1, "推理应当发生（只是结果被跳过）"
    assert not csc_neural.last_error(), "不应出现异常兜底"


def test_punctuation_not_touched(tmp_db, monkeypatch):
    text = "今天气真不好。"
    session = _FakeSession(VOCAB, {len(text) - 1: ("米", 0.99)})
    _enable(monkeypatch, session)
    from gwtool.core import corrector
    assert not [c for c in corrector.check_text(text) if c.category == "神经纠错"]


# ------------------------------------------------------------ 降级与兜底
def test_disabled_setting_means_no_neural_layer(tmp_db, monkeypatch):
    session = _FakeSession(VOCAB, {1: ("田", 0.99)})
    monkeypatch.setattr(csc_neural, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: True)
    csc_neural.set_session_for_test(session, VOCAB, _info())
    csc_neural.set_setting(False)
    from gwtool.core import corrector
    assert not [c for c in corrector.check_text("今天气真不好。")
                if c.category == "神经纠错"]
    assert session.calls == 0, "关闭时不应发生任何推理"


def test_no_runtime_means_silent_fallback(tmp_db):
    """未安装 onnxruntime 时，开关即使为真也不生效、不报错。"""
    from gwtool.core import corrector
    csc_neural.reset()
    try:
        csc_neural.set_setting(True)
    except Exception:
        pass
    assert csc_neural.enabled() is False
    corr = corrector.check_text("会议对下一步工作进行了布署。")
    assert any(c.wrong == "布署" for c in corr)


def test_inference_exception_is_swallowed(tmp_db, monkeypatch):
    """推理崩了也绝不能影响纠错主流程。"""
    session = _FakeSession(VOCAB, boom=True)
    _enable(monkeypatch, session)
    from gwtool.core import corrector
    corr = corrector.check_text("会议对下一步工作进行了布署。")
    assert any(c.wrong == "布署" for c in corr)
    assert csc_neural.last_error()


def test_missing_pack_means_unavailable(tmp_db, monkeypatch):
    monkeypatch.setattr(csc_neural, "runtime_available", lambda: True)
    monkeypatch.setattr(enhance_pack, "verify_installed",
                        lambda kind=enhance_pack.DEFAULT_KIND: False)
    csc_neural.reset()
    csc_neural.set_setting(True)
    assert csc_neural.enabled() is False
    assert "增强包" in csc_neural.status_text()


# ------------------------------------------------------------ 增强包：构造工具
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_pack(path, *, name="p", lic="Apache-2.0", schema=1, backend="onnx",
               model=b"MODEL", vocab=b"A\nB\n", tamper_vocab=False,
               extra=None, files_override=None):
    files = {"model.onnx": _sha(model), "vocab.txt": _sha(vocab)}
    if files_override is not None:
        files = files_override
    man = {"schema": schema, "name": name, "kind": "csc", "version": "1.0.0",
           "license": lic, "source": "https://example.invalid",
           "backend": backend, "max_len": 64, "files": files}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
        zf.writestr("model.onnx", model)
        zf.writestr("vocab.txt", b"TAMPERED\n" if tamper_vocab else vocab)
        for n, d in (extra or {}).items():
            zf.writestr(n, d)
    return path


# ------------------------------------------------------------ 增强包：正常路径
def test_install_verify_remove(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "ok.zip")
    info = enhance_pack.install_pack(p)
    assert info.name == "p" and info.license == "Apache-2.0"
    assert enhance_pack.installed_pack() is not None
    assert enhance_pack.verify_installed()
    # 增强包按 kind 分槽：csc 包落在 current/csc 下
    assert (paths.enhance_dir() / "current" / "csc" / "model.onnx").exists()
    assert "p" in enhance_pack.installed_pack().summary()
    assert enhance_pack.remove_pack() is True
    assert enhance_pack.installed_pack() is None
    assert enhance_pack.remove_pack() is False


# ------------------------------------------------------------ 增强包：拒绝路径
def test_missing_license_rejected(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "nolic.zip", lic="")
    with pytest.raises(enhance_pack.EnhancePackError, match="license"):
        enhance_pack.install_pack(p)


def test_bad_schema_rejected(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "schema.zip", schema=99)
    with pytest.raises(enhance_pack.EnhancePackError, match="schema"):
        enhance_pack.install_pack(p)


def test_unsupported_backend_rejected(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "be.zip", backend="gguf")
    with pytest.raises(enhance_pack.EnhancePackError, match="backend"):
        enhance_pack.install_pack(p)


def test_hash_mismatch_rejected_and_previous_install_intact(tmp_db, tmp_path):
    good = _make_pack(tmp_path / "good.zip", name="good")
    enhance_pack.install_pack(good)
    bad = _make_pack(tmp_path / "bad.zip", name="bad", tamper_vocab=True)
    with pytest.raises(enhance_pack.EnhancePackError, match="校验失败"):
        enhance_pack.install_pack(bad)
    info = enhance_pack.installed_pack()
    assert info is not None and info.name == "good", "失败的导入不得破坏已装包"


def test_missing_required_file_rejected(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "noreq.zip",
                   files_override={"model.onnx": _sha(b"MODEL")})
    with pytest.raises(enhance_pack.EnhancePackError):
        enhance_pack.install_pack(p)


def test_zip_slip_rejected(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "slip.zip", extra={"../evil.txt": b"x"})
    with pytest.raises(enhance_pack.EnhancePackError, match="危险路径"):
        enhance_pack.install_pack(p)


def test_absolute_path_rejected(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "abs.zip", extra={"/tmp/evil.txt": b"x"})
    with pytest.raises(enhance_pack.EnhancePackError, match="绝对路径"):
        enhance_pack.install_pack(p)


def test_not_a_zip_rejected(tmp_db, tmp_path):
    p = tmp_path / "plain.txt"
    p.write_text("not a zip", encoding="utf-8")
    with pytest.raises(enhance_pack.EnhancePackError):
        enhance_pack.install_pack(p)


def test_missing_file_rejected(tmp_db, tmp_path):
    with pytest.raises(enhance_pack.EnhancePackError, match="不存在"):
        enhance_pack.install_pack(tmp_path / "nope.zip")


def test_status_text_mentions_runtime_or_pack(tmp_db):
    csc_neural.reset()
    assert csc_neural.status_text()
