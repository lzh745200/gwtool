# -*- coding: utf-8 -*-
"""GB/T 9704 公文格式体检。

两级检查：
  - inspect_text：纯文本级（标题编号链条、成文日期、发文字号、结束语与文种匹配等）
  - inspect_docx：docx 级（页边距、正文字体字号行距、标题字体、页码域）
输出 Finding 列表：severity = error/warn/info。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_GB = {
    "margin": (37, 35, 28, 26),          # 上 下 左 右 mm
    "body_font": {"仿宋_GB2312", "仿宋", "FangSong", "仿宋_GB2312-0"},
    "h1_font": {"黑体", "SimHei", "Heiti"},
    "h2_font": {"楷体_GB2312", "楷体", "KaiTi"},
    "body_size": 16.0,                    # 三号
    "line_spacing": 28.0,                 # 磅
}

_CHAIN = [  # 层级顺序
    (re.compile(r"^[一二三四五六七八九十]+、"), "一、"),
    (re.compile(r"^[（(][一二三四五六七八九十]+[)）]"), "（一）"),
    (re.compile(r"^\d{1,2}[、．.](?!\d)"), "1."),
    (re.compile(r"^[（(]\d{1,2}[)）]"), "（1）"),
]

_CN_ORD = "一二三四五六七八九十"

# ---- 引文规范（引用**其他**公文时的写法）----
# 注意与既有的「发文字号」检查区分：那一条查的是**本文自己**的字号用不用
# 六角括号；本条查的是**引用他人公文**时的写法。两者规则对象不同，不可混淆。
_CITE_RE = re.compile(r"《([^《》]{0,100})》")
_CITED_DOC_NO_RE = re.compile(
    r"[（(][^）)]{0,40}〔\s*\d{4}\s*〕[^）)]{0,16}号\s*[）)]")
# 法定公文文种（12 种军队机关公文，见《军队机关公文处理工作条例》第八条）：
# 书名号内以这些词结尾的判为"公文标题"，引用时须带发文字号。
_CITE_KIND_TAIL = ("命令", "通令", "决定", "指示", "通知", "通报", "报告",
                   "请示", "批复", "函", "通告", "纪要")
# 法规/规章/制度类：引用惯例**不带**发文字号（如《XX条例》《XX办法》）。
# 必须先排除，否则「凡书名号都要求带字号」会造成大面积误报，反而让用户
# 关掉整个检查——低误报率是这类提示能活下去的前提。
_CITE_LAW_TAIL = ("法", "条例", "办法", "规定", "细则", "准则", "标准",
                  "规范", "章程", "公约", "纲要", "计划", "方案")

# ---- 数字用法（全文一致性）----
# 只收**明确计数量的量词**，刻意排除"年/月/日"等时间单位：
# 「一年来」与「1年」并存属正常修辞，纳入统计只会制造噪音。
_NUM_CLASSIFIERS = ("个", "人", "次", "条", "件", "项", "家", "种", "名",
                    "位", "份", "页", "台", "辆", "处", "起", "套", "批",
                    "期", "届", "户", "所", "座")
# 含汉字数字的固定术语：不得参与"写法统一"统计（详见 _mask_cn_terms）
_CN_TERM_EXCLUDE = ("三个代表", "三严三实", "四个全面", "五位一体", "两个维护",
                    "四个意识", "四个自信", "四个服从", "两学一做", "三重一大",
                    "一国两制", "两个务必", "六稳六保", "两不愁三保障",
                    "三会一课", "四个铁一般", "五个文明", "十四五", "十五五")

# ---- 文内一致性（单位名称的简称/全称并存）----
# 只认带机构后缀的短语：不这样做就得靠"N-gram + 频率"猜专名，
# 误报会多到不可用。长后缀写在前面只是可读性考虑——正则是在"剩余串的开头"
# 尝试交替项，因此顺序不影响结果。
_ENTITY_RE = re.compile(
    r"([\u4e00-\u9fff]{2,10}?"
    r"(?:管理委员会|管理局|办公室|委员会|服务中心|研究院|"
    r"分局|支队|大队|集团|公司|中心|医院|学校|学院|银行|"
    r"局|委|办|厅|部|处|科|院|所|站|队))")
# 实体数超过该值就不做两两比较：O(n²) 在长文上会拖慢体检
_MAX_ENTITIES = 200
# 最多报几组疑似变体，避免长文刷屏
_MAX_ENTITY_VARIANTS = 8

# ---- 公文用语与文风 ----
# 只收**几乎不可能出现在规范公文里**的口语词。像"搞""特别"这类看似口语的
# 词在公文中有大量规范用法（"搞好""特别重要"），收进来只会制造误报——
# 文风建议一旦不可信，用户会连同整个体检一起忽略。
_SPOKEN_WORDS = ("咱们", "咱", "啥", "咋", "挺好的", "搞定", "弄好",
                 "什么的", "之类的", "差不多", "恨不得", "压根",
                 "老鼻子", "贼好", "挺不错")
# 单句字数上限：公文长句宜拆。80 字是按"一屏能读完一段"的经验值定的。
_MAX_SENTENCE_CHARS = 80
# 文风类提示最多报几条，避免长文刷屏
_MAX_STYLE_FINDINGS = 6


@dataclass
class Finding:
    severity: str   # error / warn / info
    item: str       # 检查项
    detail: str     # 说明（含位置）

    @property
    def label(self) -> str:
        icon = {"error": "✖", "warn": "▲", "info": "ℹ"}.get(self.severity, "·")
        return f"{icon} [{self.item}] {self.detail}"


def _classify(line: str) -> tuple[int, str] | None:
    for lv, (rx, _) in enumerate(_CHAIN):
        if rx.match(line):
            return lv, line
    return None


def _cn_to_int(s: str) -> int:
    """中文数字 -> 整数（一~九十九，覆盖公文编号范围）。

    注意“一”必须映射为 1（用 零一二… 映射表，不能用下标 0 的集合）。
    """
    digits = "零一二三四五六七八九"
    total, cur = 0, 0
    for ch in s:
        if ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total = (total + (cur or 1)) * 100
            cur = 0
        elif ch in digits:
            cur = cur * 10 + digits.index(ch)
    return total + cur


def inspect_text(text: str, kind_hint: str = "") -> list[Finding]:
    out: list[Finding] = []
    lines = [ln.strip() for ln in text.split("\n")]
    title = next((ln for ln in lines if ln), "")
    kind = kind_hint or next(
        (k for k in ("请示", "报告", "通知", "函", "批复", "通报", "指示", "纪要")
         if k in title), "")

    # ---- 标题编号链条 ----
    counters = [0, 0, 0, 0]
    seen_kinds: list[int] = []
    for ln_no, ln in enumerate(lines, 1):
        cls = _classify(ln)
        if not cls:
            continue
        lv, line = cls
        body = _CHAIN[lv][0].match(line).group(0)
        if lv == 0:
            num = _cn_to_int(re.sub(r"[、.．]", "", body))
        elif lv == 1:
            num = _cn_to_int(re.sub(r"[（）()、.．\s]", "", body))
        else:
            num = int(re.sub(r"\D", "", body) or 0)
        expect = counters[lv] + 1
        if num != expect:
            if num > expect:
                out.append(Finding("warn", "标题编号",
                                   f"第{ln_no}行「{body}…」跳号，应为第{expect}项"))
            else:
                out.append(Finding("warn", "标题编号",
                                   f"第{ln_no}行「{body}…」编号重复或回退"))
        counters[lv] = num
        counters[lv + 1:] = [0] * (3 - lv)
        if seen_kinds and lv > max(seen_kinds) + 1:
            out.append(Finding("warn", "标题层级",
                               f"第{ln_no}行从 {_CHAIN[max(seen_kinds)][1]} "
                               f"直接跳到 {_CHAIN[lv][1]}，层级不连贯"))
        if lv not in seen_kinds:
            seen_kinds.append(lv)
        if lv < seen_kinds[-1] and lv + 1 < len(counters):
            pass

    # ---- 发文字号 ----
    for m in re.finditer(r"[〔\[（(]\s*(\d{4})\s*[〕\]）)]", text):
        bracket = text[m.start()]
        if bracket in "[(":
            out.append(Finding("error", "发文字号",
                               f"「{m.group(0)}」应使用六角括号〔〕，如“×政发〔{m.group(1)}〕5号”"))

    # ---- 成文日期 ----
    for m in re.finditer(r"[二〇○○○O零一二三四五六七八九]{4}年", text):
        out.append(Finding("info", "成文日期",
                           f"「{m.group(0)}」建议改为阿拉伯数字，如“2026年”"))
    # 逐处报告：原实现的 break 无条件在首轮末尾执行（不在 if 内），
    # 导致正文里第二处起的日期问题全部漏报。改为遍历全部命中，
    # 用 seen 去重避免同一写法的重复提示刷屏。
    seen_date: set[str] = set()
    for m in re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text):
        if len(m.group(2)) == 2 and m.group(2)[0] == "0" \
                or len(m.group(3)) == 2 and m.group(3)[0] == "0":
            if m.group(0) in seen_date:
                continue
            seen_date.add(m.group(0))
            out.append(Finding("warn", "成文日期",
                               f"「{m.group(0)}」月/日不应有前导零，应为“{int(m.group(2))}月{int(m.group(3))}日”"))
    seen_slash: set[str] = set()
    for m in re.finditer(r"\d{4}[./]\d{1,2}[./]\d{1,2}", text):
        if m.group(0) in seen_slash:
            continue
        seen_slash.add(m.group(0))
        out.append(Finding("warn", "成文日期",
                           f"「{m.group(0)}」公文成文日期建议写为“2026年8月30日”式"))

    # ---- 结束语与文种匹配 ----
    closings = {
        "请示": ("妥否，请批示", "以上请示如无不妥，请批复", "请批示"),
        "报告": ("特此报告", "专此报告"),
        "通知": ("特此通知",),
        "通报": ("特此通报",),
        "函": ("为盼", "为荷", "函复"),
        "批复": ("请遵照执行",),
    }
    if kind in closings:
        if not any(c in text for c in closings[kind]):
            out.append(Finding("warn", "结束语",
                               f"文种「{kind}」通常应有结束语，如“{closings[kind][0]}。”"))
    if kind == "报告" and re.search(r"请批示|请批复", text):
        out.append(Finding("error", "文种混用",
                           "“报告”不得夹带请示事项（出现“请批示/请批复”字样）"))
    if kind == "请示" and "一文" not in text and text.count("请示事项") > 1:
        out.append(Finding("info", "请示规则", "请示应一文一事，请核对是否合并多事项"))

    # ---- 主送机关 ----
    if kind in ("通知", "通报", "请示", "报告", "函", "批复", "指示"):
        has_recipient = any(
            ln.endswith(("：", ":")) and len(ln) <= 40 and i <= 6
            for i, ln in enumerate(lines[:8]))
        if not has_recipient:
            out.append(Finding("warn", "主送机关",
                               f"「{kind}」应顶格标注主送机关（如“各科室：”）"))

    # ---- 篇幅参考 ----
    n_chars = len(re.sub(r"\s", "", text))
    if kind and n_chars > 3000:
        out.append(Finding("info", "篇幅",
                           f"全文约 {n_chars} 字，一般性「{kind}」建议精炼篇幅"))

    out.extend(_check_classification(lines))
    out.extend(_check_attachments(lines))
    out.extend(_check_citations(lines))
    out.extend(_check_number_usage(lines))
    out.extend(_check_consistency(lines))
    out.extend(_check_style(lines))
    return out


def _check_classification(lines: list[str]) -> list[Finding]:
    """密级标注：绝密/机密/秘密 开头的行应采用“密级★保密期限”格式。"""
    for idx, ln in enumerate(lines[:6]):
        m = re.match(r"^(绝密|机密|秘密)", ln)
        if m and "★" not in ln:
            return [Finding(
                "error", "密级标注",
                f"第{idx + 1}行“{ln[:20]}”疑为密级标注，应采用“{m.group(1)}★保密期限”"
                f"格式（如：秘密★1年），并置于首页版心左上角")]
    return []


def _check_attachments(lines: list[str]) -> list[Finding]:
    """附件说明：名称后应使用全角冒号；多个附件须用阿拉伯数字编号。"""
    out = []
    for idx, ln in enumerate(lines):
        if re.match(r"^附件[ \u3000]+\S", ln):
            out.append(Finding(
                "warn", "附件说明",
                f"第{idx + 1}行：附件名称后应使用全角冒号（附件：1. ×××）"))
            break
    colon_lines = [i for i, ln in enumerate(lines) if re.match(r"^附件\s*[:：]", ln)]
    if len(colon_lines) > 1 and not any(
            re.match(r"^附件\s*[:：]\s*\d", lines[i]) for i in colon_lines):
        out.append(Finding(
            "warn", "附件说明",
            f"第{colon_lines[0] + 1}行：多个附件说明应使用阿拉伯数字编号"
            "（附件：1. ×××）"))
    return out


def _looks_like_cited_doc(title: str) -> bool:
    """书名号内是否为**公文**标题（引用时按惯例应带发文字号）。

    区分"公文"与"法规规章"是这条检查低误报的关键：
    《中华人民共和国宪法》《XX省节约用水条例》引用时从不带字号，若一并要求，
    用户会被淹在误报里。
    """
    t = title.strip()
    if not t or t.startswith("中华人民共和国"):
        return False
    if t.endswith(_CITE_LAW_TAIL):
        return False
    if "关于" in t:
        return True
    return t.endswith(_CITE_KIND_TAIL)


def _line_of(text: str, pos: int) -> int:
    """字符偏移 -> 行号（inspect_text 的 lines 由 text.split("\\n") 得来，编号一致）。"""
    return text.count("\n", 0, pos) + 1


def _check_citations(lines: list[str]) -> list[Finding]:
    """引文规范：引用其他公文时的写法要求。

    「引用公文」在公文写作中出现频率极高、出错率也高，规则明确且可机器校验，
    却是体检此前**唯一没有覆盖的大类**。当前实现六条规则：

      C1 书名号不配对（硬错）
      C2 空书名号（硬错）
      C3 书名号内嵌套书名号
      C4 公文标题**首次**引用未标注发文字号（法规规章不适用）
      C5 书名号后的字号用了半角括号（应为全角）
      C6 相邻两处「根据」之间无句读分隔，建议合并为一次引导

    刻意**不做**「引用已废止机构」检查：那已由纠错层的机构沿革对照覆盖，
    在这里重复实现只会让用户在纠错与体检两处看到同一条问题。
    """
    text = "\n".join(lines)
    out: list[Finding] = []
    if not text:
        return out

    # C1 书名号配对
    n_open, n_close = text.count("《"), text.count("》")
    if n_open != n_close:
        out.append(Finding(
            "error", "引文规范",
            f"书名号不配对：「《」出现 {n_open} 次、「》」出现 {n_close} 次，请核对"))

    # C2 空书名号
    for m in re.finditer(r"《[\s\u3000]*》", text):
        out.append(Finding("error", "引文规范",
                           f"第{_line_of(text, m.start())}行：书名号内为空"))

    # C3 嵌套书名号
    for m in re.finditer(r"《[^》]{0,60}《", text):
        out.append(Finding(
            "warn", "引文规范",
            f"第{_line_of(text, m.start())}行：书名号内又出现书名号，"
            f"嵌套的应为被引文件的正式名称"))

    # C4 首次引用未标注发文字号
    seen_titles: set[str] = set()
    for m in _CITE_RE.finditer(text):
        title = m.group(1).strip()
        if not title or title in seen_titles:
            continue          # 只查"首次"：同一文件后文再提不用重复要求
        seen_titles.add(title)
        if not _looks_like_cited_doc(title):
            continue
        # 字号可能写在书名号之后不远处（允许中间夹"（"或空格）
        if _CITED_DOC_NO_RE.search(text[m.end(): m.end() + 40]):
            continue
        out.append(Finding(
            "warn", "引文规范",
            f"第{_line_of(text, m.start())}行：《{title[:20]}》首次引用建议标注"
            f"发文字号，如“…（×政发〔2026〕5号）”"))

    # C5 字号括号用了半角
    for m in _CITE_RE.finditer(text):
        tail = text[m.end(): m.end() + 40]
        if tail.startswith("(") and "〔" in tail:
            out.append(Finding(
                "warn", "引文规范",
                f"第{_line_of(text, m.start())}行：书名号后标注发文字号应使用"
                f"全角括号「（）」，当前为半角"))

    # C6 相邻「根据」未合并
    positions = [m.start() for m in re.finditer("根据", text)]
    for a, b in zip(positions, positions[1:]):
        if b - a > 25:
            continue
        # 中间有句读说明是各自独立的句子，属正常写法
        if re.search(r"[。；！？\n]", text[a:b]):
            continue
        out.append(Finding(
            "warn", "引文规范",
            f"第{_line_of(text, b)}行：与前一处「根据」相邻且无句读分隔，"
            f"建议合并为一次引导（如“根据《A》《B》”）"))

    return out


def _mask_cn_terms(text: str) -> str:
    """把含汉字数字的固定术语替换为等长全角空格，供数字用法统计使用。

    「三个代表」「四个全面」这类政治术语里的汉字数字**不能**参与"写法统一"
    统计——否则文中只要另有一处「3个」，就会报一条毫无意义的"不统一"。
    用等长替换而非删除，是为了保持字符偏移不变（行号仍准确）。
    """
    for term in _CN_TERM_EXCLUDE:
        if term in text:
            text = text.replace(term, "\u3000" * len(term))
    return text


def _check_number_usage(lines: list[str]) -> list[Finding]:
    """数字用法：**全文统一性**层面的检查（GB/T 15835 精神，全部为提示级）。

    与纠错层的分工（重要，避免重复报警）
    ------------------------------------
    纠错层的 `NUMBER_RULES` 已经覆盖了逐处的数字规范：
      · 汉字年份「二〇二六年」→ 阿拉伯数字
      · 相邻数字表概数「3、4个」→ 汉字概数
      · 小数的百分号写法
    那三条属于"某处写得不规范"，适合逐处替换。本函数**刻意不重复**它们，
    只做纠错层做不到的事——**跨全文的写法一致性**：同一量词在全文里
    一会儿用阿拉伯数字、一会儿用汉字，单看每一处都不算错，只有纵览全文
    才能发现体例不统一。这正是"体检"相对于"纠错"的独特价值。

    全部为 `info` 级：数字用法的对错高度依赖语境，误判为"错别字"会让用户
    对纠错失去信任，故这里只提示、不判定。
    """
    text = _mask_cn_terms("\n".join(lines))
    out: list[Finding] = []
    if not text:
        return out

    # 同一量词下两种数字形式并存 -> 体例不统一
    for cl in _NUM_CLASSIFIERS:
        d = re.search(rf"(?<![\d.])(\d{{1,4}}){cl}", text)
        c = re.search(rf"([一二三四五六七八九十百千两]{{1,6}}){cl}", text)
        if d and c:
            out.append(Finding(
                "info", "数字用法",
                f"「{cl}」的数字写法全文不统一：第{_line_of(text, d.start())}行用"
                f"“{d.group(0)}”，第{_line_of(text, c.start())}行用“{c.group(0)}”。"
                f"同一语境建议统一为同一种数字形式"))

    # 百分比的两种写法并存
    if re.search(r"\d+\s*[%％]", text) and re.search(
            r"百分之[一二三四五六七八九十百千零两]+", text):
        out.append(Finding(
            "info", "数字用法",
            "百分比写法全文不统一：既有“50%”式，又有“百分之五十”式，建议全文统一"))
    return out


def _is_variant(a: str, b: str) -> bool:
    """判断两个单位名是否**疑似同一主体的简称与全称**。

    刻意做得保守——一致性检查一旦误报，"李强"与"李强华"会被当成同一个人，
    用户就再也不信这个功能了。只认最明确的一类：短名是长名**删去中间若干字**
    后得到，且两者以同一个机构后缀结尾。

    具体排除两种常见误判：
      · 短名是长名的**前缀**（如「安全科」与「安全科研」）——那是不同的词，
        不是简写，`长名.startswith(短名)` 直接返回 False；
      · 长度差过大（超过 4 字）——已不像简写了，不猜。
    """
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 3 or len(long_) - len(short) > 4:
        return False
    if long_.startswith(short):
        return False
    if not long_.startswith(short[:2]):
        return False
    if long_[-1] != short[-1]:
        return False
    # 短名的字符须在长名中按序出现（即短名是长名的子序列）
    it = iter(long_)
    return all(ch in it for ch in short)


def _check_consistency(lines: list[str]) -> list[Finding]:
    """文内一致性：同一单位在全文里出现多种写法时提示核对。

    这类问题**逐句看不出来**——"县应急局"和"县应急管理局"各自都对，
    只有纵览全文才发现体例不统一。这正是体检相对于逐处纠错的独特价值。

    **只报并列、不判对错**：不告诉用户哪个写法正确（可能两个都不对，
    或本就是两个不同主体），只把并存事实与各自出现次数摆出来。
    超过 _MAX_ENTITY_VARIANTS 条时截断，避免长文刷屏。
    """
    text = "\n".join(lines)
    counts: dict[str, int] = {}
    for m in _ENTITY_RE.finditer(text):
        name = m.group(1)
        if name:
            counts[name] = counts.get(name, 0) + 1
    names = sorted(counts)
    # 实体太多时两两比较会退化：短文才做这项检查
    if len(names) < 2 or len(names) > _MAX_ENTITIES:
        return []

    out: list[Finding] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if not _is_variant(a, b):
                continue
            out.append(Finding(
                "warn", "一致性",
                f"「{a}」（{counts[a]} 次）与「{b}」（{counts[b]} 次）并存，"
                f"请核对是否为同一单位的不同写法，并统一为规范全称"))
            if len(out) >= _MAX_ENTITY_VARIANTS:
                return out
    return out


def _check_style(lines: list[str]) -> list[Finding]:
    """公文用语与文风：口语词与超长句（全部 `info` 级，只提示不判定）。

    为什么是 info 级、且条目克制
    ----------------------------
    文风的主观性远高于格式与错别字："差不多"未必不能接受，"80 字长句"也
    未必该拆。若报成 `warn`/`error`，用户会觉得软件在说教；一旦误报几次，
    整个体检的可信度都会被带下去。因此这里只放两类**判断明确**的问题，
    且一律 `info`。

    与纠错层的分工：纠错处理"错别字/易混词"（有确定答案），本检查处理
    "语体不当"（无确定答案，只给参考）。
    """
    text = "\n".join(lines)
    out: list[Finding] = []
    if not text:
        return out

    # 按词长降序处理，并用 covered 记录已被更长词占用的区间。
    # 不做这件事的话「咱们」与「咱」会命中同一处各报一次——词表内的前缀
    # 重叠会成倍放大提示数量（"咱们"三处 -> 3 条 + 3 条）。
    covered: list[tuple[int, int]] = []
    reported: set[str] = set()
    for word in sorted(_SPOKEN_WORDS, key=len, reverse=True):
        if len(out) >= _MAX_STYLE_FINDINGS:
            return out
        for m in re.finditer(re.escape(word), text):
            start, end = m.span()
            if any(cs <= start < ce for cs, ce in covered):
                continue
            covered.append((start, end))
            if word in reported:
                continue          # 同一个词只提示一次，避免长文刷屏
            reported.add(word)
            out.append(Finding(
                "info", "文风",
                f"第{_line_of(text, start)}行出现口语词「{word}」，"
                f"公文宜改用书面语"))

    long_sentences = 0
    for m in re.finditer(r"[^。；！？\n]+[。；！？]", text):
        seg = m.group(0)
        body = re.sub(r"\s", "", seg)
        if len(body) <= _MAX_SENTENCE_CHARS:
            continue
        out.append(Finding(
            "info", "文风",
            f"第{_line_of(text, m.start())}行有一句约 {len(body)} 字，"
            f"超过 {_MAX_SENTENCE_CHARS} 字，建议拆分以便阅读"))
        long_sentences += 1
        if long_sentences >= 3:
            break
    return out[: _MAX_STYLE_FINDINGS]


def inspect_docx(path: str) -> list[Finding]:
    """对生成的/外来的 docx 做 GB/T 9704 常用格式检查。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Mm

    out: list[Finding] = []
    doc = Document(path)

    # 页边距
    sec = doc.sections[0]
    got = (round(sec.top_margin / Mm(1)), round(sec.bottom_margin / Mm(1)),
           round(sec.left_margin / Mm(1)), round(sec.right_margin / Mm(1)))
    if got != _GB["margin"]:
        out.append(Finding("info", "页边距",
                           f"当前 上{got[0]} 下{got[1]} 左{got[2]} 右{got[3]}mm；"
                           f"GB/T 9704 常用 上37 下35 左28 右26mm"))

    def east_asia(run) -> str:
        rpr = run._element.rPr
        if rpr is None:
            return ""
        rf = rpr.find(qn("w:rFonts"))
        return rf.get(qn("w:eastAsia")) if rf is not None else ""

    body_n = h_n = 0
    font_bad = size_bad = ls_bad = hfont_bad = 0
    for par in doc.paragraphs:
        text = par.text.strip()
        if not text:
            continue
        style = (par.style.name or "")
        is_heading = style.lower().startswith("heading") or "标题" in style
        if is_heading:
            h_n += 1
            if par.runs:
                fam = east_asia(par.runs[0])
                if fam and fam not in _GB["h1_font"] | _GB["h2_font"] \
                        | _GB["body_font"]:
                    hfont_bad += 1
            continue
        body_n += 1
        if body_n > 200:
            continue
        if par.runs:
            fam = east_asia(par.runs[0])
            size = par.runs[0].font.size.pt if par.runs[0].font.size else None
            if fam and fam not in _GB["body_font"]:
                font_bad += 1
            if size and abs(size - _GB["body_size"]) > 0.6:
                size_bad += 1
        pf = par.paragraph_format
        if pf.line_spacing_rule is not None and pf.line_spacing is not None:
            try:
                if abs(pf.line_spacing.pt - _GB["line_spacing"]) > 1.5:
                    ls_bad += 1
            except AttributeError:
                pass

    if body_n and font_bad / min(body_n, 200) > 0.3:
        out.append(Finding("warn", "正文字体",
                           f"多数正文段落非仿宋类字体（{font_bad} 段）"))
    if body_n and size_bad / min(body_n, 200) > 0.3:
        out.append(Finding("warn", "正文字号",
                           f"多数正文段落非三号（16pt），共 {size_bad} 段"))
    if body_n and ls_bad / min(body_n, 200) > 0.3:
        out.append(Finding("info", "行距",
                           f"多数正文段落非固定 28 磅行距，共 {ls_bad} 段"))
    if h_n and hfont_bad / h_n > 0.5:
        out.append(Finding("info", "标题字体",
                           "多数标题未使用黑体/楷体/仿宋体系，请核对"))

    # 页码域
    footer = doc.sections[0].footer
    has_page = any("PAGE" in p._p.xml for p in footer.paragraphs)
    if not has_page:
        out.append(Finding("info", "页码", "页脚未检测到页码域（PAGE）"))

    # 文本层 GB/T 9704 检查（第 24 轮补齐）：此前 inspect_docx 只查版式，
    # 外来 docx 的内容违规（发文字号括号/编号链条/结束语与文种/成文日期）
    # 全部漏报——体检一个同事发来的 docx 却查不出「(2026)」这类硬伤。
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if text.strip():
        out = inspect_text(text) + out
    return out
