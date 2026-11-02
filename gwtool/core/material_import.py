# -*- coding: utf-8 -*-
"""把「任意文档 / 任意文字」导入为写作参考素材包（P4）。

背景：v1.16 的写作参考库只认外部构建的固定语料包（index.json + annotations/），
用户无法把自己手头的通知、总结、讲话稿、PDF 甚至一段粘贴文字放进去检索。
本模块把这些输入统一转成**同一种只读语料包结构**，`MaterialPack` 与对话框
零改动即可检索/预览/插入：

    index.json
    annotations/<id>.json
    corpus/<id>.md
    templates.json

支持：
  * 任意文件：走 `importer.parse_any`（docx/doc/wps/txt/rtf/pdf/md/html，扫描件
    走内置 OCR）；解析失败时再按纯文本解码兜底（UTF-8/GB18030/UTF-16）；
  * 粘贴文字：直接 `text_to_tree`；
  * 去重（内容 sha256）、标题重复自动加序号；
  * 原子写入：先写临时目录再替换，避免半包。

包默认写到 `<数据目录>/material_packs/我的素材/`，与外部公版语料包互不影响。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from .importer import parse_any, text_to_tree
from .model import TABLE

_ROLE_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("过渡", ("因此", "然而", "同时", "一方面", "另一方面", "综上所述",
              "由此可见", "与此同时", "接下来", "在此基础上")),
    ("结尾", ("最后", "总之", "下一步", "让我们", "必将", "努力开创",
              "为……而", "谢谢")),
    ("论据", ("据统计", "数据显示", "例如", "比如", "据调查", "实践证明",
              "数据显示", "％", "%", "万人", "亿元")),
    ("论点", ("必须", "应当", "要坚持", "要坚持", "要求", "指出", "强调",
              "务必", "切实", "坚决")),
)

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*")
_MAX_SENTS = 20000          # 单篇防爆上限（正常公文远小于此）
_LOCK = threading.RLock()


class MaterialImportError(RuntimeError):
    """导入素材失败（目录不可写、包损坏等）。"""


@dataclass
class ImportSummary:
    imported: int = 0
    skipped_dup: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    chars: int = 0

    def text(self) -> str:
        parts = [f"已导入 {self.imported} 篇"]
        if self.skipped_dup:
            parts.append(f"重复跳过 {self.skipped_dup} 篇")
        if self.failed:
            parts.append(f"失败 {len(self.failed)} 篇")
        parts.append(f"新增汉字 {self.chars:,}")
        msg = "，".join(parts) + "。"
        if self.failed:
            msg += "\n\n失败明细：\n" + "\n".join(
                f"· {p}：{r}" for p, r in self.failed[:5])
        return msg


def user_pack_dir() -> Path:
    """用户自建素材包目录（数据目录内，随备份/迁移一起走）。"""
    return paths.app_data_dir() / "material_packs" / "我的素材"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def _han(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _role_of(sent: str) -> str:
    for role, keys in _ROLE_KEYS:
        if any(k in sent for k in keys):
            return role
    return "其他"


def _sentences(paragraphs: list[str]) -> list[dict]:
    out: list[dict] = []
    for para in paragraphs:
        p = (para or "").strip()
        if not p:
            continue
        for sent in _SENT_SPLIT.split(p):
            s = sent.strip()
            if len(s) < 4:          # 太短（"一、" "特此通知"）不进仿写库
                continue
            out.append({"text": s, "role": _role_of(s), "rule": "本机导入·角色启发式",
                        "conf": 0.60})
            if len(out) >= _MAX_SENTS:
                return out
    return out


def _paragraphs(tree) -> list[str]:
    out: list[str] = []
    for b in tree.blocks:
        if b.type == TABLE and b.rows:
            out.extend(" | ".join(cell for cell in row) for row in b.rows)
        elif b.text:
            out.append(b.text)
    return out


def _decode_any(raw: bytes) -> str:
    """多编码兜底解码（UTF-8/GB18030/UTF-16/Big5，最后替换式 UTF-8）。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _binary_reason(raw: bytes) -> str:
    """这段字节"根本不是文本"时返回给用户的原因，看着像文本则返回空串。

    为什么必须先挡：兜底解码会把**任意字节**按 GB18030 解成字符，于是一张 PNG
    变成一万字、一半是替换符的"素材"进了库（实测 10247 字里 5120 个 U+FFFD），
    界面还显示"已导入"，而这些乱码还会被切成句子进仿写库，污染写作参考。
    判据用两条硬信号，不靠扩展名白名单（用户改扩展名是常事）：
      · 前 4 KB 含空字节 —— 正常文本几乎不会出现 NUL；
      · 解码后不可读字符（替换符或除制表/换行外的 C0 控制符）占比 > 10%。
    """
    if b"\x00" in raw[:4096]:
        return "疑似二进制文件（含空字节），不是文本类素材"
    sample = _decode_any(raw)[:4000]
    if sample:
        bad = sum(1 for ch in sample
                  if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r"))
        if bad / len(sample) > 0.10:
            return "疑似二进制文件（大量不可读字符），不是文本类素材"
    return ""


def _load_index(pack: Path) -> list[dict]:
    """读索引：文件不存在返回空表，**解析失败则报错，绝不当作空表**。

    旧实现把"解析失败"和"没有索引"一样返回空表，于是下一次导入会以一条新记录
    覆盖整个 index.json —— 用户此前导入的素材凭空从列表里消失（语料文件还在
    磁盘上，但不再被引用，等于静默丢库）。
    """
    p = pack / "index.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MaterialImportError(
            f"素材索引无法读取（{p}）：{exc}。"
            "为避免覆盖原有记录，本次导入已中止；"
            "可先备份该文件，确认无误后删除它再重新导入。") from exc
    if not isinstance(data, list):
        raise MaterialImportError(
            f"素材索引格式异常（{p}：顶层应为数组），本次导入已中止以免覆盖。")
    return data


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def ensure_pack(pack_dir: Path | None = None) -> Path:
    pack = Path(pack_dir) if pack_dir else user_pack_dir()
    (pack / "annotations").mkdir(parents=True, exist_ok=True)
    (pack / "corpus").mkdir(parents=True, exist_ok=True)
    if not (pack / "index.json").is_file():
        _write_json(pack / "index.json", [])
    if not (pack / "templates.json").is_file():
        _write_json(pack / "templates.json", [])
    return pack


def _add_doc(pack: Path, title: str, text: str, source: str,
             summary: ImportSummary) -> None:
    """把一篇纯文本写进包（调用方持锁）。"""
    index = _load_index(pack)
    digest = _sha256(text)
    if any(str(d.get("sha256") or "") == digest for d in index):
        summary.skipped_dup += 1
        return
    base_title = (title or "未命名素材").strip()[:80]
    titles = {str(d.get("title") or "") for d in index}
    final_title = base_title
    n = 2
    while final_title in titles:
        final_title = f"{base_title}（{n}）"
        n += 1
    doc_id = uuid.uuid4().hex[:16]
    paras = [p for p in text.splitlines() if p.strip()]
    sents = _sentences(paras)
    if not sents:                       # 没有可入库句子就不建空篇
        summary.failed.append((base_title, "没有可入库的正文句子"))
        return
    (pack / "corpus" / f"{doc_id}.md").write_text(
        f"# {final_title}\n\n{text.strip()}\n", encoding="utf-8")
    _write_json(pack / "annotations" / f"{doc_id}.json", {
        "doc_id": doc_id,
        "annotated_paragraphs": [{"sentences": sents}],
    })
    index.append({
        "id": doc_id, "title": final_title, "source": source,
        "work": "自建素材", "han_chars": _han(text),
        "sha256": digest, "tags": [],
    })
    _write_json(pack / "index.json", index)
    summary.imported += 1
    summary.doc_ids.append(doc_id)
    summary.chars += _han(text)


def import_paths(paths_in: list[str | Path], pack_dir: Path | None = None,
                 progress=None) -> ImportSummary:
    """批量导入文件（任意受支持格式；不支持的按纯文本兜底）。"""
    pack = ensure_pack(pack_dir)
    summary = ImportSummary()
    files = [Path(p) for p in paths_in]
    with _LOCK:
        for i, path in enumerate(files):
            if progress:
                try:
                    progress(i + 1, len(files))
                except Exception:
                    pass
            try:
                if not path.is_file():
                    summary.failed.append((str(path), "文件不存在"))
                    continue
                res = parse_any(str(path))
                if res.ok and res.tree is not None:
                    text = res.tree.plain_text()
                    title = (res.tree.title or path.stem).strip()
                else:
                    # 任意文本兜底：解析器不认识/坏了，也允许进库。
                    # 但先挡二进制——否则图片/压缩包会被解成乱码当素材收下。
                    raw = path.read_bytes()
                    why = _binary_reason(raw)
                    if why:
                        summary.failed.append((path.name, why))
                        continue
                    text = _decode_any(raw)
                    title = path.stem
                    if not text.strip():
                        summary.failed.append((path.name, res.error or "无文字"))
                        continue
                text = text.strip()
                if not text:
                    summary.failed.append((path.name, "无文字内容"))
                    continue
                _add_doc(pack, title, text, "本机导入", summary)
            except Exception as exc:      # 单篇失败不影响其余
                summary.failed.append((path.name, str(exc)[:120]))
    return summary


def import_text(title: str, text: str,
                pack_dir: Path | None = None) -> ImportSummary:
    """导入粘贴的一段文字。"""
    pack = ensure_pack(pack_dir)
    summary = ImportSummary()
    body = (text or "").strip()
    if not body:
        summary.failed.append((title or "粘贴文本", "内容为空"))
        return summary
    with _LOCK:
        try:
            tree = text_to_tree(body, title_hint=(title or "").strip())
            _add_doc(pack, (title or tree.title or "粘贴文本"),
                     tree.plain_text().strip() or body, "粘贴文本", summary)
        except Exception as exc:
            summary.failed.append((title or "粘贴文本", str(exc)[:120]))
    return summary


def remove_doc(doc_id: str, pack_dir: Path | None = None) -> bool:
    """从自建包删除一篇（对话框「删除此素材」用；返回是否真的删掉）。"""
    pack = ensure_pack(pack_dir)
    with _LOCK:
        index = _load_index(pack)
        rest = [d for d in index if str(d.get("id")) != doc_id]
        if len(rest) == len(index):
            return False
        _write_json(pack / "index.json", rest)
        for sub, name in (("annotations", f"{doc_id}.json"),
                          ("corpus", f"{doc_id}.md")):
            try:
                (pack / sub / name).unlink()
            except OSError:
                pass
        return True
