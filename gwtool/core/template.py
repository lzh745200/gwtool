# -*- coding: utf-8 -*-
"""公文模板模型：JSON 可序列化的排版参数集合。

默认模板遵循 GB/T 9704-2012《党政机关公文格式》常用参数：
  页边距 上37mm 下35mm 左28mm 右26mm；正文仿宋_GB2312 三号(16pt) 行距固定28磅；
  一级标题黑体、二级楷体、三级仿宋加粗；页码宋体四号、奇数页右/偶数页左（外侧）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

FONT_BODY = "仿宋_GB2312"
FONT_HEI = "黑体"
FONT_KAI = "楷体_GB2312"
FONT_SONG = "宋体"
FONT_XBS = "方正小标宋简体"

PT_SIZE = {  # 字号 -> 磅值
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
    "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
}


@dataclass
class HeadingStyle:
    font: str = FONT_HEI
    size_pt: float = 16
    bold: bool = False
    align: str = "left"        # left/center
    indent_chars: float = 2    # 首行缩进字符数（0=不缩进）


@dataclass
class RedHeader:
    enabled: bool = False
    org: str = ""              # 发文机关标志（红色大字）
    org_font: str = FONT_XBS
    org_size_pt: float = 36
    doc_number: str = ""       # 发文字号，如 “X政发〔2026〕5号”
    doc_number_font: str = FONT_BODY
    doc_number_size_pt: float = 16
    red_line: bool = True      # 红色分隔线


@dataclass
class CoverInfo:
    enabled: bool = False
    title: str = ""
    subtitle: str = ""
    org: str = ""
    date: str = ""
    extra_lines: list[str] = field(default_factory=list)


@dataclass
class Colophon:  # 版记
    enabled: bool = False
    lines: list[str] = field(default_factory=list)  # 如 ["抄送：××、××。", "××机关  2026年8月30日印发"]


@dataclass
class DocTemplate:
    name: str = "标准公文"
    # 页面
    page_width_mm: float = 210
    page_height_mm: float = 297
    margin_top_mm: float = 37
    margin_bottom_mm: float = 35
    margin_left_mm: float = 28
    margin_right_mm: float = 26
    # 正文
    body_font: str = FONT_BODY
    body_size_pt: float = 16            # 三号
    line_spacing_pt: float = 28         # 固定行距28磅
    first_line_indent_chars: float = 2
    align: str = "justify"
    # 标题层级样式
    h1: HeadingStyle = field(default_factory=lambda: HeadingStyle(font=FONT_HEI, bold=False))
    h2: HeadingStyle = field(default_factory=lambda: HeadingStyle(font=FONT_KAI, bold=False))
    h3: HeadingStyle = field(default_factory=lambda: HeadingStyle(font=FONT_BODY, bold=True))
    h4: HeadingStyle = field(default_factory=lambda: HeadingStyle(font=FONT_BODY, bold=True))
    # 页码（宋体四号，奇右偶左=外侧）
    page_number_enabled: bool = True
    page_number_font: str = FONT_SONG
    page_number_size_pt: float = 14     # 四号
    page_number_format: str = "— {page} —"
    # 各部件
    red_header: RedHeader = field(default_factory=RedHeader)
    cover: CoverInfo = field(default_factory=CoverInfo)
    toc_enabled: bool = True
    toc_title: str = "目  录"
    colophon: Colophon = field(default_factory=Colophon)
    # 汇编选项
    insert_material_titles: bool = True     # 每份材料标题作为一级标题
    material_title_prefix: str = ""         # 如 “材料一：”留空则直接用标题
    # 水印/密级标注
    watermark_text: str = ""                # 空=无水印，如“征求意见稿”“秘密★1年”
    watermark_opacity: float = 0.12
    watermark_angle: float = 45.0

    # -------------------------------------------------- 序列化
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, s: str) -> "DocTemplate":
        d = json.loads(s or "{}")
        if not isinstance(d, dict):
            raise ValueError("模板配置非法")
        # 嵌套对象重建
        h = {}
        for k in ("h1", "h2", "h3", "h4"):
            if k in d and isinstance(d[k], dict):
                h[k] = HeadingStyle(**d[k])
        rh = RedHeader(**d.pop("red_header", {})) if isinstance(d.get("red_header"), dict) else RedHeader()
        cv = CoverInfo(**d.pop("cover", {})) if isinstance(d.get("cover"), dict) else CoverInfo()
        co = Colophon(**d.pop("colophon", {})) if isinstance(d.get("colophon"), dict) else Colophon()
        for k, v in h.items():
            d[k] = v
        obj = cls(**d)
        obj.red_header, obj.cover, obj.colophon = rh, cv, co
        return obj

    def clone(self, new_name: str) -> "DocTemplate":
        obj = DocTemplate.from_json(self.to_json())
        obj.name = new_name
        return obj


def default_template() -> DocTemplate:
    """内置「标准公文」模板。"""
    t = DocTemplate()
    t.red_header.enabled = True
    t.red_header.org = "××单位文件"
    t.red_header.doc_number = "×政发〔2026〕1号"
    t.colophon.enabled = True
    t.colophon.lines = ["抄送：有关单位。", "××单位办公室  2026年8月30日印发"]
    return t
