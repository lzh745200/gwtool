# -*- coding: utf-8 -*-
"""「精度增强包」的导入 / 校验 / 卸载。

定位
----
本产品主包不带神经网络模型（原因见 `paths.enhance_dir` 的注释）。需要更高
精度时，用户在一台有网机器上取得一个 **纯离线** 的 `.zip` 增强包，拷到本机
导入即可。本模块只负责「安全地把包装进来」，不做任何网络访问。

包格式（`schema = 1`）
---------------------
    manifest.json     必填，见下
    model.onnx        必填（backend=onnx），ONNX 量化模型
    vocab.txt         必填（kind=csc），每行一个 token，行号即 id
    tokenizer.json    必填（kind=cgec），HuggingFace 分词器配置

manifest.json 字段
------------------
    schema     int      必须 == 1
    name       str      包名
    kind       str      csc（拼写纠错）/ cgec（语法纠错），决定它挂到哪一层、
                        以及安装进哪个槽位；缺省按 csc 处理
    version    str      包版本
    license    str      **必填**：模型与数据集的许可证标识
    source     str      来源链接（可追溯性要求，建议必填）
    backend    str      onnx（预留 gguf 等）
    max_len    int      单次推理最大字符数，默认 128
    files      dict     文件名 -> sha256（十六进制）。必填文件须在此登记。

安全纪律（与 attachments / backup 同一套）
------------------------------------------
1. 拒绝绝对路径、`..`、盘符与符号链接成员（防 zip-slip）；
2. 成员数、单文件与总解压体积上限（防 zip-bomb）；
3. 必填文件逐个核对 sha256，任何不符即整包拒绝；
4. 先解到 staging 目录，全部校验通过后再**原子替换** current，
   失败时留下原包不动——绝不出现"装了一半"的坏状态；
5. 许可证字段缺失直接拒绝：本产品面向党政机关，来源必须可追溯。

安装布局（按 `kind` 分槽，两类包可共存）
----------------------------------------
    <enhance>/current/csc/     拼写纠错包（model.onnx + vocab.txt）
    <enhance>/current/cgec/    语法纠错包（model.onnx + tokenizer.json）
    <enhance>/.staging/<kind>/ 安装暂存，完成后原子改名到 current/<kind>

历史布局（文件直接放在 current/ 下，只有 csc）由 `migrate_layout()` 自动迁移。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths

SCHEMA = 1
MANIFEST_NAME = "manifest.json"
# 必填文件按 kind 区分：拼写包用词表，语法包（Seq2Seq）用 HF 分词器配置
REQUIRED_FILES_BY_KIND = {
    "csc": ("model.onnx", "vocab.txt"),
    "cgec": ("model.onnx", "tokenizer.json"),
}
REQUIRED_FILES = REQUIRED_FILES_BY_KIND["csc"]    # 历史默认值，保持向后兼容
CURRENT_DIRNAME = "current"
STAGING_DIRNAME = ".staging"

# 增强包按 kind 分槽存放（current/csc、current/cgec），两类包可同时安装。
# 历史布局是文件直接放在 current/ 下（只有 csc），由 migrate_layout() 自动迁移。
VALID_KINDS = ("csc", "cgec")
DEFAULT_KIND = "csc"

MAX_ENTRIES = 64
MAX_FILE_BYTES = 512 * 1024 * 1024        # 单文件 512 MB
MAX_TOTAL_BYTES = 1024 * 1024 * 1024      # 解压总量 1 GB
_CHUNK = 1024 * 1024


class EnhancePackError(Exception):
    """增强包不可用（格式、完整性或安全校验未通过）。"""


def normalize_kind(kind: str | None) -> str:
    """把外部传入的 kind 收敛到受支持的取值；未知一律落到 csc。"""
    k = str(kind or "").strip().lower()
    return k if k in VALID_KINDS else DEFAULT_KIND


def required_files(kind: str) -> tuple[str, ...]:
    """某个 kind 的必填文件清单。"""
    return REQUIRED_FILES_BY_KIND.get(normalize_kind(kind), REQUIRED_FILES)


@dataclass
class PackInfo:
    name: str = ""
    kind: str = "csc"
    version: str = ""
    license: str = ""
    source: str = ""
    backend: str = "onnx"
    max_len: int = 128
    installed_dir: Path | None = None
    raw: dict = field(default_factory=dict)

    def summary(self) -> str:
        tag = "拼写纠错" if self.kind == "csc" else (
            "语法纠错" if self.kind == "cgec" else self.kind)
        return (f"{self.name} v{self.version}（{tag}｜{self.backend}）"
                f"\n许可证：{self.license}"
                + (f"\n来源：{self.source}" if self.source else ""))


def _slot_dir(kind: str) -> Path:
    """某个 kind 的安装槽位：<enhance>/current/<kind>。"""
    return paths.enhance_dir() / CURRENT_DIRNAME / normalize_kind(kind)


def _staging_dir(kind: str) -> Path:
    """某个 kind 的暂存目录：<enhance>/.staging/<kind>；按 kind 分开避免交叉污染。"""
    return paths.enhance_dir() / STAGING_DIRNAME / normalize_kind(kind)


def current_dir(kind: str = DEFAULT_KIND) -> Path:
    """指定 kind 的安装目录（默认 csc，向后兼容旧调用）。"""
    return _slot_dir(kind)


def migrate_layout() -> None:
    """旧布局（文件直接放在 current/ 下）→ 新布局（current/csc/）。

    只认「current/manifest.json 存在」这一条判据；迁移失败静默放弃，
    下次调用再试，绝不因为迁移问题影响主流程。
    """
    try:
        base = paths.enhance_dir() / CURRENT_DIRNAME
        if not (base / MANIFEST_NAME).exists():
            return
        # 旧包里只有 csc；已被新布局占位时不动（避免把 cgec 的目录盖掉）
        dest = base / DEFAULT_KIND
        if dest.exists():
            return
        try:
            man = json.loads((base / MANIFEST_NAME).read_text(encoding="utf-8"))
            old_kind = normalize_kind(man.get("kind"))
        except Exception:
            old_kind = DEFAULT_KIND
        target = base / old_kind
        if target.exists():
            return
        target.mkdir(parents=True, exist_ok=True)
        for item in list(base.iterdir()):
            if item.name in VALID_KINDS or item == target:
                continue
            shutil.move(str(item), str(target / item.name))
    except Exception:
        pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """过滤出安全的成员；发现危险成员直接拒绝整包。"""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        raise EnhancePackError("包内没有任何文件")
    if len(infos) > MAX_ENTRIES:
        raise EnhancePackError(f"包内文件过多（{len(infos)} > {MAX_ENTRIES}）")
    total = 0
    for i in infos:
        name = i.filename.replace("\\", "/")
        # 绝对路径（POSIX 的 / 开头，或 Windows 盘符）单独报，便于定位问题包
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise EnhancePackError(f"包内含绝对路径，已拒绝：{i.filename}")
        # 相对路径穿越（zip-slip）
        if ".." in name.split("/"):
            raise EnhancePackError(f"包内含危险路径，已拒绝：{i.filename}")
        # 符号链接：外部属性高 16 位为 0xA000
        if (i.external_attr >> 16) & 0xF000 == 0xA000:
            raise EnhancePackError(f"包内含符号链接，已拒绝：{i.filename}")
        if i.file_size > MAX_FILE_BYTES:
            raise EnhancePackError(f"单文件超过上限：{i.filename}")
        total += i.file_size
        if total > MAX_TOTAL_BYTES:
            raise EnhancePackError("解压后总体积超过上限，疑似异常包")
    return infos


def read_manifest(zip_path: str | Path) -> dict:
    """只读地取出并校验 manifest，不解压任何其他内容。"""
    p = Path(zip_path)
    if not p.exists():
        raise EnhancePackError(f"文件不存在：{p}")
    if not zipfile.is_zipfile(p):
        raise EnhancePackError("不是有效的 zip 包")
    try:
        with zipfile.ZipFile(p) as zf:
            names = {i.filename.replace("\\", "/") for i in zf.infolist()}
            if MANIFEST_NAME not in names:
                raise EnhancePackError(f"包内缺少 {MANIFEST_NAME}")
            raw = zf.read(MANIFEST_NAME)
    except EnhancePackError:
        raise
    except Exception as e:
        raise EnhancePackError(f"读取清单失败：{e}") from e
    try:
        man = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise EnhancePackError(f"清单不是合法 JSON：{e}") from e
    if not isinstance(man, dict):
        raise EnhancePackError("清单根节点必须是对象")
    _validate_manifest(man)
    return man


def _validate_manifest(man: dict) -> None:
    if int(man.get("schema", 0)) != SCHEMA:
        raise EnhancePackError(
            f"清单 schema 不受支持：{man.get('schema')!r}（本机支持 {SCHEMA}）")
    if not str(man.get("name") or "").strip():
        raise EnhancePackError("清单缺少 name")
    if not str(man.get("license") or "").strip():
        # 面向党政机关：来源不可追溯的模型一律不收
        raise EnhancePackError("清单缺少 license（来源可追溯为强制要求）")
    backend = str(man.get("backend") or "onnx").lower()
    if backend != "onnx":
        raise EnhancePackError(f"暂不支持的 backend：{backend}")
    files = man.get("files")
    if not isinstance(files, dict):
        raise EnhancePackError("清单缺少 files 哈希表")
    need = required_files(man.get("kind"))
    missing = [f for f in need if f not in files]
    if missing:
        raise EnhancePackError(f"清单未登记必填文件的哈希：{'、'.join(missing)}")


def _to_info(man: dict, installed_dir: Path | None) -> PackInfo:
    try:
        max_len = int(man.get("max_len") or 128)
    except Exception:
        max_len = 128
    return PackInfo(
        name=str(man.get("name") or ""),
        kind=str(man.get("kind") or "csc").lower(),
        version=str(man.get("version") or ""),
        license=str(man.get("license") or ""),
        source=str(man.get("source") or ""),
        backend=str(man.get("backend") or "onnx").lower(),
        max_len=max(16, min(max_len, 512)),
        installed_dir=installed_dir,
        raw=man,
    )


def installed_pack(kind: str = DEFAULT_KIND) -> PackInfo | None:
    """指定 kind 已安装的增强包；未安装或已损坏时返回 None（不抛异常）。"""
    d = _slot_dir(kind)
    man_file = d / MANIFEST_NAME
    if not man_file.exists():
        return None
    try:
        man = json.loads(man_file.read_text(encoding="utf-8"))
        _validate_manifest(man)
        return _to_info(man, d)
    except Exception:
        return None


def verify_installed(kind: str = DEFAULT_KIND) -> bool:
    """指定 kind 已安装包的必填文件是否齐备。"""
    info = installed_pack(kind)
    if info is None or info.installed_dir is None:
        return False
    return all((info.installed_dir / f).exists() for f in required_files(kind))


def install_pack(zip_path: str | Path, kind: str | None = None) -> PackInfo:
    """校验并安装增强包；返回安装后的信息。任何失败都不改变现有安装。

    kind 为 None 时从清单读取；显式传入时以传入值为准（且必须与清单一致，
    否则拒绝——防止把语法包塞进拼写槽位）。**只替换本槽位**，另一类包不受影响。
    """
    man = read_manifest(zip_path)
    man_kind = normalize_kind(man.get("kind"))
    if kind is None:
        target_kind = man_kind
    else:
        target_kind = normalize_kind(kind)
        if target_kind != man_kind:
            raise EnhancePackError(
                f"包类型不匹配：清单声明为 {man_kind}，安装位置要求 {target_kind}")

    staging = _staging_dir(target_kind)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = _safe_members(zf)
            for i in infos:
                name = i.filename.replace("\\", "/")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, _CHUNK)

        # 完整性：必填文件逐个核对 sha256
        want = man.get("files") or {}
        for fname in required_files(target_kind):
            f = staging / fname
            if not f.exists():
                raise EnhancePackError(f"包内缺少必填文件：{fname}")
            expect = str(want.get(fname) or "").lower()
            if not expect:
                raise EnhancePackError(f"清单未提供 {fname} 的哈希")
            got = _sha256(f)
            if got != expect:
                raise EnhancePackError(
                    f"{fname} 校验失败（期望 {expect[:12]}…，实际 {got[:12]}…）")

        # 原子替换（只动本 kind 的槽位）
        cur = _slot_dir(target_kind)
        cur.parent.mkdir(parents=True, exist_ok=True)
        if cur.exists():
            shutil.rmtree(cur, ignore_errors=True)
        staging.rename(cur)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _to_info(man, _slot_dir(target_kind))


def remove_pack(kind: str = DEFAULT_KIND) -> bool:
    """卸载指定 kind 的增强包；返回是否确有删除。另一类包不受影响。"""
    cur = _slot_dir(kind)
    existed = cur.exists()
    if existed:
        shutil.rmtree(cur, ignore_errors=True)
    staging = _staging_dir(kind)
    shutil.rmtree(staging, ignore_errors=True)
    return existed
