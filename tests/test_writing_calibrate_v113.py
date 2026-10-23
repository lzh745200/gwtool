# -*- coding: utf-8 -*-
"""写作能力量化校准（v1.14）的回归测试。

三块内容：
  1. **16 项检查正例矩阵**：校准只允许降误报，不允许把"该报的"改没了；
  2. **逐项降噪的正反例**：每条阈值/逻辑调整都配真实句子（正例仍报、反例不报）；
  3. **语料冒烟**：本地存在 `doc/corpus/real_all` 时，规范公文不得出现 warn。

所有断言都基于真实公文句子（v1.14 校准语料 220 篇 / 46.3 万汉字中摘录或同构改写）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gwtool.core import writing_hints as WH
from gwtool.core import writing_data as W


# ---------------------------------------------------------------- 工具
def _titles(text: str, kind: str = "") -> str:
    return " ".join(h.title for h in WH.hints(text, kind))


def _has(text: str, kind: str, scope: str, kw: str) -> bool:
    return any(h.scope == scope and (kw == "" or kw in h.title)
               for h in WH.hints(text, kind))


def _warn_titles(text: str, kind: str) -> list[str]:
    return [h.title for h in WH.hints(text, kind) if h.level == "warn"]


# ============================================================ 1. 16 项检查正例
FORMAL = ("××市教育局关于开展2026年秋季校园安全隐患排查整治工作的通知\n"
          "各区县教育局，市直各学校：\n"
          "为贯彻落实全市安全生产工作会议精神，现就有关事项通知如下。\n"
          "一、排查范围\n各单位要结合实际制定实施方案，明确排查清单和责任分工。\n"
          "二、工作要求\n（一）提高认识。各单位要充分认识校园安全工作的极端重要性。\n"
          "（二）严格督导。对工作不力的，严肃追究相关责任。\n"
          "特此通知。\n××市教育局\n2026年9月10日\n")

POSITIVES = [
    # (名称, 文本, kind, scope, 标题关键词)
    ("单句过长",
     "各单位要按照职责分工，" + "进一步明确工作任务细化工作措施压实工作责任" * 5
     + "确保各项任务落地见效。", "", "文风", "句偏长"),
    ("段落过长",
     "一、总体要求\n" + "坚持以人民为中心的发展思想，把校园安全作为教育工作的底线任务，"
     "健全责任体系，完善工作机制，强化督导检查，推动各项措施落地见效。" * 12
     + "\n二、保障措施\n" + "加强组织领导，加大投入保障，严格考核问责。" * 4,
     "", "衔接", "段过长"),
    ("句式疲劳",
     "要压实工作责任，明确各单位主要负责同志为第一责任人。要健全工作机制，"
     "建立定期调度和情况通报制度。要强化督导检查，对发现问题及时督促整改。",
     "", "文风", "连续"),
    ("层次序号",
     "一、总体要求\n内容甲。\n三、保障措施\n内容丙。", "", "结构", "层次序号"),
    ("标点密度",
     "各单位要迅速行动！要压实责任！要严格督导！确保工作取得实效。",
     "", "文风", "感叹号"),
    ("口语化",
     "这项工作大家要抓紧搞定，别等着上面催。", "", "用语", "口语化表达"),
    ("模糊表述",
     "请有关部门进一步核实，适当调整，及时报送。", "", "文风", "用语偏空泛"),
    ("重复短语",
     "安全生产是底线。抓好安全生产是关键。安全生产责任重于泰山。",
     "", "用语", "重复用语"),
    ("自称一致",
     "我单位高度重视此项工作，成立了专项工作组。本单位将于9月底前完成自查。",
     "", "一致性", "自称不统一"),
    ("标题长度",
     "××市教育局关于深入贯彻落实全市安全生产工作会议精神切实做好2026年秋季"
     "校园安全隐患排查整治工作有关事项的通知\n正文。", "", "结构", "标题偏长"),
    ("文内一致性",
     "各单位应于3个工作日内反馈，特殊事项可延长至三个工作日。",
     "", "一致性", ""),
    ("要素",
     "请各单位按照要求做好相关工作，确保各项任务落实到位。", "通知", "要素", ""),
    ("结构",
     "××市教育局关于开展排查的通知\n各区县教育局：\n请各单位认真开展排查工作。",
     "通知", "结构", ""),
    ("衔接",
     "为做好校园安全工作，现就有关事项通知如下。\n"
     "各单位要高度重视校园安全工作，把安全工作摆在突出位置，切实抓紧抓好，"
     "确保不发生责任事故，确保师生生命财产安全。\n"
     "要健全工作机制，完善制度体系，形成常态化管理格局，推动各项工作有序开展，"
     "把安全要求落到每一个环节、每一个岗位。\n"
     "要加强督导检查，对发现的问题及时督促整改，确保闭环管理、件件有着落，"
     "对工作不力的严肃追究相关责任。", "", "衔接", "分层"),
    ("平均句长",
     "各单位要按照职责分工明确工作任务细化工作措施压实工作责任确保各项任务"
     "落地见效推动工作取得实效形成长效机制不断提升工作水平为高质量发展提供保障"
     "营造良好环境的总体要求各级领导干部要切实增强责任意识强化担当精神改进工作"
     "作风密切联系群众把各项部署要求落到实处见到实效不断提高群众的获得感和满意度。",
     "", "文风", "平均句长偏长"),
    ("文种识别",
     "近年来，我们坚持把党建引领贯穿基层治理全过程，推动资源下沉、力量下沉，"
     "形成了共建共治共享的良好局面。", "工作方案", "文风", "本文更像"),
]


@pytest.mark.parametrize("name,text,kind,scope,kw", POSITIVES)
def test_sixteen_checks_positive_matrix(name, text, kind, scope, kw):
    """16 项检查必须全部能命中（16/16）——校准不得降低检出能力。"""
    assert _has(text, kind, scope, kw), f"{name} 正例未命中：{_titles(text, kind)}"


def test_positive_matrix_covers_sixteen_checks():
    assert len(POSITIVES) == 16, "正例矩阵必须覆盖 16 项检查"
    assert len({p[0] for p in POSITIVES}) == 16


# ============================================================ 2. warn 误报 = 0
def test_formal_notice_has_no_warn():
    assert _warn_titles(FORMAL, "通知") == []


def test_missing_closing_is_still_warned():
    """真缺结束语（是公文、也没等价写法）仍要 warn —— 只修误报，不放松。"""
    text = ("××市教育局关于开展排查的通知\n"
            "各区县教育局：\n请各单位认真开展排查工作。\n")
    assert "缺少结束语" in _warn_titles(text, "通知")


def test_equivalent_closing_accepted():
    """等价收束句（请认真贯彻执行）不再被判"缺少结束语"。"""
    text = ("××市人民政府关于加强安全生产工作的通知\n"
            "各县区人民政府：\n"
            "现将有关事项通知如下，请认真贯彻执行。\n"
            "××市人民政府\n2026年9月10日\n")
    assert "缺少结束语" not in _warn_titles(text, "通知")


def test_print_notice_exempt_from_closing():
    """印发/转发类通知：功能是印发附件，不要求「特此通知」。"""
    text = ("国务院办公厅关于印发《公共企事业单位信息公开规定制定办法》的通知\n"
            "各省、自治区、直辖市人民政府：\n"
            "《公共企事业单位信息公开规定制定办法》已经国务院同意，现印发给你们。\n"
            "国务院办公厅\n2021年12月30日\n")
    assert "缺少结束语" not in _warn_titles(text, "通知")


def test_attachment_tail_exempt_from_closing():
    """以附件收束的通知同样不要求结束语（真实国办公开办函〔2019〕61号形态）。"""
    text = ("国务院办公厅政府信息与政务公开办公室关于规范政府信息公开平台"
            "有关事项的通知\n"
            "各省、自治区、直辖市人民政府办公厅：\n"
            "各政府信息公开工作主管部门要将平台建设和管理工作作为重要内容。\n"
            "国务院办公厅政府信息与政务公开办公室\n"
            "2019年11月29日\n"
            "下载附件：政府信息公开栏目页面设计参考方案\n")
    assert "缺少结束语" not in _warn_titles(text, "通知")


def test_non_document_text_gets_no_structure_warn():
    """新闻通讯不是公文：副标题带「…的报告」也不得按报告骨架扣要素。"""
    text = ("锻造共和国“水下盾牌”\n"
            "——来自北部战区海军某潜艇部队的报告\n"
            "■解放军报记者 杜 娟 特约记者 宋润鹏\n"
            "大洋深处，一艘潜艇悄然潜航。官兵依靠团结协作闯出胜战之路。\n")
    assert _warn_titles(text, "报告") == []


# ============================================================ 3. 逐项降噪正反例
def test_layer_hint_gated_by_genre():
    """「正文分层不明显」只对公文/未定文种出（媒体文章不提示）。"""
    media = ("长征的历史从未远去。\n"
             "红军将士用双脚丈量信仰的高度，用鲜血写下不朽的篇章，"
             "他们的故事至今仍在激励着我们不断前进。\n"
             "今天，我们重温这段历史，就是要从中汲取继续前进的力量，"
             "把伟大的长征精神传承下去，走好新时代的长征路。\n"
             "在新的征程上，还有许多雪山草地要跨越，还有许多娄山关腊子口要攻克，"
             "我们唯有接续奋斗，才能不负先辈的牺牲与期望。\n")
    assert "正文分层不明显" not in _titles(media, "经验材料")
    assert "正文分层不明显" in _titles(media, "")          # 起草阶段仍提示
    assert "正文分层不明显" in _titles(media, "通知")      # 公文文种仍提示


def test_element_hints_gated_by_genre():
    """要素审查只针对公文：经验材料/讲话不报"未识别到日期"。"""
    text = "同志们，今天召开这次会议，主要任务是分析形势、部署工作。"
    assert not any(h.scope == "要素" for h in WH.hints(text, "经验材料"))
    assert any(h.scope == "要素" for h in WH.hints(text, "通知"))


def test_vague_word_single_occurrence_in_long_text_not_reported():
    """长文里只出现一次的「有关」是规范用法，不提示。"""
    text = "有关要求如下。\n" + "各单位要抓好落实。\n" * 200
    assert "用语偏空泛：有关" not in _titles(text, "")


def test_vague_word_repeated_is_still_reported():
    text = ("进一步压实责任，进一步细化措施，进一步强化督导，确保任务落地。")
    assert "用语偏空泛：进一步" in _titles(text, "")


def test_repeat_phrase_needs_three_occurrences():
    """专名在一段内出现两次是规范要求（政府信息、伟大长征精神），不判贫乏。"""
    two = "政府信息是核心。政府信息要公开。"
    assert "重复用语" not in _titles(two, "")
    three = "政府信息是核心。政府信息要公开。政府信息要准确。"
    assert "重复用语" in _titles(three, "")


def test_repeat_phrase_in_title_exempt():
    """标题里的主题词全文统一使用，不算用语贫乏。"""
    text = ("关于印发政府信息公开工作年度报告格式的通知\n"
            "政府信息是核心内容。政府信息要依法公开。政府信息要准确及时。\n")
    assert "重复用语" not in _titles(text, "")


def test_repeat_phrase_not_glued_across_digits():
    """数字/标点隔开的汉字段不得拼接成假短语（v1.14 修 n-gram bug）。"""
    text = ("2026年8月30日中华人民共和国国务院令第123号。"
            "2026年8月30日中华人民共和国国务院令第456号。")
    titles = _titles(text, "")
    assert "重复用语" not in titles


def test_repeat_phrase_with_function_word_edges_skipped():
    """首尾虚词的半截搭配（「和国务院各部门」）不报。"""
    text = "和国务院各部门和国务院各部门和国务院各部门要协同配合。"
    assert "重复用语" not in _titles(text, "")


def test_exclamation_density_long_text_not_reported():
    """长文里 3 个感叹号（分段落）不是语气成灾。"""
    text = "甲！\n乙！\n丙！\n" + "各单位要抓好落实。\n" * 300
    assert "感叹号使用偏多" not in _titles(text, "")


def test_exclamation_density_short_text_reported():
    """短稿里 3 个感叹号密度极高，仍要提示（总量阈值提高后由密度线兜底）。"""
    assert "感叹号使用偏多" in _titles("要行动！要落实！要见效！", "")


def test_exclamation_flood_still_reported():
    text = "各单位要迅速行动" + "！各单位要压实责任" * 10 + "！"
    assert "感叹号使用偏多" in _titles(text, "")


def test_question_and_ellipsis_need_high_density():
    long_text = "什么是初心？\n什么是使命？\n为什么要奋斗？\n" + "我们要继续前进。\n" * 200
    assert "问号使用偏多" not in _titles(long_text, "")
    assert "问号使用偏多" in _titles("为什么？怎么办？如何做？", "")
    assert "省略号使用偏多" in _titles("一是……。二是……。三是……。", "")


def test_colloquial_inside_quotes_not_reported():
    """引语里的口语是原话，改它等于改别人的话。"""
    text = "“道路一定要搞清楚，一定要把真理本土化。”这是总书记的嘱托。"
    assert "口语化表达" not in _titles(text, "")


def test_single_char_colloquial_needs_two_occurrences():
    assert "口语化表达：搞" not in _titles("我们要搞建设。", "")
    assert "口语化表达：搞" in _titles("我们要搞建设，也要搞改革。", "")


def test_multi_char_colloquial_reported_once():
    """多字口语词（搞定/复盘）一次即报，不受单字门槛限制。"""
    assert "口语化表达：搞定" in _titles("这项工作要抓紧搞定。", "")


def test_layer_cross_parent_continuous_numbering_ok():
    """真实国办意见：二级序号跨一级标题连续编号（（一）～（四）｜（五）（六）｜（七））不报跳号。"""
    text = ("一、总体要求\n内容甲。\n"
            "二、强化供给\n（一）甲项。\n（二）乙项。\n（三）丙项。\n（四）丁项。\n"
            "三、深化融合\n（五）戊项。\n（六）己项。\n"
            "四、激发活力\n（七）庚项。\n")
    assert "层次序号" not in _titles(text, "")


def test_layer_real_jump_still_reported():
    text = "一、总体要求\n（一）甲项。\n（三）丙项。\n"
    assert "层次序号不规范：跳号" in _titles(text, "")


# ============================================================ 4. 阈值常量归位
def test_new_thresholds_live_in_writing_data():
    for name in ("CLOSING_ACCEPT", "PRINT_NOTICE_MARKS", "CLOSING_OPTIONAL_TAIL",
                 "VAGUE_MIN_COUNT", "VAGUE_DENSITY", "PHRASE_EDGE_BLOCK",
                 "COLLOQUIAL_SINGLE_MIN_COUNT", "PUNCT_DENSITY"):
        assert hasattr(W, name), f"阈值/词表应落在 writing_data：{name}"


def test_punct_thresholds_raised_with_evidence():
    assert W.PUNCT_EXCLAIM_TOTAL >= 8
    assert W.PUNCT_QUESTION_TOTAL >= 8
    assert W.PUNCT_ELLIPSIS_TOTAL >= 8
    assert W.PUNCT_DENSITY > 0
    assert W.PHRASE_REPEAT_MIN_COUNT >= 3
    assert W.VAGUE_MIN_COUNT >= 2


# ============================================================ 5. 语料冒烟（本地）
CORPUS = Path(__file__).resolve().parent.parent / "doc" / "corpus" / "real_all"


@pytest.mark.skipif(not CORPUS.is_dir(), reason="真实语料不在仓库内（CI 无此目录）")
def test_real_corpus_sample_has_no_warn():
    """真实语料抽样：规范公文不应出现任何 warn。"""
    files = sorted(CORPUS.glob("*.txt"))[:5]
    if not files:
        pytest.skip("语料目录为空")
    warns = []
    for p in files:
        text = p.read_text(encoding="utf-8-sig")
        kind = (WH.detect_kind(text, limit=1) or [("", 0)])[0][0]
        warns.extend((p.name, h.title) for h in WH.hints(text, kind)
                     if h.level == "warn")
    assert not warns, f"真实语料出现 warn 级提示：{warns}"


@pytest.mark.skipif(not (CORPUS / ".." / "all_texts").is_dir(),
                    reason="小样本语料不在仓库内")
def test_corpus_fingerprint_matches_doc():
    """校准文档里写的语料指纹必须与磁盘语料一致（防止"文档数字对不上语料"）。"""
    import hashlib

    import json
    calib = (Path(__file__).resolve().parent.parent / "doc" / "corpus"
             / "writing_calib_220" / "writing_calib_v1.14.json")
    if not calib.exists():
        pytest.skip("校准产物不存在（需先跑 scripts/writing_calibrate.py）")
    data = json.loads(calib.read_text(encoding="utf-8"))
    agg = hashlib.sha256()
    for p in sorted(CORPUS.glob("*.txt")):
        agg.update(f"{p.name}:{hashlib.sha256(p.read_bytes()).hexdigest()}\n"
                   .encode("utf-8"))
    assert len(list(CORPUS.glob("*.txt"))) == data["fingerprint"]["files"]
    assert os.path.getsize(str(CORPUS)) >= 0     # 目录可读
    assert agg.hexdigest() == data["fingerprint"]["sha256"]
