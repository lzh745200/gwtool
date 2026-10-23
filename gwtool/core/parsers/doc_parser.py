# -*- coding: utf-8 -*-
"""DOC（Word 97-2003）解析：尽力提取。

转换策略（v1.9b 起**默认纯 Python 优先**）
------------------------------------------
四条提取路径：
  ① 纯 Python 分片表（olefile 读 WordDocument 流 + CLX 分片表，完整率约 95%+）；
  ② 纯 Python 兜底（raw_scan 扫描流内可打印 UTF-16 片段，畸形文件也能出文本）；
  ③ LibreOffice（soffice --headless 转 docx）—— 只提纯文本时收益很小；
  ④ Windows COM（Word / WPS 转 docx）。

**为什么默认不再走 COM**：`parse_doc` 只取 `plain_text()`（见 `_text_from_conv`），
COM 转出来的 docx 里那些"格式保真"收益在这里被整体丢弃，代价却全额支付：
实测 5 份 .doc，COM 链 8.53 s vs 纯 Python 0.003 s（约 2405×），本机复测
单份 1.55–6.38 s vs 2.7 ms。更糟的是 COM 会把用户的 Word/WPS 真正拉起来，
批量导入 200 份就是几分钟的"看起来卡死"。

因此默认链路是 **纯 Python →（质量可疑时才）LibreOffice / COM 兜底**。
**质量兜底**（v1.9b 追加）：纯 Python 的结果"明显不可信"时不会直接交付 ——
判据见 `is_suspicious_extraction`（文件 >20KB 却提不出 80 字 / 替换符占比 >5% /
结构是 Word 却一个字都没有），满足且本机 COM 可用时**自动用 COM 重试一次**。
所以"默认快"不等于"难文件就放弃"：正常文书走毫秒级纯 Python，可疑文件才付
COM 的 1.5~6 秒。

需要"保真"（例如怀疑纯 Python 漏段落、或要保留页眉页脚等结构）时显式开启：

    parse_doc(path, prefer_com=True)          # 单次调用
    set_prefer_com(True)                      # 进程内默认（设置界面调用）
    set GWTOOL_DOC_PREFER_COM=1               # 环境变量（CI / 便携模式）

开启后仍保持 v1.8 的"保真链"：COM → LibreOffice → 纯 Python → raw_scan。
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from ..model import Block, DocTree, HEADING, PARAGRAPH
from .txt_parser import _HEADING_RE

# ---------------------------------------------------------------- 策略与入口
# 环境变量入口（默认关）：'1' / 'true' / 'yes' / 'on' 视为开启
_ENV_PREFER_COM = "GWTOOL_DOC_PREFER_COM"
# 进程内显式设置（设置界面/测试用）；None 表示"未设置，按环境变量/默认"。
_prefer_com_setting: bool | None = None

# ---- 质量兜底判据（见 is_suspicious_extraction）----
# 只有"明显不可信"才动用 COM 重试一次，阈值刻意保守，避免把正常的短文档
# （通知、便函）也拖进 1.5~6 秒的 COM 路径。
_SUSPICIOUS_MIN_CHARS = 80          # 去空白后正文不足 80 字
_SUSPICIOUS_MIN_BYTES = 20 * 1024   # 而原文件 > 20KB —— 只提这么点明显漏了
_SUSPICIOUS_BAD_RATIO = 0.05        # 替换符/控制符占比 > 5%


def set_prefer_com(enabled: bool | None) -> None:
    """设置"启用 COM 保真转换"的进程内默认值；传 None 恢复为按环境变量。

    给设置界面留的入口（本模块不依赖 config/UI，便于单独测试）。
    **默认关**：纯 Python 比 COM 快约 2400×（见模块 docstring 实测）。
    """
    global _prefer_com_setting
    _prefer_com_setting = None if enabled is None else bool(enabled)


def prefer_com_enabled() -> bool:
    """当前默认是否启用 COM 保真转换（显式设置 > 环境变量 > 默认关）。"""
    if _prefer_com_setting is not None:
        return _prefer_com_setting
    return str(os.environ.get(_ENV_PREFER_COM, "")).strip().lower() in (
        "1", "true", "yes", "on")


def _com_available() -> bool:
    """本机是否存在可用的 COM 通道（Windows + cmd + pywin32）。

    只做"能不能试"的判断，不真正 Dispatch：质量兜底要用它决定是否值得
    再看一眼 COM。抽成函数便于测试注入（CI 上无 COM，不能依赖真机）。
    """
    if not shutil.which("cmd"):
        return False
    try:
        import win32com.client  # noqa: F401  # type: ignore
    except Exception:
        return False
    return True


def is_suspicious_extraction(path: str, text: str) -> bool:
    """纯 Python 提取结果是否"明显不可信"，值得再用 COM 兜一次。

    只认**确定性**信号（不做"像不像公文"这种语义判断）：
      ① 文件不小却几乎没提出字：去空白后 < 80 字、而文件 > 20KB；
      ② 替换符/控制符泛滥：``U+FFFD`` 与 C0 控制符占非空白字符 > 5%
         （编码猜错、分片表解析跑偏时的典型症状）；
      ③ 结构上是 Word 文档却一个字都没提出来（``_looks_like_word_doc``）。
    都不是就返回 False —— 正常文书不该为了一次怀疑付 1.5~6 秒 COM 成本。
    """
    body = "".join(ch for ch in (text or "") if not ch.isspace())
    try:
        size = Path(path).stat().st_size
    except OSError:
        size = 0
    if len(body) < _SUSPICIOUS_MIN_CHARS and size > _SUSPICIOUS_MIN_BYTES:
        return True
    if body:
        bad = sum(1 for ch in body
                  if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r"))
        if bad / len(body) > _SUSPICIOUS_BAD_RATIO:
            return True
    if not body and _looks_like_word_doc(path):
        return True
    return False


def parse_doc(path: str, *, prefer_com: bool | None = None) -> DocTree:
    """解析 .doc。

    ``prefer_com``：True=先走 COM 保真转换（慢，会拉起 Word/WPS）；
    False=纯 Python 优先；None（默认）=取 `prefer_com_enabled()`（默认关）。
    无论哪种，**提取结果明显不可信且本机 COM 可用**时会自动用 COM 重试一次
    （见 `is_suspicious_extraction`），所以"默认快"不等于"放弃难文件"。
    """
    use_com = prefer_com_enabled() if prefer_com is None else bool(prefer_com)
    text = _extract_text(path, prefer_com=use_com)
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
        outdir = None
        try:
            app = win32com.client.Dispatch(progid)
            app.Visible = False
            doc = app.Documents.Open(str(Path(path).resolve()), ReadOnly=True)
            # 独立临时目录：调用方读完就 rmtree（见 _extract_text）。
            # 老实现直接写 %TEMP%\<stem>_conv.docx 且从不删除，导入多少 .doc
            # 就在用户临时目录里留多少文件；同名 stem 之间还会互相覆盖，
            # 并发导入两个同名文件时可能读到对方的内容。
            outdir = tempfile.mkdtemp(prefix="gwtool_doccom_")
            out = Path(outdir) / (Path(path).stem + "_conv.docx")
            doc.SaveAs2(str(out), FileFormat=16)  # 16 = wdFormatDocumentDefault(docx)
            if out.exists():
                return outdir          # 目录交给调用方清理
            raise OSError("转换未产出文件")
        except Exception:
            if outdir:
                shutil.rmtree(outdir, ignore_errors=True)
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
    """soffice 转 docx，返回**产物所在临时目录**（由调用方负责 rmtree）。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    outdir = tempfile.mkdtemp(prefix="gwtool_doc_")
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", outdir, path],
            capture_output=True, timeout=120, check=False)
        out = Path(outdir) / (Path(path).stem + ".docx")
        if out.exists():
            return outdir
        shutil.rmtree(outdir, ignore_errors=True)
        return None
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True)
        return None


# ------------------------------------------------------------------ 纯Python解析
def _extract_text(path: str, *, prefer_com: bool = False) -> str:
    """提取正文纯文本。``prefer_com`` 见模块 docstring。

    默认（``prefer_com=False``）：纯 Python 优先，**质量兜底**如下 ——
      · 结果正常（见 `is_suspicious_extraction`）→ 直接用（毫秒级，不碰 COM）；
      · 结果明显不可信（几乎没字 / 替换符泛滥 / 结构是 Word 却提不出字）
        → 先试 LibreOffice，仍有问题且本机 COM 可用时**再用 COM 重试一次**；
      · 所有兜底都不如原结果时，返回原结果（宁可给不完美的文本，也不给空）。
    这样"默认快 ~2400×"与"难文件仍能救回"两件事同时成立。
    """
    if prefer_com:
        # 保真链（v1.8 顺序）：COM → LibreOffice → 纯 Python → raw_scan
        text = _extract_via_office(path, use_com=True)
        return text if text.strip() else _extract_pure(path)

    text = _extract_pure(path)
    if text.strip() and not is_suspicious_extraction(path, text):
        return text

    # ---- 质量兜底：结果不可信时才动用外部组件（各一次，不循环）
    alt = _extract_via_office(path, use_com=False)
    if alt.strip() and not is_suspicious_extraction(path, alt):
        return alt
    if _com_available():
        com_text = _extract_via_office(path, use_com=True)
        if com_text.strip():
            return com_text
    return alt or text


def _text_from_conv(conv_dir: str | None, path: str, suffix: str) -> str:
    """把 LibreOffice/COM 的转换产物读成纯文本，读完**立即删临时目录**。

    转换产物落在独立临时目录里（`mkdtemp`），所以同名 stem 并发导入不会
    互相覆盖；无论解析成功还是抛异常都在 finally 里清理。
    空结果**不算成功**（WPS 对 FIB 损坏的 doc 会"成功打开"却产出空文档）。
    """
    if not conv_dir:
        return ""
    try:
        conv = str(Path(conv_dir) / (Path(path).stem + suffix))
        if Path(conv).exists():
            from .docx_parser import parse_docx
            return parse_docx(conv).plain_text()
        return ""
    finally:
        shutil.rmtree(conv_dir, ignore_errors=True)


def _extract_via_office(path: str, *, use_com: bool) -> str:
    """外部办公组件转换（COM 可选）。返回纯文本，失败/空结果返回 ""。"""
    if use_com:
        text = _text_from_conv(_convert_via_com(path), path, "_conv.docx")
        if text.strip():
            return text
    return _text_from_conv(_convert_via_soffice(path), path, ".docx")


def _extract_pure(path: str) -> str:
    """纯 Python 两级：分片表 → raw_scan；两级都失败返回 ""，绝不抛。

    raw_scan 是「绝不再抛」的底线：olefile 面对畸形 OLE 会抛 ValueError
    （如 "bytes length not a multiple of item size"），必须接住并退化成空文本，
    由上层按「提取不到内容」处理。
    """
    try:
        text = _extract_text_olefile(path)
        if text.strip():
            return text
    except Exception:
        pass
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
            if count <= 0:
                # 明确报错而不是 return []：返回空表会被上层当成
                # "解析成功、只是没有内容"，把结构损坏伪装成空文档，
                # 排查时完全看不到真实原因（老实现正是如此）。
                raise ValueError(f"CLX 分片数异常（count={count}，lcb={lcb}）")
            cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
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
