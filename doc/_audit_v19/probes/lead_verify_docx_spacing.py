# -*- coding: utf-8 -*-
"""Lead 复核：docx 行距/段距参数（静态 XML 取证）。"""
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, ".")

from gwtool.core import docxgen
from gwtool.core.model import Block, DocTree, HEADING, PARAGRAPH
from gwtool.core.template import default_template

out = Path(tempfile.mkdtemp()) / "probe.docx"
tree = DocTree(title="行距取证", blocks=[
    Block(type=HEADING, level=1, text="一、总体要求"),
    Block(type=PARAGRAPH, text="为深入贯彻落实上级部署要求，现就有关事项通知如下。" * 4),
])
docxgen.generate_docx([tree], default_template(), str(out))

with zipfile.ZipFile(out) as z:
    styles = z.read("word/styles.xml").decode("utf-8")
    doc = z.read("word/document.xml").decode("utf-8")
    settings = z.read("word/settings.xml").decode("utf-8")

print("=== styles.xml docDefaults 的 spacing ===")
import re
m = re.search(r"<w:docDefaults>.*?</w:docDefaults>", styles, re.S)
print((m.group(0)[:600] if m else "未找到 docDefaults"))

print("\n=== 正文段落的 pPr spacing ===")
paras = re.findall(r"<w:pPr>.*?</w:pPr>", doc, re.S)
shown = 0
for p in paras:
    if "spacing" in p and shown < 4:
        print(p[:400])
        shown += 1
print(f"(共 {len(paras)} 个 pPr，含 spacing 的前 {shown} 个已展示)")

print("\n=== settings.xml 的 updateFields ===")
print("updateFields 存在:", "updateFields" in settings)
idx = settings.find("updateFields")
print(settings[max(0, idx - 80): idx + 120] if idx >= 0 else "")

print("\n=== TOC 域是否带 dirty ===")
print("fldChar dirty 出现次数:", doc.count('w:dirty="true"'))
print("TOC 指令:", re.findall(r'TOC[^<]{0,40}', doc)[:2])
