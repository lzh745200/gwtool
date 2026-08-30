# -*- coding: utf-8 -*-
"""DOC（Word 97-2003）解析：尽力提取。

策略（按优先级自动降级）：
  1. Windows：通过 COM 调用本机 Word 或 WPS 完美转换 docx（若安装）；
  2. 有 LibreOffice（soffice）时 headless 转换 docx；
  3. 纯 Python：olefile 读取 WordDocument 流，解析 FIB + 分片表(piece table)
     提取文本（完整率约 95%+，不含图片/表格线等格式信息）。
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from ..model import Block, DocTree, HEADING, PARAGRAPH
from .txt_parser import _HEADING_RE


def parse_doc(path: str) -> DocTree:
    text = _extract_text(path)
    return _text_to_tree(text, title_hint=Path(path).stem)


# ------------------------------------------------------------------ 转换路径
def _convert_via_com(path: str) -> str | None:
    """Windows 下用 Word/WPS COM 转 docx，返回临时 docx 路径。"""
    if not shutil.which("cmd"):
        return None
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None
    for progid in ("kwps.Application", "wps.Application", "Word.Application"):
        try:
            app = win32com.client.Dispatch(progid)
            app.Visible = False
            doc = app.Documents.Open(str(Path(path).resolve()), ReadOnly=True)
            out = Path(tempfile.gettempdir()) / (Path(path).stem + "_conv.docx")
            doc.SaveAs2(str(out), FileFormat=16)  # 16 = wdFormatDocumentDefault(docx)
            doc.Close(False)
            app.Quit()
            return str(out)
        except Exception:
            continue
    return None


def _convert_via_soffice(path: str) -> str | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    outdir = tempfile.mkdtemp(prefix="gwtool_doc_")
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", outdir, path],
            capture_output=True, timeout=120, check=False)
        out = Path(outdir) / (Path(path).stem + ".docx")
        return str(out) if out.exists() else None
    except Exception:
        return None


# ------------------------------------------------------------------ 纯Python解析
def _extract_text(path: str) -> str:
    # 1) COM 完美转换
    conv = _convert_via_com(path)
    if conv:
        from .docx_parser import parse_docx
        return parse_docx(conv).plain_text()
    # 2) LibreOffice
    conv = _convert_via_soffice(path)
    if conv:
        from .docx_parser import parse_docx
        return parse_docx(conv).plain_text()
    # 3) 纯 Python 分片表解析
    try:
        return _extract_text_olefile(path)
    except Exception:
        return _extract_text_raw_scan(path)


def _extract_text_olefile(path: str) -> str:
    import olefile

    ole = olefile.OleFileIO(path)
    try:
        word = ole.openstream("WordDocument").read()
        if len(word) < 0x200 or struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
            raise ValueError("不是 Word 97-2003 文档")
        # FIB: fcClx/lcbClx 位于 0x01A2/0x01A6（Word97 布局）
        fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
        # fWhichTblStm: FIB bit 9 of flags(0x000A 处)
        flags = struct.unpack_from("<H", word, 0x000A)[0]
        tbl_name = "1Table" if flags & 0x0200 else "0Table"
        try:
            table = ole.openstream(tbl_name).read()
        except Exception:
            table = ole.openstream("0Table" if tbl_name == "1Table" else "1Table").read()
        clx = table[fc_clx:fc_clx + lcb_clx]
        pieces = _parse_clx(clx)
        chunks: list[str] = []
        for cp_start, cp_end, fc in pieces:
            compressed = bool(fc & 0x40000000)
            offset = (fc & 0x3FFFFFFF) // 2 if compressed else (fc & 0x3FFFFFFF)
            length = cp_end - cp_start
            if compressed:
                raw = word[offset:offset + length]
                chunks.append(raw.decode("cp1252", errors="replace"))
            else:
                raw = word[offset:offset + length * 2]
                chunks.append(raw.decode("utf-16-le", errors="replace"))
        text = "".join(chunks)
    finally:
        ole.close()
    return _normalize_doc_text(text)


def _parse_clx(clx: bytes) -> list[tuple[int, int, int]]:
    """解析 CLX -> [(cpStart, cpEnd, fc), ...]；fc 含压缩标志位。"""
    i, n = 0, len(clx)
    while i < n:
        t = clx[i]
        if t == 1:  # Prc
            cb = struct.unpack_from("<H", clx, i + 1)[0]
            i += 3 + cb
        elif t == 2:  # Pcdt
            lcb = struct.unpack_from("<I", clx, i + 1)[0]
            plc = clx[i + 5:i + 5 + lcb]
            count = (lcb - 4) // 12  # n+1 个 CP(4B) + n 个 PCD(8B)
            cps = struct.unpack_from("<%dI" % (count + 1), plc, 0)
            out = []
            base = 4 * (count + 1)
            for k in range(count):
                _, fc, _ = struct.unpack_from("<HII", plc, base + 8 * k)
                out.append((cps[k], cps[k + 1], fc))
            return out
        else:
            raise ValueError("CLX 结构异常")
    raise ValueError("未找到分片表")


def _normalize_doc_text(text: str) -> str:
    # 控制字符清理：\r 段落、\x07 单元格/行尾、\x0b 软回车等
    text = text.replace("\r\x07", "\n").replace("\x07", "\n")
    text = text.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    text = text.replace("\x13", "").replace("\x14", "").replace("\x15", "")
    out = []
    for ch in text:
        if ch == "\n" or (ord(ch) >= 32 and ch != "\x7f"):
            out.append(ch)
    return "".join(out)


def _extract_text_raw_scan(path: str) -> str:
    """最后兜底：扫描流内可打印 UTF-16 中文片段。"""
    import olefile
    ole = olefile.OleFileIO(path)
    try:
        data = ole.openstream("WordDocument").read()
    finally:
        ole.close()
    parts = []
    buf = []
    i = 0
    while i < len(data) - 1:
        code = data[i] | (data[i + 1] << 8)
        if (0x4E00 <= code <= 0x9FFF) or code in (0x3001, 0x3002, 0xFF0C, 0xFF1B,
                                                  0xFF1A, 0xFF1F, 0xFF01, 0x201C, 0x201D) \
                or (0x30 <= code <= 0x39) or (0x41 <= code <= 0x5A) or (0x61 <= code <= 0x7A):
            buf.append(chr(code))
            i += 2
        else:
            if len(buf) >= 8:
                parts.append("".join(buf))
            buf = []
            i += 2
    if len(buf) >= 8:
        parts.append("".join(buf))
    return "\n".join(parts)


def _text_to_tree(text: str, title_hint: str = "") -> DocTree:
    tree = DocTree(title=title_hint)
    paras = [p.strip() for p in text.split("\n")]
    paras = [p for p in paras if p]
    for idx, para in enumerate(paras):
        if idx == 0 and len(para) <= 50:
            tree.title = para
            tree.blocks.append(Block(type=HEADING, level=1, text=para))
        elif _HEADING_RE.match(para) and len(para) <= 40:
            tree.blocks.append(Block(type=HEADING, level=2, text=para))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=para))
    return tree
