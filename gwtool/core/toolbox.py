# -*- coding: utf-8 -*-
"""文秘工具箱：金额大写、数字/日期大写、全半角、简繁转换。

全部纯本地实现，无网络、无外部二进制依赖（简繁用 OpenCC 词典数据）。
"""
from __future__ import annotations

import re

_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = ["", "拾", "佰", "仟"]
# 四位段单位，按"本段右侧剩余段数"取模索引：
#   剩余 1 段 -> 万，2 -> 亿，3 -> 万亿，4 -> 亿亿，5 -> 万亿 ...（3 为周期循环）
# 对照：10^4=万、10^8=亿、10^12=万亿、10^16=亿亿、10^20=万亿亿…
_CHUNK_SCALES = ["万", "亿", "万亿"]

_HALF2FULL = None  # 兼容占位（转换见 half_to_full）


def amount_to_cn(value) -> str:
    """人民币小写金额 -> 规范大写。

    规则（中国人民银行《正确填写票据和结算凭证的基本规定》）：
      - 零拾零佰化简；中间有"0"必须写"零"，连续几个"0"只写一个"零"
      - 角分：无分写"整"，有角无分写"整"可省（此处保留"整"），
        全零写"整"；分有值不写"整"
      - 万/亿 段末零化简
    示例：10050000.30 -> 壹仟零伍万元叁角整
         10050000.00 -> 壹仟零伍万元整
         1005000.00  -> 壹佰万零伍仟元整（上段不满四位，跨段须补"零"）
    """
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
        # 角分必须恰好 1~2 位：'1.999' 超过"分"的最小单位，宁可明确拒绝，
        # 也不要静默截断成 1.99（金额少写一分是事故，不是四舍五入）
        if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
            raise ValueError(f"无法识别的金额：{value!r}")
        yuan_str, _, fen_str = s.partition(".")
        amount = int(yuan_str)
        jiao = int(fen_str[0]) if len(fen_str) >= 1 else 0
        fen = int(fen_str[1]) if len(fen_str) >= 2 else 0
    else:
        from decimal import Decimal
        try:
            d = Decimal(str(value)).quantize(Decimal("0.01"))
        except Exception:
            raise ValueError(f"无法识别的金额：{value!r}") from None
        d = abs(d)          # 冲销等负数金额按绝对值处理，票据不支持负号大写
        amount = int(d)
        cents = int((d - amount) * 100)
        jiao, fen = divmod(cents, 10)

    if amount == 0 and jiao == 0 and fen == 0:
        return "零元整"

    def group4(n: int) -> str:
        """四位一组转大写：按位取单位（仟/佰/拾/个），零只写一个。

        逐位取单位而不是"遇到非零位就补零"：后者在 1005 这类
        "零后面还有有效位"的组里会把位数丢掉（读成 壹仟零伍拾）。
        """
        out: list[str] = []
        zero_run = False
        for i, ch in enumerate(f"{n:04d}"):
            d = int(ch)
            if d == 0:
                zero_run = bool(out)        # 组首零不写（前导零）
                continue
            if zero_run:
                out.append("零")
                zero_run = False
            out.append(_CN_DIGITS[d] + _CN_UNITS[3 - i])
        return "".join(out)

    # 整数部分按 4 位一段切（**含高位空段**）。
    # 段号是"第几段"的定位信息，不能随前导零一起丢掉：
    # 1_0000_0000_0000_0000 的 1 必须知道自己排在"亿亿"段（10^16），
    # 若只按有效数字切段会算成 10^12（=万亿），读出来少 4 个数量级。
    groups = (len(str(amount)) + 3) // 4
    chunks = [0] * groups
    n = amount
    for i in range(groups - 1, -1, -1):
        chunks[i] = n % 10000
        n //= 10000

    int_parts: list[str] = []
    # 段间补零：判据是**上一段的最低有效位与下一段的最高有效位在十进制上是否
    # 相邻**（规范：阿拉伯数字中间有"0"时必须写"零"，连续几个只写一个）。
    #
    # 用「位号」（个位=0，逐位递增）表达最不易出错：此前用"本段是否满四位"之类
    # 间接判据，既漏过 1005000（壹佰万**零**伍仟：上段 0100 的最低有效位在百位，
    # 与下段千位之间隔着万位），又会在 1234567（壹佰贰拾叁万肆仟…：上段 0123 的
    # 最低有效位在万位，与下段千位紧邻）上多补一个零。
    #
    # 例外：两段都满四位时（50001000 = 伍仟万壹仟 或 伍仟万零壹仟），规范明确
    # "可以只写一个零字，也可以不写"；此处与既有行为/断言一致地取"不写"。
    def _top(val: int, base: int) -> int:
        """段内最高有效位的绝对位号。"""
        return base + len(str(val)) - 1

    def _bottom(val: int, base: int) -> int:
        """段内最低有效位的绝对位号（去掉该段尾部所有的零）。"""
        k = 0
        while val % 10 == 0:
            val //= 10
            k += 1
        return base + k

    prev_val = 0
    prev_bottom = None
    for gi, val in enumerate(chunks):
        if val == 0:
            continue                      # 段名（万/亿）一律不写；缺口由下一段补零
        base = 4 * (len(chunks) - 1 - gi)
        if prev_bottom is not None:
            adjacent = (prev_bottom - _top(val, base)) == 1
            may_skip = prev_val >= 1000 and val >= 1000
            if not (adjacent or may_skip):
                int_parts.append("零")
        int_parts.append(group4(val) + _scale_suffix(len(chunks), gi))
        prev_val = val
        prev_bottom = _bottom(val, base)
    int_cn = "".join(int_parts)

    dec_cn = ""
    if jiao == 0 and fen == 0:
        dec_cn = "整"
    else:
        if amount > 0 and jiao == 0 and fen > 0:
            dec_cn += "零"
        if jiao > 0:
            dec_cn += _CN_DIGITS[jiao] + "角"
        if fen > 0:
            dec_cn += _CN_DIGITS[fen] + "分"
        elif jiao > 0:
            dec_cn += "整"

    prefix = "人民币" if (amount or jiao or fen) else ""
    yuan_cn = int_cn + "元" if (amount > 0 or dec_cn != "整") else ""
    if amount == 0 and (jiao or fen):
        yuan_cn = ""
    return prefix + yuan_cn + dec_cn


def _scale_suffix(group_count: int, gi: int) -> str:
    """第 gi 段的单位后缀（group_count = 整数部分按 4 位切出的总段数）。

    汉语数词是 **万 / 亿 / 万亿** 三级一循环，不是"每四位一个新单位"：

        10^4  = 万        10^8  = 亿        10^12 = 万亿
        10^16 = 亿亿      10^20 = 万亿亿     10^24 = 亿亿亿 …

    换成"本段右侧还剩几段"（remaining）就是固定循环：

        remaining = 1 -> 万      2 -> 亿      3 -> 万亿
                    4 -> 亿亿    5 -> 万亿亿  6 -> 亿亿亿 …

    老实现把单位写成 ["", "万", "亿"] 三档下标，13 位以上直接 IndexError 崩溃；
    而 12 位的 ``1_2345_6789_0123``（1 万亿 2345 亿…）又要求最高段挂"万亿"，
    只有按这个循环取才不会读错。
    """
    remaining = group_count - 1 - gi          # 本段右侧还有几段 = 本段的 10^(4r) 量级
    if remaining == 0:
        return ""
    # 10^(4r) 的标准读法（r 与读数）：
    #   1=万  2=亿  3=万亿  4=亿亿  5=万亿亿  6=亿亿亿  7=万亿亿亿 …
    # r 是 3 的倍数时读"万亿…"，否则读"亿…"（叠 亿 的次数见下表）
    _READINGS = {1: "万", 2: "亿", 3: "万亿", 4: "亿亿", 5: "万亿亿", 6: "亿亿亿",
                 7: "万亿亿亿", 8: "亿亿亿亿"}
    if remaining in _READINGS:
        return _READINGS[remaining]
    # 更高位继续按"三的倍数 -> 万亿…，否则 亿…"延伸（10^36 以上，实际用不到）
    base = "万亿" if remaining % 3 == 0 else "亿"
    return base + "亿" * (remaining // 3)


def digits_to_cn_date(text: str) -> str:
    """2026年8月30日 -> 二〇二六年八月三十日；支持 2026-08-30 / 2026.8.30。"""
    m = re.search(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?", text)
    if not m:
        return text
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    year_cn = "".join("〇一二三四五六七八九"[int(c)] if c.isdigit() else c for c in y)
    cn = f"{year_cn}年{_small_number(mo)}月{_small_number(d)}日"
    return text[:m.start()] + cn + text[m.end():]


def number_to_upper_cn(text: str) -> str:
    """阿拉伯数字串 -> 大写数码（贰零贰陆），用于编号类文字。"""
    def repl(m):
        return "".join("零壹贰叁肆伍陆柒捌玖"[int(c)] for c in m.group(0))
    return re.sub(r"\d+", repl, text)


def _small_number(n: int) -> str:
    """1-99 的中文数字（8月->八月, 10月->十月, 21日->二十一日）。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    tens, ones = divmod(n, 10)
    out = "十" if tens == 1 else digits[tens] + "十"
    if ones:
        out += digits[ones]
    return out


def full_to_half(text: str) -> str:
    """全角字母/数字/标点 -> 半角（保留汉字）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def half_to_full(text: str) -> str:
    """半角字母/数字/标点 -> 全角（保留汉字）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if ch == " ":
            out.append("\u3000")
        elif 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


_CC_CACHE: dict[str, object] = {}


def _opencc(direction: str):
    if direction not in _CC_CACHE:
        try:
            import opencc
            _CC_CACHE[direction] = opencc.OpenCC(direction)  # 自动补 .json
        except Exception:
            _CC_CACHE[direction] = False
    return _CC_CACHE[direction]


def s2t(text: str) -> str:
    """简 -> 繁（OpenCC 离线词典；库缺失时原样返回）。"""
    cc = _opencc("s2t")
    return cc.convert(text) if cc else text


def t2s(text: str) -> str:
    """繁 -> 简。"""
    cc = _opencc("t2s")
    return cc.convert(text) if cc else text
