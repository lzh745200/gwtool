# -*- coding: utf-8 -*-
"""公文模板模型：JSON 可序列化的排版参数集合。

默认模板遵循 GB/T 9704-2012《党政机关公文格式》常用参数：
  页边距 上37mm 下35mm 左28mm 右26mm；版心 156mm×225mm；每面 22 行、每行 28 字；
  正文仿宋_GB2312 三号(16pt) 行距固定28磅；
  一级标题黑体、二级楷体、三级仿宋加粗；页码四号、奇数页右/偶数页左（外侧）。

说明（2026-09-17）：本产品默认公文体系已切换为军队机关公文 ——
《军队机关公文处理工作条例》（2017-10-01 施行）及其版式标准
GJB 5100A-2017《军队机关公文格式》。该标准的页面参数（A4、天头 37mm、
订口 28mm、版心 156mm×225mm、22 行×28 字、正文三号仿宋、标题二号小标宋、
密级三号黑体、页码四号半角白体）与上列 GB/T 9704-2012 参数一致，
因此本模板无需改动即同时满足两套标准；差异在文种与要素编排，见 skeletons.py。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, asdict

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
    #   公文标题（材料标题/汇编名）：小标宋二号、居中、不缩进（GB/T 9704）
    #   一级「一、」黑体三号 / 二级「（一）」楷体三号 / 三级「1.」仿宋加粗
    doc_title: HeadingStyle = field(
        default_factory=lambda: HeadingStyle(font=FONT_XBS, size_pt=22,
                                             bold=False, align="center",
                                             indent_chars=0))
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
    # 目录收录到第几级标题（1~3）。默认 2：公文目录通常只到"一、/（一）"，
    # 收录三级会把"1. 2. 3."这类枚举清单也塞进目录（实测 30 条清单占 3 页）。
    toc_max_level: int = 2
    colophon: Colophon = field(default_factory=Colophon)
    # 汇编选项
    insert_material_titles: bool = True     # 每份材料标题作为一级标题
    material_title_prefix: str = ""         # 如 “材料{n}：”，{n} 替换为中文数字；留空则直接用标题
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
        # 嵌套对象重建（逐层过滤未知键，兼容跨版本存取）
        # ⚠ doc_title 必须一起重建：漏掉它时 from_json/clone 会让 doc_title
        # 退化成普通 dict，docxgen 取 `tpl.doc_title.font` 直接 AttributeError
        # ——批量汇编逐篇生成会整批失败（v1.15 加字段时踩到过）。
        h = {}
        for k in ("doc_title", "h1", "h2", "h3", "h4"):
            if k in d and isinstance(d[k], dict):
                h[k] = HeadingStyle(**_known_fields(HeadingStyle, d[k]))
        rh_raw = d.pop("red_header", {})
        cv_raw = d.pop("cover", {})
        co_raw = d.pop("colophon", {})
        rh = RedHeader(**_known_fields(RedHeader, rh_raw)) if isinstance(rh_raw, dict) else RedHeader()
        cv = CoverInfo(**_known_fields(CoverInfo, cv_raw)) if isinstance(cv_raw, dict) else CoverInfo()
        co = Colophon(**_known_fields(Colophon, co_raw)) if isinstance(co_raw, dict) else Colophon()
        for k, v in h.items():
            d[k] = v
        obj = cls(**_known_fields(cls, d))
        obj.red_header, obj.cover, obj.colophon = rh, cv, co
        return obj

    def clone(self, new_name: str) -> "DocTemplate":
        obj = DocTemplate.from_json(self.to_json())
        obj.name = new_name
        return obj


def material_label(prefix: str, index: int) -> str:
    """汇编材料自动编号前缀。prefix 形如 “材料{n}：”，{n} 替换为中文数字；
    不含 {n} 时原样用于每份材料；prefix 为空返回空串。"""
    if not (prefix or "").strip():
        return ""
    if "{n}" in prefix:
        return prefix.replace("{n}", _cn_num(index))
    return prefix


def _cn_num(n: int) -> str:
    """1-99 转中文数字（材料编号常规范围），超出回退阿拉伯数字。"""
    digits = "零一二三四五六七八九"
    if 1 <= n < 10:
        return digits[n]
    if n == 10:
        return "十"
    if 10 < n < 20:
        return "十" + digits[n % 10]
    if 20 <= n < 100:
        tens, ones = divmod(n, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")
    return str(n)


def _known_fields(dc_cls, data: dict) -> dict:
    """只保留 dataclass 已声明的字段。

    模板 config_json 存在库里，跨版本读写必然出现键不匹配：新版本写的字段
    被旧程序读、或字段改名后老库残留旧键。直接 cls(**d) 会抛 TypeError，
    让模板编辑器与汇编向导整个崩掉，因此忽略未知键、缺失键走默认值。
    """
    if not isinstance(data, dict):
        return {}
    allowed = {f.name for f in fields(dc_cls)}
    return {k: v for k, v in data.items() if k in allowed}


def cover_visible(tpl: "DocTemplate") -> bool:
    """封面是否真的要出一页：开关打开**且至少有一个要素非空**。

    为什么要加"非空"这一条：v1.15 起内置模板默认带封面（`default_template()`
    的 cover.enabled=True），而 `report.py` / `archive.export_manifest` 这类
    "复用公文体式"的通路也走 `default_template()`。它们不填封面要素，若只判
    开关，归档移交清单会凭空多出一页只有模板名的封面 —— 那不是这些文书的体式。
    因此统一在这里判"有内容才出封面"，docx 与 PDF 两条渲染通路共用同一判据
    （否则同一份内容导出两个格式，页数会不一样）。
    """
    cover = tpl.cover
    if not cover.enabled:
        return False
    lines = [cover.title, cover.subtitle, cover.org, cover.date,
             *(cover.extra_lines or [])]
    return any((s or "").strip() for s in lines)


def default_template() -> DocTemplate:
    """内置「标准公文」模板。

    v1.15：默认带封面（`cover.enabled=True`）——「一键汇编」的产物是一份要
    交出去的汇编件，没有封面不成体式；封装名/单位/日期由向导第 2 步覆盖
    （`compile_wizard._apply_cover_fields`），这里给的是**无输入时也不空**的
    兜底值。
    """
    t = DocTemplate()
    t.red_header.enabled = True
    t.red_header.org = "××单位文件"
    t.red_header.doc_number = "×政发〔2026〕1号"
    t.cover.enabled = True
    t.cover.title = "材料汇编"
    t.colophon.enabled = True
    t.colophon.lines = ["抄送：有关单位。", "××单位办公室  2026年8月30日印发"]
    return t
