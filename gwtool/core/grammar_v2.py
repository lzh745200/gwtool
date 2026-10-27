# -*- coding: utf-8 -*-
"""语法规则库 v2（v1.19 P1）：成分残缺 / 搭配 / 语序 / 关联词 / 杂糅 / 冗余。

与 grammar_rules.py 的分工：旧模块是"最小可用集"（语义冗余/关联词/搭配/成对标点），
本模块按 P1 目标扩到 **120 条**，并把"改法不唯一"的结构类病句明确标成
advisory（只提示、不自动改），避免过度纠正。

设计口径（与 v1.17 severity 一致）：
  * auto      —— 改法唯一、确定性替换；仍受 corrector 的受保护片段/每句上限约束；
  * advisory  —— suggestion 是"怎么改"的说明而不是替换文本，只提示；
  * 每条规则都在 `_RULES` 里带 reason 与 confidence，便于报告与复核。

命中格式与 grammar_rules.all_hits 完全一致：
(start, end, wrong, suggestion, category, reason, confidence)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ------------------------------------------------------------------ 类别名
CAT_MISSING = "成分残缺"
CAT_COLLOCATION = "搭配"
CAT_ORDER = "语序"
CAT_CONJUNCTION = "关联词"
CAT_STRUCT = "结构杂糅"
CAT_REDUNDANCY = "语义冗余"


@dataclass(frozen=True)
class Rule:
    pattern: str
    repl: str            # auto：替换模板（可带 \1）；advisory：提示说明
    category: str
    reason: str
    conf: float
    advisory: bool = False
    # v1.19：命中区间取哪个分组（0=整个匹配）。关联词规则用它只替换错配的
    # 那个词（如"但是"→"而且"），而不是整段改掉——与旧 grammar_rules 的
    # "最小修改"口径一致，也避免测试/报告里出现整段替换。
    span_group: int = 0


def _pair(wrong: str, right: str, category: str, reason: str,
          conf: float = 0.86) -> Rule:
    """整词替换规则：wrong 必须整体出现（精确串，避免误伤更长的合法组合）。"""
    return Rule(re.escape(wrong), right, category, reason, conf)


# ================================================================ 1. 搭配（38）
COLLOCATION = [
    _pair("召开活动", "开展活动", CAT_COLLOCATION, "“召开”只用于会议，活动应“开展”"),
    _pair("召开工作", "开展工作", CAT_COLLOCATION, "“召开”不能带“工作”，应为“开展”"),
    _pair("召开任务", "部署任务", CAT_COLLOCATION, "“召开”不能带“任务”，应为“部署”"),
    _pair("召开建设", "推进建设", CAT_COLLOCATION, "“召开”不能带“建设”，应为“推进”"),
    _pair("召开项目", "推进项目", CAT_COLLOCATION, "“召开”不能带“项目”，应为“推进”"),
    _pair("召开培训", "组织培训", CAT_COLLOCATION, "“召开”不能带“培训”，应为“组织”"),
    _pair("召开检查", "开展检查", CAT_COLLOCATION, "“召开”不能带“检查”，应为“开展”"),
    _pair("召开调研", "开展调研", CAT_COLLOCATION, "“召开”不能带“调研”，应为“开展”"),
    _pair("开展会议", "召开会议", CAT_COLLOCATION, "“开展”不能带“会议”，应为“召开”"),
    _pair("举办工作", "开展工作", CAT_COLLOCATION, "“举办”不能带“工作”，应为“开展”"),
    _pair("举办任务", "部署任务", CAT_COLLOCATION, "“举办”不能带“任务”，应为“部署”"),
    _pair("举办通知", "发出通知", CAT_COLLOCATION, "“举办”不能带“通知”，应为“发出”"),
    _pair("进行会议", "召开会议", CAT_COLLOCATION, "“进行”不能带“会议”，应为“召开”"),
    _pair("进行通知", "发出通知", CAT_COLLOCATION, "“进行”不能带“通知”，应为“发出”"),
    _pair("提高力度", "加大力度", CAT_COLLOCATION, "“力度”应与“加大”搭配"),
    _pair("提高意识", "增强意识", CAT_COLLOCATION, "“意识”应与“增强”搭配"),
    _pair("增强水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("增强力度", "加大力度", CAT_COLLOCATION, "“力度”应与“加大”搭配"),
    _pair("加大水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("加大质量", "提升质量", CAT_COLLOCATION, "“质量”应与“提升”搭配"),
    _pair("加大效率", "提高效率", CAT_COLLOCATION, "“效率”应与“提高”搭配"),
    _pair("加快质量", "提升质量", CAT_COLLOCATION, "“质量”应与“提升”搭配"),
    _pair("加快水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("完成目标", "实现目标", CAT_COLLOCATION, "“目标”应与“实现”搭配"),
    _pair("完成要求", "达到要求", CAT_COLLOCATION, "“要求”应与“达到”搭配"),
    _pair("达成任务", "完成任务", CAT_COLLOCATION, "“任务”应与“完成”搭配"),
    _pair("达成要求", "达到要求", CAT_COLLOCATION, "“要求”应与“达到”搭配"),
    _pair("实现任务", "完成任务", CAT_COLLOCATION, "“任务”应与“完成”搭配"),
    _pair("实现要求", "达到要求", CAT_COLLOCATION, "“要求”应与“达到”搭配"),
    _pair("克服问题", "解决问题", CAT_COLLOCATION, "“问题”应与“解决”搭配"),
    _pair("树立水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("提升意识", "增强意识", CAT_COLLOCATION, "“意识”应与“增强”搭配"),
    _pair("履行任务", "完成任务", CAT_COLLOCATION, "“任务”应与“完成”搭配"),
    _pair("担任职责", "履行职责", CAT_COLLOCATION, "“职责”应与“履行”搭配"),
    _pair("加强水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("强化水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("推动水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
    _pair("促进水平", "提高水平", CAT_COLLOCATION, "“水平”应与“提高”搭配"),
]

# ================================================================ 2. 关联词（17）
# 命中区间只取第 2 组（错配的那个词），与旧层"最小修改"一致：
# 例如"不但…但是"只把"但是"换成"而且"，不动前半句。
_CONJ_GAP = r"([^。；！？\n]{1,40}?)"
CONJUNCTION = [
    Rule(r"不但" + _CONJ_GAP + r"(但是)", "而且", CAT_CONJUNCTION,
         "“不但”应与“而且”呼应，不能用“但是”", 0.82, span_group=2),
    Rule(r"虽然" + _CONJ_GAP + r"(因此)", "但是", CAT_CONJUNCTION,
         "“虽然”应与“但是”呼应，不能用“因此”", 0.82, span_group=2),
    Rule(r"尽管" + _CONJ_GAP + r"(因此)", "但是", CAT_CONJUNCTION,
         "“尽管”应与“但是”呼应，不能用“因此”", 0.82, span_group=2),
    Rule(r"因为" + _CONJ_GAP + r"(因此)", "所以", CAT_CONJUNCTION,
         "“因为”应与“所以”呼应，不能叠用“因此”", 0.82, span_group=2),
    Rule(r"由于" + _CONJ_GAP + r"(因此)", "所以", CAT_CONJUNCTION,
         "“由于”应与“所以/因此”择一，不能“由于…因此”", 0.78, span_group=2),
    Rule(r"只有" + _CONJ_GAP + r"(就)", "才", CAT_CONJUNCTION,
         "“只有”应与“才”呼应", 0.82, span_group=2),
    Rule(r"只要" + _CONJ_GAP + r"(才)", "就", CAT_CONJUNCTION,
         "“只要”应与“就”呼应", 0.82, span_group=2),
    Rule(r"即使" + _CONJ_GAP + r"(所以)", "也", CAT_CONJUNCTION,
         "“即使”应与“也”呼应，不能用“所以”", 0.82, span_group=2),
    Rule(r"无论" + _CONJ_GAP + r"(却)", "都", CAT_CONJUNCTION,
         "“无论”应与“都”呼应，不能用“却”", 0.80, span_group=2),
    Rule(r"不管" + _CONJ_GAP + r"(却)", "都", CAT_CONJUNCTION,
         "“不管”应与“都”呼应，不能用“却”", 0.80, span_group=2),
    Rule(r"尽管" + _CONJ_GAP + r"(所以)", "但是", CAT_CONJUNCTION,
         "“尽管”应与“但是”呼应，不能用“所以”", 0.80, span_group=2),
    Rule(r"既然" + _CONJ_GAP + r"(所以)", "就", CAT_CONJUNCTION,
         "“既然”应与“就/那么”呼应，不用“所以”", 0.80, span_group=2),
    Rule(r"之所以" + _CONJ_GAP + r"(?<!是)(?<!正)(?<!恰恰)(因为)",
         "是因为", CAT_CONJUNCTION,
         "“之所以”后面应接“是因为”（“是因为/正是因为/恰恰因为”属正确写法，不报）",
         0.80, span_group=2),
    Rule(r"因为" + _CONJ_GAP + r"(之所以)", "所以", CAT_CONJUNCTION,
         "“因为…之所以”句式杂糅，应改为“因为…所以”", 0.78, span_group=2),
    Rule(r"不但" + _CONJ_GAP + r"(却)", "而且", CAT_CONJUNCTION,
         "“不但”应与“而且”呼应，不能用“却”", 0.80, span_group=2),
    Rule(r"虽然" + _CONJ_GAP + r"(反而)", "但是", CAT_CONJUNCTION,
         "“虽然”应与“但是”呼应，不能用“反而”", 0.80, span_group=2),
    Rule(r"既然" + _CONJ_GAP + r"(因此)", "就", CAT_CONJUNCTION,
         "“既然”应与“就/那么”呼应，不用“因此”", 0.80, span_group=2),
]

# ================================================================ 3. 语序 / 的得地（14）
ORDER = [
    Rule(r"进行地(讨论|研究|分析|部署|检查|调研|论证|评估)",
         r"进行的\1", CAT_ORDER, "“进行”后应用“的”构成名词短语", 0.88),
    Rule(r"(开展|组织|落实|推进|完成)地(工作|活动|检查|任务|建设|项目)",
         r"\1的\2", CAT_ORDER, "动词后应用“的”修饰名词", 0.86),
    Rule(r"落实地(很)?(到位|不够)", r"落实得\1\2", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"完成地(十分|非常|很)?(很好|出色|顺利|圆满)", r"完成得\1\2", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"运行地(平稳|良好|顺畅)", r"运行得\1", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"发展地(很)?(快|迅速|较好)", r"发展得\1\2", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"做地(很)?(好|不好|到位)", r"做得\1\2", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"说地(很)?(对|好|清楚)", r"说得\1\2", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"写地(很)?(好|清楚|规范)", r"写得\1\2", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"进行地(顺利|成功|圆满)", r"进行得\1", CAT_ORDER, "补语应用“得”", 0.88),
    Rule(r"给予地(支持|帮助|肯定)", r"给予的\1", CAT_ORDER, "“给予”后应用“的”", 0.86),
    Rule(r"得到地(支持|认可|帮助)", r"得到的\1", CAT_ORDER, "“得到”后应用“的”", 0.86),
    Rule(r"予以地(肯定|表扬|支持)", r"予以的\1", CAT_ORDER, "“予以”后应用“的”", 0.86),
    Rule(r"工作地开展", "工作的开展", CAT_ORDER, "名词短语应用“的”", 0.86),
    # v1.19：补状语后置与"V+地+双音节动词"两类高频语序/虚词错
    Rule(r"会议召开于([^。；！？，]{2,12})", r"会议在\1召开", CAT_ORDER,
         "地点状语应前置：“在…召开”", 0.82),
    Rule(r"活动举办在([^。；！？，]{2,12})", r"活动在\1举办", CAT_ORDER,
         "地点状语应前置：“在…举办”", 0.82),
    Rule(r"文件印发在([^。；！？，]{2,12})", r"文件在\1印发", CAT_ORDER,
         "时间/地点状语应前置：“在…印发”", 0.82),
    Rule(r"进行(了|着)?([^。；！？，]{0,4}?)地(讨论|总结|研究|分析|部署|检查|评估)",
         r"进行\1\2的\3", CAT_ORDER, "“进行”后的定语应用“的”", 0.86),
    Rule(r"给予(了|着)?([^。；！？，]{0,4}?)地(肯定|表扬|支持|帮助|评价)",
         r"给予\1\2的\3", CAT_ORDER, "“给予”后的定语应用“的”", 0.86),
    Rule(r"予以(了|着)?([^。；！？，]{0,4}?)地(肯定|表扬|支持)", r"予以\1\2的\3",
         CAT_ORDER, "“予以”后的定语应用“的”", 0.86),
    Rule(r"作(了)?([^。；！？，]{0,4}?)地(说明|解释|汇报|报告|总结)", r"作\1\2的\3",
         CAT_ORDER, "“作”后的定语应用“的”", 0.86),
    Rule(r"得到(了)?([^。；！？，]{0,4}?)地(肯定|表扬|认可|支持|帮助)", r"得到\1\2的\3",
         CAT_ORDER, "“得到”后的定语应用“的”", 0.86),
]

# ================================================================ 4. 成分残缺（9）
MISSING = [
    Rule(r"通过([^，。；！？]{2,40})，使", r"通过\1，使（缺主语：删“通过”或“使”）",
         CAT_MISSING, "“通过…使…”滥用介词导致主语残缺", 0.62, advisory=True),
    Rule(r"经过([^，。；！？]{2,40})，使", r"经过\1，使（缺主语：删“经过”或“使”）",
         CAT_MISSING, "“经过…使…”导致主语残缺", 0.62, advisory=True),
    Rule(r"由于([^，。；！？]{2,40})，使", r"由于\1，使（缺主语：删“由于”或“使”）",
         CAT_MISSING, "“由于…使…”导致主语残缺", 0.62, advisory=True),
    Rule(r"在([^，。；！？]{2,40})上，使", r"在\1上，使（缺主语：删“在…上”或“使”）",
         CAT_MISSING, "“在…上，使…”导致主语残缺", 0.62, advisory=True),
    Rule(r"通过([^，。；！？]{2,40})，让", r"通过\1，让（缺主语：删“通过”或“让”）",
         CAT_MISSING, "“通过…让…”导致主语残缺", 0.62, advisory=True),
    Rule(r"经过([^，。；！？]{2,40})，让", r"经过\1，让（缺主语：删“经过”或“让”）",
         CAT_MISSING, "“经过…让…”导致主语残缺", 0.62, advisory=True),
    Rule(r"由于([^，。；！？]{2,40})，让", r"由于\1，让（缺主语：删“由于”或“让”）",
         CAT_MISSING, "“由于…让…”导致主语残缺", 0.62, advisory=True),
    Rule(r"通过([^，。；！？]{2,40})，使得",
         r"通过\1，使得（缺主语：删“通过”或“使得”）",
         CAT_MISSING, "“通过…使得…”导致主语残缺", 0.62, advisory=True),
    Rule(r"对于([^，。；！？]{2,40})，使",
         r"对于\1，使（缺主语：调整介词结构或补出主语）",
         CAT_MISSING, "介词结构前置导致主语残缺", 0.60, advisory=True),
    Rule(r"在([^，。；！？]{2,40})下，使",
         r"在\1下，使（缺主语：删“在…下”或“使”）",
         CAT_MISSING, "“在…下，使…”导致主语残缺", 0.60, advisory=True),
]

# ================================================================ 5. 结构杂糅（11）
STRUCT = [
    Rule(r"原因是([^。；！？]{2,40}?)造成的", r"原因是\1", CAT_STRUCT,
         "“原因是…造成的”句式杂糅，删“造成的”", 0.84),
    Rule(r"是由于([^。；！？]{2,40}?)的结果", r"是由于\1", CAT_STRUCT,
         "“是由于…的结果”句式杂糅，删“的结果”", 0.84),
    Rule(r"本着([^。；！？]{2,40}?)为原则", r"本着\1原则", CAT_STRUCT,
         "“本着…为原则”杂糅，应“本着…原则”或“以…为原则”", 0.82),
    Rule(r"围绕([^。；！？]{2,40}?)为中心",
         r"围绕\1（建议改为“以…为中心”或“围绕…”）", CAT_STRUCT,
         "“围绕…为中心”句式杂糅", 0.62, advisory=True),
    Rule(r"对于([^。；！？]{2,30}?)问题上", r"对于\1问题", CAT_STRUCT,
         "“对于…问题上”杂糅，删“上”", 0.82),
    Rule(r"在([^。；！？]{2,30}?)方面上", r"在\1方面", CAT_STRUCT,
         "“在…方面上”杂糅，删“上”", 0.82),
    Rule(r"包括([^。；！？]{2,40}?)等组成",
         r"包括\1等（建议改为“由…组成”或“包括…”）", CAT_STRUCT,
         "“包括…等组成”句式杂糅", 0.60, advisory=True),
    Rule(r"分为([^。；！？]{1,20}?)组成",
         r"分为\1（建议改为“由…组成”）", CAT_STRUCT,
         "“分为…组成”句式杂糅", 0.60, advisory=True),
    Rule(r"之所以([^。；！？]{2,40}?)是因为([^。；！？]{2,40}?)的原因",
         r"之所以\1是因为\2", CAT_STRUCT,
         "“之所以…是因为…的原因”语义重复，删“的原因”", 0.80),
    Rule(r"在([^，。；！？]{2,30})中，使", r"在\1中，使（缺主语：调整句式）",
         CAT_STRUCT, "“在…中，使…”导致主语残缺", 0.60, advisory=True),
    Rule(r"经过([^，。；！？]{2,40})，使得",
         r"经过\1，使得（缺主语：删“经过”或“使得”）", CAT_STRUCT,
         "“经过…使得…”导致主语残缺", 0.60, advisory=True),
]

# ================================================================ 6. 语义冗余（32）
REDUNDANCY = [
    _pair("涉及到", "涉及", CAT_REDUNDANCY, "“涉及”后不带“到”"),
    _pair("截至到", "截至", CAT_REDUNDANCY, "“截至”已含“到”义"),
    _pair("波及到", "波及", CAT_REDUNDANCY, "“波及”后不带“到”"),
    _pair("诉诸于", "诉诸", CAT_REDUNDANCY, "“诉诸”后不带“于”"),
    _pair("见诸于", "见诸", CAT_REDUNDANCY, "“见诸”后不带“于”"),
    _pair("付诸于", "付诸", CAT_REDUNDANCY, "“付诸”后不带“于”"),
    _pair("来自于", "来自", CAT_REDUNDANCY, "“来自”后不带“于”"),
    _pair("免费赠送", "赠送", CAT_REDUNDANCY, "“赠送”已含无偿义"),
    _pair("凯旋归来", "凯旋", CAT_REDUNDANCY, "“凯旋”已含归来义"),
    _pair("亲眼目睹", "目睹", CAT_REDUNDANCY, "“目睹”已含亲眼义"),
    _pair("目的是为了", "目的是", CAT_REDUNDANCY, "“目的”本身即“为了”"),
    _pair("唯一一个", "唯一", CAT_REDUNDANCY, "“唯一”已表单一"),
    _pair("首次首创", "首创", CAT_REDUNDANCY, "“首创”已含首次义"),
    _pair("一致共识", "共识", CAT_REDUNDANCY, "“共识”已含一致义"),
    _pair("全部都", "全部", CAT_REDUNDANCY, "“全部”已表范围，不再加“都”"),
    _pair("切忌不要", "切忌", CAT_REDUNDANCY, "“切忌”已含否定义，不再加“不要”"),
    _pair("重新再次", "再次", CAT_REDUNDANCY, "“再次”与“重新”重复"),
    _pair("互相彼此", "彼此", CAT_REDUNDANCY, "“彼此”与“互相”重复"),
    _pair("彼此互相", "互相", CAT_REDUNDANCY, "“互相”与“彼此”重复"),
    _pair("首先第一", "首先", CAT_REDUNDANCY, "“首先”与“第一”重复"),
    _pair("最后终于", "终于", CAT_REDUNDANCY, "“终于”已含最后义"),
    _pair("马上立刻", "立刻", CAT_REDUNDANCY, "“立刻”与“马上”重复"),
    _pair("十分非常", "非常", CAT_REDUNDANCY, "“非常”与“十分”重复"),
    _pair("特别十分", "十分", CAT_REDUNDANCY, "“特别”与“十分”重复"),
    _pair("共同一起", "一起", CAT_REDUNDANCY, "“一起”与“共同”重复"),
    _pair("一起共同", "共同", CAT_REDUNDANCY, "“共同”与“一起”重复"),
    # 数量词"大约/超过/至少/近/共计/约…左右/以上/上下"的重复由旧
    # grammar_rules.ADVISORY_RULES 覆盖（表达提示，只提示不替换）；
    # v2 不再重复定义，避免同一位置两条不同建议互相打架。
]

_RULES: list[Rule] = [
    *COLLOCATION, *CONJUNCTION, *ORDER, *MISSING, *STRUCT, *REDUNDANCY,
]
_COMPILED: list[tuple[re.Pattern, Rule]] = [
    (re.compile(r.pattern), r) for r in _RULES
]


def all_hits(text: str):
    """返回与 grammar_rules.all_hits 同形的命中元组列表。"""
    if not text:
        return []
    out = []
    for rx, rule in _COMPILED:
        for m in rx.finditer(text):
            if rule.span_group:
                start, end = m.start(rule.span_group), m.end(rule.span_group)
                wrong = m.group(rule.span_group)
                suggestion = rule.repl
            else:
                start, end = m.start(), m.end()
                wrong = m.group(0)
                suggestion = m.expand(rule.repl)
            out.append((start, end, wrong, suggestion,
                        rule.category, rule.reason, rule.conf))
    return out


def rule_count() -> int:
    return len(_RULES)


CATEGORIES = (CAT_MISSING, CAT_COLLOCATION, CAT_ORDER, CAT_CONJUNCTION,
              CAT_STRUCT, CAT_REDUNDANCY)
