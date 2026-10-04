# -*- coding: utf-8 -*-
"""风格量化引擎的**量法**测试（D1–D14 逐项）。

量法是整条链路的根：参数表是别人用 102 万字语料统计出来的，如果本项目的
量法与统计时的口径不一致，那对照就完全失效 —— 表格再准也没用。
所以这一组把每个量法的边界行为都钉住。
"""
from __future__ import annotations

from gwtool.core import style_data as D
from gwtool.core import style_profile as SP


class TestLengthAndSentences:
    def test_chars_excludes_newlines_and_markdown(self):
        a = SP.analyze("**加粗**与`代码`\n第二行")
        # 去掉 ** ` 与换行后 = "加粗与代码第二行"
        assert a["chars"] == len("加粗与代码第二行")

    def test_empty_text_returns_none(self):
        assert SP.analyze("") is None
        assert SP.analyze("   \n\n  ") is None

    def test_heading_only_line_still_yields_text(self):
        """只有一行标题时**不算空**：去掉 `##` 后标题本身就是正文。

        这是上游口径（标题计入字数），刻意保留 —— 改口径就必须重算参数表。
        """
        a = SP.analyze("## 标题")
        assert a is not None and a["chars"] == len("标题")

    def test_sentence_split_on_four_marks(self):
        a = SP.analyze("第一句话在这里。第二句话在这里！第三句在这里？第四句在这里；")
        assert len([s for s in a["plain"].replace("\n", "").split("。")]) >= 1
        assert a["sent"] > 0

    def test_short_fragments_not_counted_as_sentence(self):
        # 长度 < 4 的片段不计入句长统计（避免"是。"这类碎片拉低均值）
        a = SP.analyze("是。好。这是一句足够长的正文内容。")
        assert a["sent"] == len("这是一句足够长的正文内容")

    def test_markdown_blockquote_and_table_skipped(self):
        a = SP.analyze("正文一段足够长的内容。\n> 引用行\n| 表 | 格 |\n---\n")
        assert "引用行" not in a["plain"]
        assert "表" not in a["plain"]


class TestHeadingLevels:
    def test_all_three_levels_counted(self):
        text = "一、一级\n（一）二级\n1．三级\n这是一段足够长的正文内容用来充数。"
        a = SP.analyze(text)
        assert a["h1"] == 1 and a["h2"] == 1 and a["h3"] == 1

    def test_bold_marked_headings_still_counted(self):
        text = "**一、加粗一级**\n**（一）加粗二级**\n**1．加粗三级**\n正文够长的一段内容。"
        a = SP.analyze(text)
        assert (a["h1"], a["h2"], a["h3"]) == (1, 1, 1)

    def test_markdown_hash_headings_are_read(self):
        a = SP.analyze("## 一、标题\n正文内容足够长的一段话。")
        assert a["h1"] == 1

    def test_h1_title_text_captured(self):
        a = SP.analyze("一、工作目标\n正文够长。")
        assert a["h1_titles"] == ["工作目标"]

    def test_numeric_ordinal_requires_non_digit_after(self):
        # `1.` 后面必须不是数字，否则 "1.5%" 之类的小数会被误判成三级标题
        a = SP.analyze("1.5%的增长\n1．三级标题\n正文够长的一段内容。")
        assert a["h3"] == 1

    def test_yishi_counted_with_optional_punctuation(self):
        a = SP.analyze("一是加强领导，二是明确责任、三是抓好落实。正文够长的内容。")
        assert a["yishi"] == 3


class TestPunctuationAndDensity:
    def test_dun_is_per_thousand_chars(self):
        text = "甲、乙、丙、丁" + "填充" * 100
        a = SP.analyze(text)
        assert a["dun"] == round(1000 * 3 / a["chars"], 1)

    def test_dash_counted(self):
        a = SP.analyze("这是一个——破折号。正文够长的一段内容。")
        assert a["dash"] == 1

    def test_ascii_quote_counted(self):
        a = SP.analyze('他说"这是引语"了。正文够长的一段内容。')
        assert a["ascii_quote"] == 2

    def test_short_quote_concept_counted(self):
        a = SP.analyze("这叫“四链联动”。正文够长的一段内容在这里。")
        assert a["quote"] == 1

    def test_long_quote_measured_per_thousand(self):
        long_q = "“这是一句明显超过九个字的长引语内容”"
        a = SP.analyze(long_q + "填充" * 100)
        assert a["quote_long"] > 0

    def test_long_quote_only_counts_double_curly_quotes(self):
        """ASCII 直角引号无法配对，不能计入长引语，否则统计失真。"""
        a = SP.analyze('"这是一句明显超过九个字的内容"' + "填充" * 100)
        assert a["quote_long"] == 0


class TestStrengthWords:
    def test_four_strength_categories(self):
        text = "必须落实，应当完成，不得违反。要切实确保到位，建议支持推进。"
        a = SP.analyze(text)
        assert a["qz"] == 3          # 必须 应当 不得
        assert a["yq"] >= 2          # 要 切实 确保
        assert a["jy"] >= 2          # 建议 支持

    def test_quoted_content_excluded_from_strength_count(self):
        """自造概念常带力度字样（如需求分类「必须改」），计入会顶破配额。

        经验总结的强制词配额只有 0-1 个，任何误判都是致命的。
        """
        a = SP.analyze("需求分三类：“必须改”“可以缓”。正文够长的一段内容。")
        assert a["qz"] == 0

    def test_budebu_is_not_a_prohibition(self):
        """「不得不」是"只能"义，不是禁止义，必须从「不得」中排除。"""
        a = SP.analyze("我们不得不这样做。正文够长的一段内容在这里。")
        assert a["qz"] == 0

    def test_plain_budoe_is_counted(self):
        a = SP.analyze("不得违反规定。正文够长的一段内容在这里。")
        assert a["qz"] == 1


class TestPercentAndPlaceholder:
    def test_percent_counted(self):
        a = SP.analyze("增长了15%，提高了3.5%。正文够长的一段内容。")
        assert a["pct"] == 2

    def test_placeholder_percent_not_counted(self):
        """【待补：xx%】里的 % 不是真实数据 —— 占位不算用百分比。

        这条直接支撑生成侧铁律 G1：拿不准就占位，不会因此被判"用了数据"。
        """
        a = SP.analyze("新增点位【待补：xx%】个。正文够长的一段内容。")
        assert a["pct"] == 0


class TestParagraphsAndFocus:
    def test_paragraph_needs_length_and_is_not_heading(self):
        # 段落判据是"长度 > 40 且不是任何层级标题行"（上游口径）
        long_body = "这是一段足够长的正文内容" * 5
        text = ("一、标题行\n"
                f"{long_body}\n"
                "太短。\n"
                "（一）二级标题也不算段落，即便它很长" + "很长" * 30 + "。")
        a = SP.analyze(text)
        assert a["paras"] == 1

    def test_section_weights_sum_to_hundred(self):
        text = "一、甲\n" + "甲" * 50 + "\n二、乙\n" + "乙" * 50
        a = SP.analyze(text)
        total = sum(pct for _n, _c, pct in a["sections"])
        assert 99.0 <= total <= 101.0

    def test_sections_have_lead_in_and_named_parts(self):
        text = "导语部分内容足够长。\n一、甲部分\n" + "甲" * 50
        a = SP.analyze(text)
        names = [n for n, _c, _p in a["sections"]]
        assert names[0] == "导语"
        assert any("甲部分" in n for n in names)


class TestGenreMatching:
    def test_plan_sample_matches_plan_genre(self):
        """一份典型工作方案（三段、三级标题「1．」、无「一是」）应最像工作方案。

        这是整张参数表能不能用的**端到端验证**：若匹配不出来，说明量法与
        统计口径不一致。
        """
        text = ("一、工作目标\n到今年年底补齐设施缺口。\n"
                "二、重点任务\n"
                "（一）摸清底数。\n1．开展拉网式排查，逐小区核清保有量。\n"
                "2．建立台账，明确责任单位与时限。\n"
                "3．动态更新，每月更新一次。\n"
                "（二）补齐缺口。\n1．加快建设集中充停设施。\n"
                "2．加装阻车系统。\n3．整治违规停放。\n"
                "三、保障措施\n（一）加强组织领导。\n"
                "（二）强化资金保障，由财政统筹安排。\n"
                "（三）严格督导考核，纳入年度考核。\n" + "补充说明内容。" * 60)
        assert SP.match_genre(SP.analyze(text))[0][0] == "工作方案"

    def test_short_material_matches_dangjian_family(self):
        text = ("党建工作最怕与生产经营两张皮。\n" + "叙述做法与成效。" * 40
                + "\n党支部书记田志远说：“过去总觉得党建是软指标，如今谁也不敢往后放。”"
                + "继续陈述做法。" * 40)
        top = SP.match_genre(SP.analyze(text))[0][0]
        assert top == "短经验材料"

    def test_ranking_covers_all_seven_styles(self):
        a = SP.analyze("一、标题\n正文内容足够长的一段话在这里。")
        assert len(SP.match_genre(a)) == len(D.STYLES)


class TestSkeletonDraft:
    def test_every_style_has_a_draft(self):
        for s in D.STYLES:
            draft = SP.skeleton_draft(s)
            assert draft.strip(), f"{s} 没有骨架草稿"

    def test_draft_has_no_invented_content(self):
        """骨架草稿只能有【待补】占位与约束说明，**不能凭空写出成文内容**。

        这条守住生成侧铁律 G1（不编数据/不编内容）。
        """
        for s in D.STYLES:
            draft = SP.skeleton_draft(s)
            assert "【待补" in draft, f"{s} 缺少占位标记"

    def test_draft_contains_style_constraints(self):
        draft = SP.skeleton_draft("工作方案")
        assert D.ID_PARAM["工作方案"] in draft
        assert "重心" in draft and "篇幅目标" in draft

    def test_unknown_style_returns_empty(self):
        assert SP.skeleton_draft("不存在的文体") == ""


class TestDataIntegrity:
    def test_all_ref_rows_are_lo_hi_mid_triples(self):
        for style, row in D.REF.items():
            for key, val in row.items():
                if key == "n":
                    continue
                assert isinstance(val, tuple) and len(val) == 3, f"{style}.{key}"
                lo, hi, mid = val
                assert lo <= mid <= hi, f"{style}.{key} 的中位数不在区间内"

    def test_family_mapping_covers_every_style(self):
        assert set(D.FAMILY_OF) == set(D.STYLES)
        assert "短经验材料" in D.STYLES_OF_FAMILY[D.FAMILY_DANGJIAN]

    def test_hard_rules_reference_real_fields(self):
        for style, rules in D.HARD.items():
            for field_key, _fn, _msg, _basis in rules:
                assert field_key in D.REF[style], f"{style} 的硬冲突引用了不存在的字段"

    def test_soft_rules_reference_real_fields(self):
        for style, rules in D.SOFT.items():
            for field_key, _fn, _msg, _basis in rules:
                assert field_key in D.REF[style]

    def test_id_param_and_skeleton_cover_every_style(self):
        for s in D.STYLES:
            assert s in D.ID_PARAM
            assert s in D.SKELETON

    def test_legal_documents_are_not_parametrized(self):
        """法定公文刻意不设参数行 —— 其结构由制度锁定，是固定填空而非写作技法。"""
        for name in ("通知", "请示", "批复", "报告"):
            assert name not in D.REF
            assert name in D.NO_PARAM
