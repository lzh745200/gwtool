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


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _looks_like_word_doc(path: str) -> bool:
    """粗筛：是不是一个可能含 Word 正文的 OLE 复合文档。

    为什么要先筛再交给 COM：把明显不是 Word 文档的文件（全零、截断的垃圾、
    纯文本）喂给 WPS/Word，Open 失败时 COM 层的报错不一定是 Python 异常 ——
    实测会抛 Windows 原生 SEH 异常（0x800706be，RPC 服务已崩），这种异常
    `except Exception` 根本接不住，会直接把整个进程带走（批量导入时表现为
    “导入到一半程序没了”）。所以在进 COM 之前先按结构把明显不可能的挡掉。

    判定看三层：OLE 文件头 + WordDocument 流存在 + 流首 FIB 魔数 0xA5EC
    （与纯 Python 解析器同一标准）。只看前两层不够 —— 真实探测发现 WPS
    对 FIB 魔数被破坏的 doc 照样“成功打开”，却产出整篇乱码的 docx（比
    提取不到还糟）；而这类文件恰恰也是 SEH 崩进程的高危对象，理应拦在
    COM 之外，交给纯 Python 链路的 raw_scan 兜底。

    读不动就返回 False，交给后面的 LibreOffice / 纯 Python 兜底。
    """
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != _OLE_MAGIC:
                return False
    except OSError:
        return False
    try:
        import olefile

        ole = olefile.OleFileIO(path)
        try:
            if not ole.exists("WordDocument"):
                return False
            word = ole.openstream("WordDocument").read(2)
            return len(word) == 2 and struct.unpack("<H", word)[0] == 0xA5EC
        finally:
            ole.close()
    except Exception:
        # olefile 打不开（结构坏）—— 同样不值得交给 COM
        return False


# ------------------------------------------------------------------ 转换路径
def _convert_via_com(path: str) -> str | None:
    """Windows 下用 Word/WPS COM 转 docx，返回临时 docx 路径。

    每个 progid 无论成功失败都必须把派发出来的 COM 实例 Quit 掉，并显式
    释放引用：Dispatch 之后只要 Open/SaveAs2/Close 任一步出错，旧实现的
    `except: continue` 会直接跳到下一个 progid，把已经起来的隐藏 Word 进程
    丢在原地 —— 批量导入 .doc 时会累积成十几个隐藏进程，拖到最后 COM 服务
    崩掉，整个导入进程跟着挂。

    进入 COM 之前先做结构粗筛（_looks_like_word_doc）：喂给 Word 一个明显
    不是 Word 文档的文件，Open 失败时可能抛原生 SEH 异常（0x800706be），
    `except Exception` 接不住，会直接终结进程。
    """
    if not shutil.which("cmd"):
        return None
    if not _looks_like_word_doc(path):
        return None
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None
    for progid in ("kwps.Application", "wps.Application", "Word.Application"):
        app = doc = None
        try:
            app = win32com.client.Dispatch(progid)
            app.Visible = False
            doc = app.Documents.Open(str(Path(path).resolve()), ReadOnly=True)
            out = Path(tempfile.gettempdir()) / (Path(path).stem + "_conv.docx")
            doc.SaveAs2(str(out), FileFormat=16)  # 16 = wdFormatDocumentDefault(docx)
            return str(out)
        except Exception:
            continue
        finally:
            # 顺序反过来关：先文档后应用；任何一步失败都不能挡住后续清理，
            # 否则又会把进程漏在这里。
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:
                pass
            try:
                if app is not None:
                    app.Quit()
            except Exception:
                pass
            doc = app = None
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
        text = parse_docx(conv).plain_text()
        if text.strip():
            return text
        # WPS 对 FIB 损坏的 doc 可能“成功打开”却产出空文档 —— 空结果不算
        # 成功，继续降级（真实探测发现：此时纯 Python 路径本可救回全文）。
        # 真为空文档时后面 raw_scan 也返回空，无额外代价。
    # 2) LibreOffice
    conv = _convert_via_soffice(path)
    if conv:
        from .docx_parser import parse_docx
        text = parse_docx(conv).plain_text()
        if text.strip():
            return text
    # 3) 纯 Python 分片表解析
    try:
        return _extract_text_olefile(path)
    except Exception:
        pass
    # 4) 最后兜底：扫描流内可打印片段。
    # 这一级是「绝不再抛」的底线 —— 前面每一级都可能失败（结构坏、库版本差异），
    # 兜底自己再抛就等于让 parse_doc 直接炸给用户看。olefile 面对畸形 OLE 会抛
    # ValueError（如 "bytes length not a multiple of item size"），必须接住并
    # 退化成空文本，由上层按「提取不到内容」处理。
    try:
        return _extract_text_raw_scan(path)
    except Exception:
        return ""


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
            # PCD 布局（Word97）：2B 标志位 + 4B fc + 2B prm，共 8 字节。
            # fc 取偏移 +2 处的 4 字节；此前写成 "<HII"（10 字节）越过 PCD
            # 边界，最后一个分片必然 struct.error —— 纯 Python 解析整条
            # 路径从未真正工作过，一直靠 raw_scan 兜底伪装正常。
            for k in range(count):
                fc = struct.unpack_from("<I", plc, base + 8 * k + 2)[0]
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
