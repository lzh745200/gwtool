# -*- coding: utf-8 -*-
"""文字纠错引擎：三级流水线。

  ① 精确匹配：error_pairs（含精标对、生成的混淆对、用户自定义对）最长优先；
  ② 上下文规则：同词不同境（截止/截至、爆发/暴发）；
  ③ 标点/数字规则（GB/T 15835）。

输出 Correction 列表：不重叠、按位置排序；同名命中按置信度取最高。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import grammar_rules
from .corrector_data import (CONTEXT_RULES, CURATED_PAIRS, NEGATIVE_CONTEXTS,
                             NUMBER_RULES, ORG_RENAME_PAIRS, PUNCT_RULES)
from ..db import dao

_RULES_CACHE: dict = {}

# L1 精确词表命中的 reason 标识（也是"精标长对可吃掉子规则"判据的锚点）
_REASON_LEXICON = "词库匹配"

# 提示类类别：其 suggestion 是**提示标签**而不是替换文本，
# 批量写回 / 整篇应用必须整类排除，否则会把正文改成标签本身。
# 单一事实来源 —— batch、correct_dialog、reference_panel 一律 import 这里，
# 不再各自维护一份清单（两份清单手工同步必然漂移，本项目已两次踩过）。
ADVISORY_CATEGORIES: tuple[str, ...] = (
    "数字用法",      # 阿拉伯数字年份/百分数等规范提示
    grammar_rules.CAT_ADVISORY,     # 表达提示（跨词冗余）
    grammar_rules.CAT_PUNCT_PAIR,   # 标点规范（成对符号/引号形式）
)


@dataclass
class Correction:
    start: int
    end: int
    wrong: str
    suggestion: str
    category: str      # 错别字 / 易混词 / 禁用词 / 标点 / 数字用法 /
                       # 用户词库 / 神经纠错 / 语法纠错
    reason: str
    confidence: float
    # 改动类型：replace（替换，默认）/ insert（增字）/ delete（删字）。
    # 默认值保证历史构造点零改动；L5 Seq2Seq 层需用它区分变长输出。
    kind: str = "replace"

    @property
    def label(self) -> str:
        return f"{self.wrong} → {self.suggestion}"


def _is_valid_pair(wrong: str, correct: str) -> bool:
    """加载端防御：过滤同词、空串、含非中文噪声。"""
    if not wrong or not correct or wrong == correct:
        return False
    if not re.search(r"[\u4e00-\u9fff]", wrong):
        return False
    if any(ch in wrong for ch in "\n\r\t "):
        return False
    return True


def _load_patterns() -> dict[str, tuple[str, str, float]]:
    """wrong -> (correct, category, confidence)；用户自定义优先。"""
    if "patterns" in _RULES_CACHE:
        return _RULES_CACHE["patterns"]
    patterns: dict[str, tuple[str, str, float]] = {}
    # 内置精标
    for wrong, correct, cat, conf in CURATED_PAIRS:
        if conf <= 0 or not _is_valid_pair(wrong, correct):
            continue
        if _is_self_contained(wrong, correct):
            continue
        old = patterns.get(wrong)
        if old is None or conf > old[2]:
            patterns[wrong] = (correct, cat, conf)
    # 数据库（生成数据 + 用户添加，用户添加 source=user 置信度高）
    try:
        for p in dao.all_error_pairs():
            if not _is_valid_pair(p.wrong, p.correct) or not p.enabled:
                continue
            # `source == "curated"` 的库内记录是**构建期对内置精标表的快照**
            # （seed.db 生成时写进去的 220 条）。内置文件才是唯一事实来源：
            # 早先的实现把快照也当数据加载，于是
            #   · 内置表里**已删除**的错对（行使权利→行使权力、哑巴→听障人士）
            #     会从快照"复活"；
            #   · 内置表里**已修订**的类别/置信度（异形词降为"规范用字 0.7"）
            #     会被快照里更高置信的旧值盖回去。
            # 这两点在真实用户机上都会生效（首启动即导入 seed.db），单测却因为
            # 用空库看不到 —— 属"内置数据改了但线上不变"的隐性缺陷。
            # 与 README 既有口径一致：程序内置的人工精标对始终生效、不受规则集开关影响。
            if p.source == "curated":
                continue
            if _is_self_contained(p.wrong, p.correct):
                continue
            conf = p.confidence if p.source != "user" else max(p.confidence, 0.99)
            old = patterns.get(p.wrong)
            # 同置信度时按正形字典序取小（v1.8）：同一错形若有多个正形（用户
            # 从多份词表导入、或历史数据重复），"显示哪一条"原来取决于库内
            # 行序，同一份词库在两台机器上可能给出不同建议 —— 不可复现。
            # 种子库已在构建期按错形去重（见 scripts/seed_data.py），
            # 这里再兜一层，保证任何来源的数据都给出确定性的建议。
            if old is None or conf > old[2] or (conf == old[2] and p.correct < old[0]):
                patterns[p.wrong] = (p.correct, p.category or "错别字", conf)
    except Exception as exc:
        # 数据库不可用时退化为纯内置数据 —— 降级本身是设计，但必须留痕：
        # 零日志会让「自定义词库没生效」完全不可见（用户只会觉得纠错变弱）。
        from .. import logs
        logs.get_logger("corrector").warning(
            "纠错词库加载失败，本次退化为内置数据：%s", exc)
    # 机构沿革对照（提示级，建议核实后替换）
    for wrong, correct in ORG_RENAME_PAIRS:
        if _is_self_contained(wrong, correct):
            continue
        old = patterns.get(wrong)
        if old is None or 0.7 > old[2]:
            patterns[wrong] = (correct, "机构沿革", 0.7)
    _RULES_CACHE["patterns"] = patterns
    # 持久忽略名单（用户确认无需提示的词）
    try:
        _RULES_CACHE["ignore"] = dao.all_ignore_words()
    except Exception as exc:
        from .. import logs
        logs.get_logger("corrector").warning(
            "忽略名单加载失败，退化为空集：%s", exc)
        _RULES_CACHE["ignore"] = set()
    return patterns


def _is_self_contained(wrong: str, correct: str) -> bool:
    """错形是不是正形的**子串**（自包含对，加载时丢弃）。

    审计 P1-1：机构沿革表里 `旅游局 → 文化和旅游局`、`建设局 → 住房和城乡建设局`
    这类映射中，正形**包含**错形。子串匹配会在**已经正确的现行机构名**上再次命中：

        市文化和旅游局 → 市文化和文化和旅游局
        住房和城乡建设部 → 住房和城乡住房和城乡建设部

    而 `correct_block` / 参考面板「全部替换」会把它写回正文 —— 直接改坏正确名称。
    这类映射在词表法里没有可靠的正向写法（无法区分"旧称单独出现"与"新称内部
    子串"），按项目红线「宁少报不误报」在加载端整条丢弃；旧称的提示能力由
    `core.consistency._check_orgs`（全文新旧并存才提示）承担。
    """
    return bool(wrong) and wrong in correct


def invalidate_cache() -> None:
    """用户增删词库后调用，使缓存失效。"""
    _RULES_CACHE.clear()
    try:                       # 顺带失效重复字层的词典派生缓存
        from . import repeat_rules
        repeat_rules.invalidate_cache()
    except Exception:
        pass
    try:                       # 顺带失效神经层的开关缓存（默认关闭时零成本）
        from . import csc_gec, csc_neural
        csc_neural.invalidate_cache()
        csc_gec.invalidate_cache()
    except Exception:
        pass


# ------------------------------------------------------------------ 引擎
def check_text(text: str) -> list[Correction]:
    if not text:
        return []
    found: list[Correction] = []
    found.extend(_check_exact(text))
    found.extend(_check_regex_group(text, CONTEXT_RULES, "易混词"))
    found.extend(_check_regex_group(text, PUNCT_RULES, "标点"))
    found.extend(_check_regex_group(text, NUMBER_RULES, "数字用法"))
    found.extend(_check_grammar(text))                    # L2.5 语法与表达规则层
    found.extend(_check_repeat(text))                     # L3 重复字（同字连）
    found = _apply_neural_layer(text, _dedupe(found))     # L4 单字替换精排
    return _apply_gec_layer(text, found)                  # L5 语法纠错


def _check_grammar(text: str) -> list[Correction]:
    """L2.5 语法与表达规则层：语义冗余 / 关联词搭配 / 搭配不当 / 成对标点。

    与 L2 的分工：L2 处理"同一个词的两种写法看语境"，本层处理**不在词表里**的
    多字赘余、关联词错配、动宾搭配错位、成对符号落单 —— 这三类是词表法
    结构上够不着的区间（对应《纠错系统差距分析》的"多字少字/词序/语法"）。

    异常一律咽掉：规则数据损坏、正则编译失败，都只丢本层，绝不拖垮主流程。
    """
    try:
        return [Correction(s, e, w, sug, cat, reason, conf)
                for s, e, w, sug, cat, reason, conf
                in grammar_rules.all_hits(text)]
    except Exception:
        return []


def _check_repeat(text: str) -> list[Correction]:
    """L3 重复字检测：延迟导入，模块级零成本，异常一律咽掉不拖垮主流程。"""
    try:
        from .repeat_rules import check_repeat
        return check_repeat(text)
    except Exception:
        return []


def _apply_neural_layer(text: str,
                        corrections: list[Correction]) -> list[Correction]:
    """可选第 4 级：神经网络精排（默认关闭，需导入增强包并手动开启）。

    设计约束：
      - 未安装/未开启时**零成本**：`enabled()` 只做一次内存布尔判断；
      - L4 只补前三层没有覆盖的位置，输出同样是 `Correction`，
        所以 UI 层（纠错面板 / 任意文档纠错 / 标记视图）无需任何改动；
      - **异常一律咽掉**：增强包损坏、依赖缺失、推理报错，都只回落三级
        流水线，绝不让纠错主流程失败。
    """
    try:
        from . import csc_neural
        if not csc_neural.enabled():
            return corrections
        extra = csc_neural.enhance(text, corrections)
        if not extra:
            return corrections
        return _dedupe(list(corrections) + list(extra))
    except Exception:
        return corrections


def _apply_gec_layer(text: str,
                     corrections: list[Correction]) -> list[Correction]:
    """可选第 5 级：Seq2Seq 语法纠错（默认关闭，需导入 cgec 增强包并开启）。

    与 L4 的分工：
      - L4 是「单字替换」分类器，逐位判定，补前三层漏掉的错字；
      - L5 是「变长输出」生成模型，能处理增字/删字/改序类语病。
    因此 L5 在 L4 之后运行，其输出仍挂在 `occupied` 之外——L4 优先、L5 补漏，
    同一位置不会双报。异常同样一律咽掉，未开启时只做一次内存布尔判断。
    """
    try:
        from . import csc_gec
        if not csc_gec.enabled():
            return corrections
        extra = csc_gec.enhance(text, corrections)
        if not extra:
            return corrections
        return _dedupe(list(corrections) + list(extra))
    except Exception:
        return corrections


def _buckets() -> dict[str, list[str]]:
    """首字 -> 词列表 的匹配桶（最长优先），随词库缓存避免每次 4 万条重建。"""
    buckets = _RULES_CACHE.get("buckets")
    if buckets is None:
        patterns = _load_patterns()
        buckets = {}
        for w in patterns:
            buckets.setdefault(w[0], []).append(w)
        for first in buckets:
            buckets[first].sort(key=len, reverse=True)  # 最长优先
        _RULES_CACHE["buckets"] = buckets
    return buckets


# 负向上下文正则：模块级缓存（NEGATIVE_CONTEXTS 是静态数据，编译一次反复用；
# 原先在 _check_exact 内局部 dict，每次调用都要重建，注释声明的“预编译”未兑现）
_NEG_RX_CACHE: dict[str, list] = {}


def _check_exact(text: str) -> list[Correction]:
    patterns = _load_patterns()
    ignore = _RULES_CACHE.get("ignore") or set()
    if not patterns:
        return []
    by_first = _buckets()
    raw: list[tuple[int, int, str, str, str, float]] = []
    i, n = 0, len(text)
    # 书名号前缀计数：O(n) 预计算，命中处 O(1) 判定（原为每命中 O(n) 全扫）
    q_open = q_close = 0   # text[:i] 中的书名号计数（随 i 推进更新）
    while i < n:
        cands = by_first.get(text[i])
        if cands:
            hit = None
            for w in cands:
                if text.startswith(w, i):
                    hit = w
                    break
            if hit:
                if hit in ignore:
                    i += len(hit)
                    continue
                correct, cat, conf = patterns[hit]
                # 负向上下文：检查命中词之前的文本（窗口 120 字足够——
                # 模式均为紧邻短语；原实现对全前缀 re.search，O(命中×文本长)）
                neg = _NEG_RX_CACHE.get(hit)
                if neg is None:
                    neg = _NEG_RX_CACHE[hit] = [re.compile(rx) for rx
                                                in NEGATIVE_CONTEXTS.get(hit, [])]
                if any(rx.search(text[max(0, i - 120):i]) for rx in neg):
                    i += 1
                    continue
                # 书名号内的文件标题按原文引用，不作纠错提示
                if q_open > q_close:
                    i += len(hit)
                    continue
                # 机构沿革：历史语境不改（审计 P2-12）。`1953年，卫生部`、
                # `原卫生部` 这类引述改成现名属把对的改错 —— 机构沿革只应是
                # "新稿里误用了旧名"的提示，不是历史改写器。
                if cat == "机构沿革" and _looks_historical(text, i):
                    i += len(hit)
                    continue
                raw.append((i, i + len(hit), hit, correct, cat, conf))
                # 前进 **1 个字**而不是 len(hit)（v1.8，深审 P1 附带修复）：
                # 原来一跳就跳过整个命中，于是"起点更早的短命中"会把**落在它内部
                # 的长命中整个吞掉**。实测：`文中出现了戒骄戒燥` 里生成对
                # `了戒→了解`（0.55）从下标 4 命中并把指针推到 6，
                # 4 字精标对 `戒骄戒燥`（0.9，起点 5）**根本不会被扫描到**。
                # 逐字前进让两个起点都进入候选，重叠按（置信度，长度）由
                # `_dedupe` 仲裁 —— 0.9 的长对胜出，语义与既有设计一致。
                i += 1
                continue
        if text[i] == "《":
            q_open += 1
        elif text[i] == "》":
            q_close += 1
        i += 1
    return _suppress_inside_common_words(text, raw)


_HIST_YEAR_RX = re.compile(r"(?:19|20)\d{2}\s*年")


def _looks_historical(text: str, i: int) -> bool:
    """命中处是否处于"历史引述"语境（机构沿革专用，审计 P2-12）。

    只取两种**确定性**信号，不做语义猜测：
      · 命中前 12 字内出现四位年份 —— `1953年，卫生部`、`1998年组建的国家旅游局`；
      · 紧邻前缀是「原 / 前 / 撤销」—— `原卫生部`、`前国家旅游局`。
    命中则不改：把历史引述里的旧机构名改成现名，会把本来正确的引述改错。
    """
    head = text[max(0, i - 12):i]
    if _HIST_YEAR_RX.search(head):
        return True
    return head.rstrip().endswith(("原", "前", "撤销"))


def _suppress_inside_common_words(
        text: str, raw: list[tuple[int, int, str, str, str, float]]) -> list[Correction]:
    """词边界保护：命中片段两端都不落在分词边界上（即横跨多个常用词的内部），
    判定为词内误报并抑制。例：'安全生产'（分词为 安全|生产）内的 '全生'
    不应报 '全生→全省'；而独立成词的 '布署' 两端均为边界，正常报出。

    低置信命中（如生成的混淆对 0.55）执行更严的门控：
      1) 两端都必须落在分词边界（原规则只要求"非双内"）——
         拦截跨词命中（如 '全厂生产' 分词 全厂|生产，'厂生' 右端在边界）；
      2) 命中不得严格位于任何分词 token 内部；
      3) 追加防线（v1.5.1）：命中若由**≥2 个高频常用字**组成
         （如 这是/没又/住要 —— 生成器的"同音错形"恰好落在常用字上），
         它在任何正常行文中随处可见，属结构性误报，一律抑制。
         实测 seed.db 的 4 万条生成对中有 925 条属此类。
         判据刻意用**逐字词频**而非分词结果：jieba 会把"这是"整体并成
         一个词、把"请驶用"并成一个词，依赖分词边界并不可靠。
      4) v1.8 新增四道**与分词无关**的结构性闸门（与上面的 strict 门控并列，
         且同样只对 conf < _LOW_CONF 的生成层命中生效 —— 精标对、用户词条、
         规则层的 confidence 均 ≥0.7，一律不受影响）：
         a. **错形本身是已知合法词 → 不报**。生成层是按同音/近音程序化配对的，
            "错形"若本身是 jieba 词表/随包 CC-CEDICT 词典/用户术语里的合法词形，
            属"同音异形真词"歧义。真实语料上的表现是 `显式→显示`、`一键→一件`、
            `重试→重视`、`一价→一家`、`上疏→上述` 这类**把正确用词改成另一个词**
            的误报（改完语义就变了）。无模型条件下无法判定该处究竟是哪个词，
            按项目红线「宁少报不误报」直接不报；这些真实混淆仍由精标对、上下文
            规则、用户词库覆盖。
            实测代价：4 万条生成对里约 2.5% 不再参与召回（E2 约 -1.4pp），
            收益：仓库 doc/ 真实长文语料上低置信误报 110 处 → 19 处（0.52/万字）；
            词典全量探针里"合法词被判错"从 133 个降到 79 个（余下全是**本该报**的
            变体/旧称/禁用词）。
         b. **命中是更长词典词的一部分 → 不报**。若 `一矢` 落在词典词 `一矢中的`
            里，改它就是把词拆坏（与 repeat_rules 的"更长词覆盖即放行"同源）。
         c. **同一文档内重复出现 ≥_DOC_REPEAT_MIN 次 → 不报**。同一篇材料里
            反复出现的词形是作者的术语选择（新词、简称、单位内部提法），
            不是笔误；真实笔误一般只出现在一处。该闸门不依赖外部词典，
            正好补上 a/b 在"词表外新词"上的空档。用户把真错加进词库后
            置信度升到 0.99，即永久生效（逃生通道不变）。
         d. 上述 a/b/c 三条都**不属于**"高置信短路"：`_CURATED_WRONG` 里的
            人工精标错形永不进入它们（否则 `布署`、`帐目` 这些被词典收录的
            真错别字反而会被放行）。
    """
    boundaries: set[int] = set()
    inside_mask = bytearray(len(text) + 2)   # 位置 -> 是否严格位于某 token 内部
    has_tokens = False
    # 分词不可用（jieba 未装/词典损坏/运行期异常）时为 None：门控整体**降级为不过滤**。
    # 注意不能退化成空集 —— 空集会让 both_ends 恒为 False，把低置信命中全部吞掉，
    # 与"宁少报不误报的过滤器"这一设计意图正好相反（漏报比误报更不可接受）。
    tokenized: bool | None = None
    if raw:
        try:
            import jieba
            from ..db.tokenize import _ensure_jieba
            _ensure_jieba()
            for _w, a, b in jieba.tokenize(text):
                boundaries.add(a)
                boundaries.add(b)
                for k in range(a + 1, b):
                    inside_mask[k] = 1
                has_tokens = True
            tokenized = has_tokens
        except Exception:
            tokenized = None
    out: list[Correction] = []
    for s, e, hit, correct, cat, conf in raw:
        if conf < _LOW_CONF:
            # 生成层四道**与分词无关**的结构性闸门，必须排在分词分支之前：
            # 分词不可用时它们照样生效（降低误报的防线不该随分词一起降级）。
            # 人工精标错形永远豁免：它们是被人工确认过的"真错"（如 `布署`），
            # 不能因为恰好被词典收录就被当成"同音异形歧义"放行。
            if hit not in _CURATED_WRONG:
                if _is_known_word(hit):
                    continue   # 错形本身是词典词 -> 同音异形歧义，不报
                if _covered_by_known_word(text, s, e):
                    continue   # 命中是更长词典词的一部分 -> 词被拆坏，不报
                if text.count(hit) >= _DOC_REPEAT_MIN:
                    continue   # 文档内重复出现 -> 作者的术语选择，不报
            if tokenized is None:
                # 无分词信息：跳过词边界门控，只保留确定性的结构规则
                if _is_common_char_sequence(hit):
                    continue
            else:
                both_ends = s in boundaries and e in boundaries
                inside = bool(inside_mask[s])
                if not (both_ends and not inside):
                    continue  # 低置信：跨词/词内命中 -> 误报
                if _is_common_char_sequence(hit):
                    continue  # 逐字皆为高频常用字 -> 结构性误报
        elif tokenized is not None:
            if len(hit) <= 2:
                # 两字高置信精标对也必须**两端都落在分词边界**（v1.8，审计 P1-3）。
                # 原规则只要求"两端不同时在词内"，于是正常词的跨接会被报出：
                #   `按排放标准`（按|排放|标准）-> `按排→安排`
                #   `按装机容量`（按|装机|容量）-> `按装→安装`
                #   `虽然烧了`（虽然|烧|了）    -> `然烧→燃烧`
                #   `防害虫`（防|害虫）         -> `防害→妨害`
                # 这些都是"前一词的尾字 + 后一词的首字"，conf 0.9+ 默认阈值必中。
                # 四字及以上不受影响（成语错形不可能由正常词跨接产生），
                # 故只对两字条目收紧，避免误伤长对召回。
                if not (s in boundaries and e in boundaries):
                    continue
            elif boundaries and s not in boundaries and e not in boundaries:
                continue  # 两端均在词内部 -> 误报
        out.append(Correction(s, e, hit, correct, cat, "词库匹配", conf))
    return out


_LOW_CONF = 0.7            # 低于此置信度走严格门控
# “高频常用字”门槛（jieba 单字词频）。取值依据实测权衡（语料：本仓库 2331 段
# /5.2 万字正常中文；样本：seed.db 4 万条生成对随机 600 条）：
#     门槛      生成层召回    每万字误报命中
#     关闭       85.3%        26.15
#     5000       70.3%         8.84   <- 采用
#     20000      82.5%        13.84
# 取 5000 的理由：公文篇幅长、以"零误报"为红线，宁可少报也不要在每 300 字
# 就弹一个假建议。**逃生通道**：用户手动添加的词条置信度会被抬到 0.99，
# 永不进入本门控——任何被误抑制的真错，用户加一次即永久生效。
_COMMON_CHAR_FREQ = 5000

# v1.8：生成层（conf < _LOW_CONF）的"同一文档内重复出现"豁免门槛。
# 依据：同一篇材料里同一词形出现 ≥2 次，几乎只能是作者的术语选择（新词/简称/
# 单位内部提法）；真实笔误一般只出现在一处。取 2 而非更高，是因为真实语料里
# `一键`、`显式` 这类词往往一篇出现 2~3 次，门槛再高就漏掉主要收益。
# 逃生通道与上面一致：用户把真错加进词库后置信度 0.99，不吃这道闸门。
_DOC_REPEAT_MIN = 2

# 人工精标错形集合（v1.8）：生成层两道新闸门的**豁免名单**。
# 为什么需要：jieba 词典本身收了一批"别写"（如 `布署`、`帐目`），
# 若一律按"错形是词典词 -> 不报"处理，会把精标层里真正要报的错别字吃掉。
# 判据用**词形**而非置信度——这样 `test_bugfix_regression` 里以 0.55 构造的
# `布署` 原始命中同样不会被误伤，语义稳定、不依赖调用方传的 conf。
_CURATED_WRONG: frozenset[str] = frozenset(
    w for w, _c, _cat, conf in CURATED_PAIRS if conf > 0)


def _is_common_char_sequence(hit: str) -> bool:
    """命中是否由 ≥2 个高频常用字逐一组成（结构性误报的特征）。

    例：'这是'(这 261791 / 是 796991)、'没又'、'住要' → True；
        '驶用'(驶 422) → False —— 真错别字得以保留。
    """
    if len(hit) < 2:
        return False
    return all(jieba_freq(ch) >= _COMMON_CHAR_FREQ for ch in hit)


def jieba_freq(word: str) -> int:
    try:
        import jieba
        return int(jieba.dt.FREQ.get(word, 0))
    except Exception:
        return 0


def _known_word_set() -> frozenset[str]:
    """已知词形集合（jieba 判据之外的词典/术语证据），懒加载并缓存。

    词典加载走 DAO 的只读接口并缓存在 `_RULES_CACHE`（`invalidate_cache()` 会清），
    异常（库不可用）降级为空集 —— 只退化为 jieba 判据，不影响主流程。
    """
    cached = _RULES_CACHE.get("known_words")
    if cached is not None:
        return cached
    words: set[str] = set()
    try:
        from ..db import dao
        words.update(dao.builtin_dictionary_words())
        words.update(dao.user_dictionary_words())
    except Exception:
        words = set()
    cached = _RULES_CACHE["known_words"] = frozenset(words)
    return cached


def _is_known_word(hit: str) -> bool:
    """错形本身是不是**已知的合法词形**（词典词或用户术语）。

    两道判据取并集：
      · jieba 词频 > 0 —— 1990 年代口径的通用词表，覆盖面窄但无需加载；
      · 随包 CC-CEDICT 词典（12 万词）或用户导入的行业术语 —— 覆盖面宽得多，
        能兜住 `一价`、`一哥`、`上疏`、`可恃` 这类"词频表里没有、但确实是
        合法词/专名"的错形。实测词典全量探针里，被误判的词典词从 133 降到 92，
        剩下的主要是**本该报**的变体/旧称/禁用词。
    任一命中即视为"同音异形真词"，生成层不对它报错。
    """
    if jieba_freq(hit) > 0:
        return True
    return hit in _known_word_set()


# 词典最长词 8 字（与 repeat_rules._MAX_WIN 同口径）：判断"命中是否被更长的
# 词典词覆盖"时的窗口上限。
_MAX_WORD_WIN = 8


def _covered_by_known_word(text: str, s: int, e: int) -> bool:
    """命中区间是否被某个**更长的**词典词完整覆盖。

    与 repeat_rules 的 ② 规则同源：若 `一矢`（0..2）落在词典词 `一矢中的`
    （0..4）里，那这段字是**一个合法词的一部分**，把它当"一矢→一时"的错形
    改掉会把词拆坏。窗口上限 8 字（词典最长词），每个候选 O(64) 次集合查询，
    只对低置信命中执行，代价可忽略。
    """
    words = _known_word_set()
    if not words:
        return False
    n = len(text)
    lo = max(0, s - _MAX_WORD_WIN + 1)
    hi = min(n, s + _MAX_WORD_WIN)
    for a in range(lo, s + 1):
        for b in range(e + 1, hi + 1):
            if text[a:b] in words:
                return True
    return False


def _check_regex_group(text: str, rules, category: str) -> list[Correction]:
    out: list[Correction] = []
    for rx, repl, reason, conf in rules:
        if conf <= 0:
            continue
        try:
            for m in re.finditer(rx, text):
                if category == "数字用法":
                    # 数字规则的 repl 是提示标签而非替换文本
                    out.append(Correction(m.start(), m.end(), m.group(0), repl,
                                          category, reason, conf))
                else:
                    out.append(Correction(m.start(), m.end(), m.group(0),
                                          m.expand(repl), category, reason, conf))
        except re.error:
            continue
    return out


def _dedupe(items: list[Correction]) -> list[Correction]:
    """重叠去重：按 (confidence desc, length desc) 贪心保留。

    **D3 修正**：贪心之前先做一次"包含消解" —— 严格落在某个 L1 精确词表命中
    （``reason == "词库匹配"``）内部的候选一律丢弃。
    否则 ``反应情况→反映情况``（词表 0.85，长对）会被上下文规则
    ``反应→反映``（0.9，短规则）截胡：提示退化成"反应→反映"，
    精标集实测因此只有 99.4%（评测 E1 长期红灯）。
    消解是**单向**的——只有 L1 长对能吃掉别人，反向不成立，
    所以规则层自身的命中不会被误伤，重叠语义仍由下面的贪心兜底。
    """
    lexicon = [c for c in items if c.reason == _REASON_LEXICON]
    if lexicon:
        items = [c for c in items
                 if c.reason == _REASON_LEXICON or not any(
                     e.start <= c.start and c.end <= e.end
                     and (e.end - e.start) > (c.end - c.start) for e in lexicon)]
    items = sorted(items, key=lambda c: (c.confidence, c.end - c.start), reverse=True)
    taken: list[Correction] = []
    for c in items:
        if any(not (c.end <= t.start or c.start >= t.end) for t in taken):
            continue
        taken.append(c)
    return sorted(taken, key=lambda c: c.start)


def apply_correction(text: str, c: Correction) -> str:
    return text[:c.start] + c.suggestion + text[c.end:]


def apply_all(text: str, corrections: list[Correction], skip: set[int] | None = None) -> str:
    """从后往前批量应用（skip 为要忽略的 Correction id 集合，用 start 标识）。"""
    skip = skip or set()
    for c in sorted(corrections, key=lambda x: x.start, reverse=True):
        if c.start in skip:
            continue
        text = text[:c.start] + c.suggestion + text[c.end:]
    return text


# ------------------------------------------------------------------ 纠错标记（增强）
# 类别 -> (标记底色, 标记文字色)。中间调配色，深浅色主题下均可读。
_MARK_STYLE = {
    "错别字": ("#fdecea", "#8c1d18"),
    "易混词": ("#fff3e0", "#8a5300"),
    "标点": ("#e8eaf6", "#283593"),
    "数字用法": ("#e0f2f1", "#004d40"),
    "重复字": ("#fce4ec", "#880e4f"),     # L3 同字连（重复字）
    "用户词库": ("#fdecea", "#8c1d18"),
    "神经纠错": ("#ede7f6", "#4527a0"),   # L4 神经精排（可选增强包）
    "语法纠错": ("#e3f2fd", "#0d47a1"),   # L5 Seq2Seq 语法纠错（可选增强包）
    "语义冗余": ("#fff8e1", "#795500"),   # L2.5 多字赘余（可自动替换）
    "搭配": ("#e8f5e9", "#1b5e20"),       # L2.5 动宾/修饰搭配
    "关联词": ("#f3e5f5", "#4a148c"),     # L2.5 关联词呼应
    "表达提示": ("#e0f7fa", "#006064"),   # L2.5 提示类（跨词冗余，不可自动改）
    "标点规范": ("#e1f5fe", "#01579b"),   # L2.5 成对标点/引号形式（不可自动改）
}
_MARK_FALLBACK = ("#eceff1", "#37474f")


def to_marked_html(text: str, corrections: list[Correction],
                   show_suggestion: bool = True,
                   anchor_prefix: str = "mk") -> str:
    """把原文渲染成带纠错标记的 HTML（QTextBrowser 直接 setHtml 可用）。

    每处错误按类别底色高亮 + 下划线 + 命名锚点（{anchor_prefix}{序号}，
    供 scrollToAnchor 定位），后随灰色〔建议〕；悬停显示完整说明。
    文本一律 HTML 转义，换行转 <br>。重叠区间（理论已被 _dedupe 消除）
    做跳过防御，绝不重复渲染。
    """
    import html as _html
    parts: list[str] = []
    pos = 0
    ordered = sorted(corrections, key=lambda c: (c.start, -(c.end - c.start)))
    for i, c in enumerate(ordered):
        if c.start < pos:
            continue
        if c.end <= c.start or c.end > len(text):
            continue
        parts.append(_html.escape(text[pos:c.start]).replace("\n", "<br>"))
        bg, fg = _MARK_STYLE.get(c.category, _MARK_FALLBACK)
        tip = _html.escape(f"{c.label}（{c.category}，置信度 {c.confidence:.2f}）")
        sug = (f'<span style="color:{_MARK_FALLBACK[1]};">'
               f'〔{_html.escape(c.suggestion)}〕</span>') if show_suggestion else ""
        parts.append(
            f'<a name="{anchor_prefix}{i}"></a>'
            f'<span style="background-color:{bg};color:{fg};'
            f'text-decoration:underline;" title="{tip}">'
            f'{_html.escape(c.wrong)}</span>{sug}')
        pos = c.end
    parts.append(_html.escape(text[pos:]).replace("\n", "<br>"))
    return "".join(parts)


def paragraph_no(text: str, pos: int) -> int:
    """pos 落在第几段（按换行计数，1 起）。"""
    return text.count("\n", 0, max(0, min(pos, len(text)))) + 1


def correct_block(text: str, skip_categories: "set[str] | tuple" = ADVISORY_CATEGORIES
                  ) -> "tuple[str, list[Correction]]":
    """单个文本块（段落/标题/单元格）的纠错：返回 (修正后文本, 命中明细)。

    偏移为块内局部偏移。默认跳过 ``ADVISORY_CATEGORIES``（"数字用法/表达提示/
    标点规范"）——这些类的 suggestion 是**提示标签而不是替换文本**，
    直接写回会把正文改成标签；且它们多为需人工判断的规范建议，
    批量替换容易把编号、日期、成对符号改坏。
    """
    cs = [c for c in check_text(text) if c.category not in skip_categories]
    out = text
    for c in sorted(cs, key=lambda c: -c.start):
        out = out[:c.start] + c.suggestion + out[c.end:]
    return out, cs


def tree_to_blocks(tree) -> "list[dict]":
    """DocTree -> 轻量块列表（kind: heading/para/table），供任意文档纠错逐块处理。

    块里必须带上标题层级 ``level``：老实现只存 kind/text/rows，
    blocks_to_tree 只能给所有标题填 level=1，用户把一篇三级标题的公文
    纠错后导出，层级信息**不可逆丢失** —— docxgen 全部用 Heading 1，
    目录（TOC \\o "1-3"）与页码定位随之全错。level 只对 heading 有意义，
    para/table 存 0 即可（blocks_to_tree 只在 heading 分支读它）。
    """
    from .model import HEADING, TABLE
    # 标题不折进块列表：blocks_to_tree 的 title 是独立入参，
    # 折叠会导致导出时标题渲染两遍（DocTree.title + 正文 Heading1）。
    blocks = []
    for b in tree.blocks:
        if b.type == TABLE and b.rows:
            blocks.append({"kind": "table", "text": "", "rows": [list(r) for r in b.rows],
                           "level": 0})
        else:
            blocks.append({"kind": "heading" if b.type == HEADING else "para",
                           "text": b.text or "", "rows": None,
                           "level": int(b.level or 0) if b.type == HEADING else 0})
    return blocks


def blocks_to_tree(title: str, blocks) -> "object":
    """块列表 -> DocTree（表格行转 TABLE 块；标题沿用块内记录的层级）。"""
    from .model import Block, DocTree, HEADING, PARAGRAPH, TABLE
    out: list[Block] = []
    for b in blocks:
        if b["kind"] == "table" and b.get("rows"):
            out.append(Block(type=TABLE, rows=[list(r) for r in b["rows"]]))
        elif b["kind"] == "heading" and (b.get("text") or "").strip():
            # 缺 level（旧快照/外部构造的块）时退回 1 级，不让导出直接失败；
            # 上限 4 与 GB/T 9704 的标题层级一致，防止脏数据造出 Heading 7。
            level = int(b.get("level") or 1)
            out.append(Block(type=HEADING, level=max(1, min(level, 4)), text=b["text"]))
        elif (b.get("text") or "").strip():
            out.append(Block(type=PARAGRAPH, text=b["text"]))
    return DocTree(title=title or "", blocks=out)
