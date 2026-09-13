# -*- coding: utf-8 -*-
"""纠错链（corrector/enhance_pack/csc_neural/ruleset）逐模块深探。

全部真实调用（真实 zip、真实损坏 SQLite 文件、真实 onnxruntime），禁 mock。
覆盖此前零执行路径，每条用例注明探测动机：
  A. corrector —— 停用对过滤、词内高置信抑制、无效对防御、损坏库回退、
     apply/标记渲染的边界防御；
  B. enhance_pack —— 空 zip、文件过多、符号链接、清单畸形（非 JSON/数组/
     缺 name/缺 files）、kind 不匹配、max_len 异常、旧布局迁移、卸载空槽位；
  C. csc_neural —— _load_engine 真实加载链（未装包/词汇表过小/伪 onnx）、
     status_text 各状态、标量检测支路、无名称位置约定、超长文本截断。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gwtool.core import corrector
from gwtool.core import csc_neural
from gwtool.core import enhance_pack
from gwtool.db import connection as dbconn
from gwtool.db import dao


# ================================================================ A. corrector
class TestPairValidation:
    """加载端防御：无效纠错对不得进入词库。"""

    @pytest.mark.parametrize("wrong,correct", [
        ("", "部署"),          # 空串
        ("部署", "部署"),      # 同词
        ("abc", "xyz"),        # 无中文
        ("部署\n", "部署"),    # 含换行
        ("部 署", "部署"),     # 含空格
    ])
    def test_invalid_pairs_rejected(self, wrong, correct):
        assert not corrector._is_valid_pair(wrong, correct)

    def test_valid_pair_accepted(self):
        assert corrector._is_valid_pair("布署", "部署")

    def test_invalid_db_pair_never_hits(self, tmp_db):
        """数据库里的无效对（无中文/含空白）在真实检索路径下不生效。"""
        dao.add_error_pair("hello", "world", source="探针")
        dao.add_error_pair("部 署", "部署", source="探针")
        corrector.invalidate_cache()
        assert corrector.check_text("hello world 部 署") == []


class TestDisabledPairs:
    def test_disabled_pair_excluded(self, tmp_db):
        """停用某来源后，该来源的对立即不再参与纠错（缓存同步失效）。

        用词刻意避开内置精标库（"布署"内置已有 0.98 对，会混淆归属）。
        """
        dao.add_error_pair("探针错词", "探针对词", source="停用源", confidence=0.99)
        corrector.invalidate_cache()
        assert corrector.check_text("探针错词出现了")
        dao.set_error_pairs_enabled(False, source="停用源")
        corrector.invalidate_cache()
        assert corrector.check_text("探针错词出现了") == []
        # 重新启用后恢复
        dao.set_error_pairs_enabled(True, source="停用源")
        corrector.invalidate_cache()
        assert corrector.check_text("探针错词出现了")


class TestInWordSuppression:
    def test_high_conf_inside_word_still_suppressed(self, tmp_db):
        """高置信对命中两端都落在词内部时同样抑制（'安全生产' 内的 '全生'）。"""
        dao.add_error_pair("全生", "全省", confidence=0.99, source="探针")
        corrector.invalidate_cache()
        assert corrector.check_text("安全生产责任制") == []

    def test_boundary_hit_survives(self, tmp_db):
        """同一对在正常词边界上则正常报出。"""
        dao.add_error_pair("布署", "部署", confidence=0.99, source="探针")
        corrector.invalidate_cache()
        hits = corrector.check_text("布署工作")
        assert len(hits) == 1 and hits[0].suggestion == "部署"


class TestCorruptedDbFallback:
    def test_load_patterns_survives_broken_db(self, tmp_db, tmp_path):
        """数据库文件损坏时纠错引擎回退到纯内置词库，绝不崩主流程。"""
        broken = tmp_path / "broken.db"
        broken.write_bytes(b"this is not a sqlite database at all" * 64)
        original = dbconn.current_db_file()
        try:
            dbconn.close_current_thread()
            dbconn.configure(broken)
            corrector.invalidate_cache()
            patterns = corrector._load_patterns()
            assert patterns, "损坏库时必须退回内置精标对"
            assert any(w == "布署" for w in patterns), "内置对（布署/部署）应在"
        finally:
            dbconn.close_current_thread()
            dbconn.configure(original)
            corrector.invalidate_cache()

    def test_neural_setting_survives_broken_db(self, tmp_db, tmp_path):
        """设置表不可读时 L4 开关按关闭处理。"""
        broken = tmp_path / "broken2.db"
        broken.write_bytes(b"garbage" * 512)
        original = dbconn.current_db_file()
        try:
            dbconn.close_current_thread()
            dbconn.configure(broken)
            csc_neural.invalidate_cache()
            assert csc_neural._setting_on() is False
        finally:
            dbconn.close_current_thread()
            dbconn.configure(original)
            csc_neural.invalidate_cache()


class TestApplyAndRender:
    def test_apply_correction_single(self):
        cs = corrector.check_text("布署工作")
        c = cs[0]
        assert corrector.apply_correction("布署工作", c) == "部署工作"

    def test_apply_all_with_skip(self):
        from gwtool.core.corrector import Correction
        text = "甲乙丙"
        cs = [Correction(0, 1, "甲", "A", "错别字", "r", 0.9),
              Correction(2, 3, "丙", "C", "错别字", "r", 0.9)]
        assert corrector.apply_all(text, cs) == "A乙C"
        assert corrector.apply_all(text, cs, skip={0}) == "甲乙C"

    def test_marked_html_defends_against_bad_ranges(self):
        """越界/零长区间被跳过，正常区间照常渲染（渲染层防御）。"""
        from gwtool.core.corrector import Correction
        bad = [Correction(0, 99, "越界", "x", "错别字", "r", 0.9),
               Correction(2, 2, "零长", "x", "错别字", "r", 0.9),
               Correction(0, 2, "布署", "部署", "错别字", "r", 0.9)]
        html = corrector.to_marked_html("布署工作", bad)
        assert "部署" in html
        assert html.count("<a name=") == 1  # 只有合法的一条被渲染

    def test_paragraph_no(self):
        text = "第一段\n第二段\n第三段"
        assert corrector.paragraph_no(text, 0) == 1
        assert corrector.paragraph_no(text, 5) == 2
        assert corrector.paragraph_no(text, 999) == 3

    def test_empty_text(self, tmp_db):
        assert corrector.check_text("") == []


# ============================================================ B. enhance_pack
def _manifest(kind="csc", **over) -> dict:
    m = {
        "schema": 1, "name": "探针包", "kind": kind, "version": "0.0.1",
        "license": "Apache-2.0", "source": "https://example.test/pack",
        "backend": "onnx", "max_len": 64,
        "files": {},
    }
    m.update(over)
    return m


def _make_zip(path: Path, man: dict, extra: dict[str, bytes] | None = None,
              members: list | None = None) -> dict[str, bytes]:
    """造一个真实 zip。清单的 files 哈希表自动回填（供 install 校验）。"""
    import hashlib
    files: dict[str, bytes] = {
        "model.onnx": b"probe-onnx-bytes",
        "vocab.txt": "\n".join(f"tok{i}" for i in range(150)).encode(),
    }
    if extra:
        files.update(extra)
    if members is None:
        members = [(k, v) for k, v in files.items()]
        hashes = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
        man.setdefault("files", {}).update(hashes)
        members.append(("manifest.json", json.dumps(man).encode()))
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}


class TestEnhancePackProbe:
    def _write(self, tmp_path, man=None, **kw) -> Path:
        p = tmp_path / "pack.zip"
        hashes = _make_zip(p, man or _manifest(), **kw)
        return p, hashes

    def test_empty_zip_rejected(self, tmp_path):
        """彻底空的 zip：read_manifest 报缺少清单。"""
        p = tmp_path / "empty.zip"
        with zipfile.ZipFile(p, "w") as zf:
            pass  # 一个成员都没有
        with pytest.raises(enhance_pack.EnhancePackError, match="缺少 manifest"):
            enhance_pack.read_manifest(p)

    def test_dirs_only_zip_rejected_by_safe_members(self, tmp_path):
        """只有目录条目、无实文件的包：_safe_members 的空包防御（产品路径
        上 read_manifest 会先行拒绝，本用例直接验证防御本体仍有效）。"""
        p2 = tmp_path / "dirs_only.zip"
        with zipfile.ZipFile(p2, "w") as zf:
            zf.writestr(zipfile.ZipInfo("d1/"), "")
            zf.writestr(zipfile.ZipInfo("d2/"), "")
        with zipfile.ZipFile(p2) as zf:
            with pytest.raises(enhance_pack.EnhancePackError, match="没有任何文件"):
                enhance_pack._safe_members(zf)

    def test_too_many_entries_rejected(self, tmp_path):
        p = tmp_path / "many.zip"
        with zipfile.ZipFile(p, "w") as zf:
            for i in range(enhance_pack.MAX_ENTRIES + 1):
                zf.writestr(f"f{i}.bin", "x")
        with zipfile.ZipFile(p) as zf:
            with pytest.raises(enhance_pack.EnhancePackError, match="文件过多"):
                enhance_pack._safe_members(zf)

    def test_symlink_member_rejected(self, tmp_path):
        """符号链接成员（外部属性高 16 位 = 0xA000）必须整包拒绝。"""
        p = tmp_path / "link.zip"
        with zipfile.ZipFile(p, "w") as zf:
            info = zipfile.ZipInfo("evil_link")
            info.external_attr = (0xA000 << 16)  # S_IFLNK
            zf.writestr(info, "/etc/passwd")
        with zipfile.ZipFile(p) as zf:
            with pytest.raises(enhance_pack.EnhancePackError, match="符号链接"):
                enhance_pack._safe_members(zf)

    def test_manifest_not_json(self, tmp_path):
        p = tmp_path / "badjson.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("manifest.json", b"{not json")
        with pytest.raises(enhance_pack.EnhancePackError, match="JSON"):
            enhance_pack.read_manifest(p)

    def test_manifest_array_root(self, tmp_path):
        p = tmp_path / "arr.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("manifest.json", b"[1,2,3]")
        with pytest.raises(enhance_pack.EnhancePackError, match="对象"):
            enhance_pack.read_manifest(p)

    @pytest.mark.parametrize("over,match", [
        ({"name": ""}, "name"),
        ({"license": ""}, "license"),
        ({"backend": "gguf"}, "backend"),
        ({"files": None}, "files"),
        ({"files": {}}, "必填文件"),
    ])
    def test_manifest_field_validation(self, tmp_path, over, match):
        p = tmp_path / "field.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("manifest.json",
                        json.dumps(_manifest(**over)).encode())
        with pytest.raises(enhance_pack.EnhancePackError, match=match):
            enhance_pack.read_manifest(p)

    def test_kind_mismatch_rejected(self, tmp_db, tmp_path):
        """显式指定 kind 与清单不一致时拒绝——语法包不能塞进拼写槽位。"""
        p, _ = self._write(tmp_path)
        with pytest.raises(enhance_pack.EnhancePackError, match="不匹配"):
            enhance_pack.install_pack(p, kind="cgec")

    def test_missing_member_file_rejected(self, tmp_db, tmp_path):
        """manifest 登记了哈希但 zip 内没有该文件。"""
        man = _manifest()
        man["files"] = {"model.onnx": "0" * 64, "vocab.txt": "1" * 64}
        p = tmp_path / "miss.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("manifest.json", json.dumps(man).encode())
        with pytest.raises(enhance_pack.EnhancePackError, match="缺少必填文件"):
            enhance_pack.install_pack(p)

    def test_max_len_invalid_falls_back(self, tmp_db, tmp_path):
        """max_len 不是数字时回退默认 128，整包照常安装。"""
        import hashlib
        man = _manifest(max_len="不是数字")
        files = {"model.onnx": b"x-bytes", "vocab.txt": b"tok\n" * 60}
        man["files"] = {k: hashlib.sha256(v).hexdigest()
                        for k, v in files.items()}
        p = tmp_path / "maxlen.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("manifest.json", json.dumps(man).encode())
            for k, v in files.items():
                zf.writestr(k, v)
        info = enhance_pack.install_pack(p)
        assert info.max_len == 128

    def test_migrate_layout_moves_legacy_slot(self, tmp_db):
        """旧布局（文件直接在 current/ 下）自动迁移到 current/csc/。"""
        from gwtool import paths
        cur = paths.enhance_dir() / "current"
        cur.mkdir(parents=True, exist_ok=True)
        (cur / "manifest.json").write_text(
            json.dumps(_manifest()), encoding="utf-8")
        (cur / "model.onnx").write_bytes(b"legacy")
        enhance_pack.migrate_layout()
        assert (cur / "csc" / "manifest.json").exists()
        assert (cur / "csc" / "model.onnx").exists()
        assert not (cur / "model.onnx").exists()

    def test_installed_pack_corrupt_manifest_returns_none(self, tmp_db):
        """槽位里 manifest 损坏时按未安装处理（不抛异常）。"""
        from gwtool import paths
        slot = paths.enhance_dir() / "current" / "csc"
        slot.mkdir(parents=True, exist_ok=True)
        (slot / "manifest.json").write_text("{broken", encoding="utf-8")
        assert enhance_pack.installed_pack("csc") is None

    def test_remove_pack_when_absent(self, tmp_db):
        assert enhance_pack.remove_pack("csc") is False


# ============================================================= C. csc_neural
class TestLoadEngineRealChain:
    """_load_engine 真实加载链（此前测试全程用注入会话绕过）。"""

    @pytest.fixture(autouse=True)
    def _reset(self):
        csc_neural.reset()
        yield
        csc_neural.reset()

    def test_no_pack_reports_missing(self, tmp_db):
        eng = csc_neural._load_engine()
        assert eng is None
        assert "未导入" in csc_neural.last_error()

    def test_tiny_vocab_rejected_before_ort(self, tmp_db, tmp_path):
        """词汇表 <100 行时在创建推理会话前就拒绝（真实文件链路）。"""
        from gwtool import paths
        slot = paths.enhance_dir() / "current" / "csc"
        slot.mkdir(parents=True, exist_ok=True)
        man = _manifest()
        small = b"\n".join(b"tok%d" % i for i in range(10))
        onnx = b"not-a-real-onnx"
        import hashlib
        man["files"] = {"model.onnx": hashlib.sha256(onnx).hexdigest(),
                        "vocab.txt": hashlib.sha256(small).hexdigest()}
        (slot / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (slot / "model.onnx").write_bytes(onnx)
        (slot / "vocab.txt").write_bytes(small)
        eng = csc_neural._load_engine()
        assert eng is None
        assert "词汇表过小" in csc_neural.last_error()

    def test_fake_onnx_fails_gracefully(self, tmp_db, tmp_path):
        """词汇表合规但 model.onnx 是伪文件：ORT 创建会话失败 → 优雅回落。"""
        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            pytest.skip("本机无 onnxruntime/numpy：伪模型拒绝链路需真实推理依赖")
        from gwtool import paths
        slot = paths.enhance_dir() / "current" / "csc"
        slot.mkdir(parents=True, exist_ok=True)
        man = _manifest()
        vocab = "\n".join(f"tok{i}" for i in range(150)).encode()
        onnx = b"definitely not onnx protobuf"
        import hashlib
        man["files"] = {"model.onnx": hashlib.sha256(onnx).hexdigest(),
                        "vocab.txt": hashlib.sha256(vocab).hexdigest()}
        (slot / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (slot / "model.onnx").write_bytes(onnx)
        (slot / "vocab.txt").write_bytes(vocab)
        eng = csc_neural._load_engine()
        assert eng is None, "伪 onnx 必须加载失败"
        assert "加载模型失败" in csc_neural.last_error()
        # 失败状态被缓存：同指纹不重复尝试
        assert csc_neural._load_engine() is None


class TestNeuralDecodeBranches:
    """解码支路的分支补漏（借助官方测试钩子注入会话）。

    张量形状契约（与真实 ONNX 输出一致）：run() 返回 [det_full, cor_full]，
    det_full = [1, L, x]，代码取 val[0] 得 [L, x]。
    """

    @pytest.fixture(autouse=True)
    def _reset(self, tmp_db):
        csc_neural.reset()
        dao.set_setting(csc_neural.SETTING_KEY, "1")
        yield
        csc_neural.reset()

    VOCAB = ["[pad]", "布", "署", "部", "工", "作"]

    @staticmethod
    def _sess(outputs, output_names=("detection", "correction")):
        class S:
            def run(self, _x, _f):
                return outputs
            def get_outputs(self):
                return [type("O", (), {"name": n})()
                        for n in self._names]
            _names = list(output_names)
        return S()

    @staticmethod
    def _info():
        return enhance_pack.PackInfo(name="t", max_len=16)

    def test_scalar_detect_branch(self, tmp_db):
        """检测支路为标量 logit 形式（[L,1]）：>0.5 判错、置信度走 sigmoid。

        '署'→'部' 应被解码为一条单字替换建议；其余位 argmax=0 被跳过。
        """
        det = [[2.0], [2.0], [-2.0], [-2.0], [-2.0]]        # [L,1] 标量
        cor = ([[0, 0, 0, 0, 0, 0]]                         # '布'
               + [[0, 0, 0, 9, 0, 0]]                       # '署'→vocab[3]='部'
               + [[0, 0, 0, 0, 0, 0]] * 3)                  # argmax=0 跳过
        sess = self._sess([[det], [cor]])
        csc_neural.set_session_for_test(sess, self.VOCAB, self._info())
        out = csc_neural.enhance("布署工作")
        assert len(out) == 1
        assert (out[0].wrong, out[0].suggestion) == ("署", "部")
        assert 0.55 <= out[0].confidence <= 0.95

    def test_position_fallback_without_names(self, tmp_db):
        """会话输出无名时按位置约定取 det/cor（兜底分支）。"""
        det = [[0, 1], [0, 1], [0, 1], [0, 1], [0, 1]]      # [L,2] 二分类
        cor = [[9, 0, 0, 0, 0, 0]] * 5                      # argmax=0 → 跳过
        sess = self._sess([[det], [cor]], output_names=())
        csc_neural.set_session_for_test(sess, self.VOCAB, self._info())
        out = csc_neural.enhance("布署工作")
        assert out == []  # cor argmax 全为 0 → 无有效替换

    def test_cor_none_means_no_decode(self, tmp_db):
        """只有检测支路没有纠正支路时解码为空。"""
        det = [[0, 1]] * 5
        sess = self._sess([[det]], output_names=())         # 仅 1 个输出
        csc_neural.set_session_for_test(sess, self.VOCAB, self._info())
        assert csc_neural.enhance("布署工作") == []

    def test_long_text_truncated(self, tmp_db):
        """超长文本截断到 _MAX_TEXT_CHARS 后正常完成（截断分支真实执行）。"""
        det = [[0, 1]] * 2
        cor = [[9, 0, 0]] * 2
        sess = self._sess([[det], [cor]])
        csc_neural.set_session_for_test(sess, ["[pad]", "字"], self._info())
        long_text = "字" * (csc_neural._MAX_TEXT_CHARS + 500)
        assert csc_neural.enhance(long_text) == []

    def test_empty_text_short_circuit(self, tmp_db):
        assert csc_neural.enhance("") == []

    def test_status_text_without_pack(self, tmp_db):
        csc_neural.reset()
        s = csc_neural.status_text()
        assert isinstance(s, str) and s
