# -*- coding: utf-8 -*-
"""文秘工具箱：金额大写、数字/日期大写、全半角、简繁转换。

全部纯本地实现，无网络、无外部二进制依赖（简繁用 OpenCC 词典数据）。
"""
from __future__ import annotations

import re

_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = ["", "拾", "佰", "仟"]
_CN_BIG = ["", "万", "亿", "万亿"]

_HALF2FULL = None  # 兼容占位（转换见 half_to_full）


def amount_to_cn(value) -> str:
    """人民币小写金额 -> 规范大写。

    规则（中国人民银行《正确填写票据和结算凭证的基本规定》）：
      - 零拾零佰化简；中间连续零只写一个"零"
      - 角分：无分写"整"，有角无分写"整"可省（此处保留"整"），
        全零写"整"；分有值不写"整"
      - 万/亿 段末零化简
    示例：10050000.30 -> 壹仟零伍拾万元零叁角整
         10050000.00 -> 壹仟零伍拾万元整
    """
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
        if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
            raise ValueError(f"无法识别的金额：{value!r}")
        yuan_str, _, fen_str = s.partition(".")
        amount = int(yuan_str)
        jiao = int(fen_str[0]) if len(fen_str) >= 1 else 0
        fen = int(fen_str[1]) if len(fen_str) >= 2 else 0
    else:
        from decimal import Decimal
        d = Decimal(str(value)).quantize(Decimal("0.01"))
        amount = int(d)
        cents = int((d - amount) * 100)
        jiao, fen = divmod(cents, 10)

    if amount == 0 and jiao == 0 and fen == 0:
        return "零元整"

    def group4(n: int) -> str:
        """四位一组转大写，组内化简零。"""
        digits = [int(c) for c in f"{n:04d}"]
        out = []
        zero_pending = False
        for i, d in enumerate(digits):
            unit = _CN_UNITS[3 - i]
            if d == 0:
                zero_pending = bool(out)
            else:
                if zero_pending:
                    out.append("零")
                    zero_pending = False
                out.append(_CN_DIGITS[d] + unit)
        return "".join(out)

    # 整数部分按 万/亿 分组
    sections: list[tuple[int, str]] = []
    scales = ["", "万", "亿"]
    n = amount
    idx = 0
    while n > 0:
        sections.append((n % 10000, scales[idx]))
        n //= 10000
        idx += 1
    int_parts: list[str] = []
    zero_pending = False
    for pos in range(len(sections) - 1, -1, -1):
        val, scale = (sections[pos][0], scales[pos])
        if val == 0:
            zero_pending = bool(int_parts)
            continue
        if zero_pending and int_parts:
            int_parts.append("零")
            zero_pending = False
        g = group4(val)
        # 组内末尾是"零"如 0500 -> 零佰? group4 已处理前导；组值 <1000 且非首组需补零：
        if val < 1000 and int_parts:
            int_parts.append("零")
        int_parts.append(g + scale)
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
