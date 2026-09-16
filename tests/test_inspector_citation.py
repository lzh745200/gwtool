# -*- coding: utf-8 -*-
"""引文规范检查（inspector._check_citations）的测试。

分两组：
  · 命中组——能查出各类引文硬伤；
  · **不误报组**——这是更重要的一组。引文检查一旦误报率高，用户会连同整个
    体检一起关掉，那还不如不做。故对法规规章、已标注字号、重复引用等
    合法写法逐条钉死。
"""
from __future__ import annotations

from gwtool.core.inspector import inspect_text


def _cites(text: str) -> list:
    """只取「引文规范」这一类的 Finding，隔离其他检查项的干扰。"""
    return [f for f in inspect_text(text) if f.item == "引文规范"]


def _details(text: str) -> str:
    return " | ".join(f.detail for f in _cites(text))


class TestStructuralErrors:
    def test_unpaired_brackets(self):
        assert "不配对" in _details("《关于做好工作的通知\n正文")

    def test_empty_book_title(self):
        assert "书名号内为空" in _details("《》")

    def test_whitespace_only_book_title(self):
        assert "书名号内为空" in _details("《　》")

    def test_nested_book_title(self):
        got = _details("《关于印发《XX办法》的通知》")
        assert "嵌套" in got

    def test_empty_text_is_safe(self):
        assert _cites("") == []


class TestMissingDocNumber:
    def test_flags_official_doc_without_number(self):
        text = "根据《关于做好安全生产工作的通知》，现就有关事项通知如下。"
        assert "首次引用建议标注发文字号" in _details(text)

    def test_accepts_doc_with_number(self):
        text = ("根据《关于做好安全生产工作的通知》（×政发〔2026〕5号），"
                "现就有关事项通知如下。")
        assert "首次引用建议标注发文字号" not in _details(text)

    def test_ignores_national_laws(self):
        # 引用法律从不带字号，若要求带字号会大面积误报
        assert "首次引用" not in _details("根据《中华人民共和国行政处罚法》的规定")
        assert "首次引用" not in _details("依据《中华人民共和国宪法》")

    def test_ignores_local_regulations(self):
        assert "首次引用" not in _details("按照《XX省节约用水条例》执行")
        assert "首次引用" not in _details("根据《XX工作办法》办理")
        assert "首次引用" not in _details("执行《XX实施细则》")

    def test_reports_only_first_occurrence(self):
        # 同一文件后文再提不必重复要求带字号，否则长文会被刷屏
        name = "关于做好安全生产工作的通知"
        text = (f"根据《{name}》，现通知如下。另请一并落实《{name}》的要求。")
        hits = [f for f in _cites(text) if "首次引用" in f.detail]
        assert len(hits) == 1

    def test_accepts_number_written_with_space(self):
        text = "根据《关于做好工作的通知》（ ×政发〔2026〕5号 ）执行"
        assert "首次引用" not in _details(text)


class TestHalfwidthParen:
    def test_flags_halfwidth_after_title(self):
        got = _details("《关于做好工作的通知》(×政发〔2026〕5号)")
        assert "全角括号" in got

    def test_accepts_fullwidth(self):
        got = _details("《关于做好工作的通知》（×政发〔2026〕5号）")
        assert "全角括号" not in got


class TestRepeatedLeadIn:
    def test_flags_adjacent_genju(self):
        got = _details("根据《甲文件》根据《乙文件》的要求，现通知如下")
        assert "建议合并" in got

    def test_accepts_when_separated_by_punctuation(self):
        # 中间有句号说明是各自独立的句子，属正常写法
        assert "建议合并" not in _details("根据《甲文件》。根据《乙文件》的要求")

    def test_accepts_far_apart(self):
        filler = "为贯彻落实上级部署要求并结合本地区本部门工作实际" * 2
        assert "建议合并" not in _details(
            f"根据《甲文件》，{filler}，根据《乙文件》执行")


class TestNoFalsePositivesOnCleanText:
    """正常公文不得出现任何引文提示。"""

    def test_citation_check_is_wired_into_inspect_text(self):
        """元测试：守住检查不被从 inspect_text 里摘掉。

        本类其余用例都是**负向**断言（"不报警"），一旦 `_check_citations`
        被移除或不再被调用，它们会**全部假绿** —— 必须有正向锚点钉住接线。
        """
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        code = (root / "gwtool" / "core" / "inspector.py").read_text(
            encoding="utf-8")
        assert "def _check_citations(" in code, "检查函数被删除了"
        assert "out.extend(_check_citations(lines))" in code, (
            "检查函数存在但未接入 inspect_text —— 负向用例会假绿")

    def test_clean_official_document(self):
        text = (
            "关于做好安全生产工作的通知\n"
            "各科室：\n"
            "根据《关于进一步加强安全生产工作的意见》"
            "（×政发〔2026〕5号），现就有关事项通知如下。\n"
            "一、提高认识\n"
            "《中华人民共和国安全生产法》是本项工作的根本遵循。\n"
            "特此通知。\n"
        )
        assert _cites(text) == [], _details(text)

    def test_multiple_citations_one_lead_in(self):
        # 「根据《A》《B》」是规范写法（一次引导、复数引用），不得报重复引导
        text = ("根据《中华人民共和国安全生产法》《关于做好工作的通知》"
                "（×政发〔2026〕5号），现通知如下。")
        assert "建议合并" not in _details(text)

    def test_citation_inside_parentheses(self):
        text = "该事项依据上级文件（见《关于做好工作的通知》〔2026〕5号）办理。"
        # 只要不报硬错即可；此处主要守住"不因写法变体而产生 error"
        assert not [f for f in _cites(text) if f.severity == "error"]
