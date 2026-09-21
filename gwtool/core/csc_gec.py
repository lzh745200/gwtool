# -*- coding: utf-8 -*-
"""L5 语法纠错层（可选 · opt-in）。

它在纠错流水线中的位置
----------------------
    文本 → L1 精确词表 → L2 上下文/标点/数字规则 → L3 词边界保护
         → L4 神经精排（单字替换）→ 【L5 本模块】→ list[Correction]

为什么必须独立成层，不能并进 L4
-------------------------------
L4 的模型是「检测+纠正」双头分类器，解码契约是**逐位 argmax + 位置连续性合并**，
每个位置只可能被换成**另一个单字**。L5 的模型是 Seq2Seq（BART/T5 一类），输出
是**变长序列**——可以增字、删字、改序。两者的后处理完全正交：把 L5 塞进 L4 的
`_decode_window` 等于要求它产出一一对应的逐位分布，那会丢掉 L5 的全部价值；
而把 L4 的合并逻辑改成通用对齐，会让 L4 现有的 18 个用例全部重写。
因此各自独立、各自契约清晰，在 `corrector.check_text` 里串起来。

三条硬约束（与 L4 同源，不可协商）
----------------------------------
1. **不引入 torch**：只用 ONNX Runtime（见 `requirements-optional.txt`）。
   分词走 `tokenizers`（HF 的 Rust 库，约几 MB，不含 torch）。
2. **模型不随主包分发**：装在 `current/cgec` 槽位。未导入时本模块全程静默，
   纠错行为与接入前完全一致。
3. **任何异常都必须咽掉**：加载失败、分词器缺失、推理报错——一律返回空列表。

Seq2Seq 的失控风险与十道门控
----------------------------
生成式模型有个分类器没有的失败模式：**把对的改成错的，而且成段地改**。
所以 L5 的输出必须过 G1–G10（见 `_gate`），任一不过就整段丢弃。宁可漏报，
不可误报——这是面向党政机关文本的基本要求。

模型契约（增强包内 `model.onnx` + `tokenizer.json`）
----------------------------------------------------
按 session 的输入名自动识别两种模式（`_load_engine` 探测，`_Engine.mode`）：

A. oneshot（单次前向，非自回归）——输入名不含 "decoder"
    输入（按需，只喂 session 声明过的）：
        input_ids       int64  [1, L]     已含 [CLS]/[SEP] 包裹
        attention_mask  int64  [1, L]
        token_type_ids  int64  [1, L]
    输出：
        *logits* / *output*  [1, T, vocab]  —— 逐 token 分布，逐行 argmax
    适合并行解码 / 单流标注类模型。

B. encdec（自回归 Seq2Seq：T5/BART 一类）——输入名含 "decoder"
    输入：
        input_ids            int64  [1, L]   编码器输入（不包 [CLS]/[SEP]）
        attention_mask       int64  [1, L]
        decoder_input_ids    int64  [1, K]   [种子] + 已生成 token（见下）
    输出：
        *logits* / *output*  [1, K, vocab]  —— 最后一行是「下一个 token」分布
    贪心解码循环由本模块在 Python 侧步进。约定：导出图内部把
    decoder_input_ids 的首粒替换为模型的 decoder_start_token（转换脚本
    烘焙为常量），因此本模块喂 [pad_id 种子] + 已生成序列即可，
    无需知晓 start token 是什么。
    编码器输入的**尾部 eos 必须保留**（T5/BART 训练分布即如此）：
    剥掉后模型「不知道何时该停」，自回归陷入复读循环（2026-09-12
    实测）。引擎只剥分词器自带的头部特殊 token 与尾部填充。

特殊 token 的 id 从 `tokenizer.json` 按 [CLS]/[SEP]/[PAD]/[UNK] 与
<s>/</s>/<pad>/<unk> 候选读取，不写死（BERT 系与 T5/BART 系都覆盖）。
encdec 的停机集合只含 sep/pad——T5 词表里没有 <s>，cls 回退到 BERT
惯例值 101 时会撞上真实 token（id 101 =「进行」），绝不能进停机集合。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .. import logs
from . import enhance_pack

log = logs.get_logger("csc.l5")

SETTING_KEY = "corrector_gec_enabled"      # 设置表键名（值为 "1"/"0"）
_MARK_CATEGORY = "语法纠错"
_KIND = "cgec"                             # 使用 enhance_pack 的 cgec 槽位

# 单次调用最多处理的字符数（防止超长文档把 UI 拖死）
_MAX_TEXT_CHARS = 20000
# 单段最长字符数：超过就按句切分（G8）
_MAX_SEG_CHARS = 200
# 段最小字符数（G10）：太短的片段模型没上下文，容易乱改
_MIN_SEG_CHARS = 8
# 一次喂给模型的窗口（含 [CLS]/[SEP]）
_WINDOW_DEFAULT = 128

# --- 门控阈值（G1–G3、G9）---
_MIN_CONF = 0.75           # G1：段内平均置信度下限
_MAX_CHANGE_RATIO = 0.30   # G2：改动字符占段长的比例上限
_MIN_LCS_RATIO = 0.60      # G3：与原句的最长公共子序列保留率下限
_MIN_LEN_RATIO = 0.6       # G9：输出/输入长度比下界
_MAX_LEN_RATIO = 1.6       # G9：长度比上界

# G4：数字与阿拉伯数字组合（含小数、百分比、区间）
_NUM_RE = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)?%?")
# G5：需要保护的标点（中英文）
_PUNCT_SET = set("，。、；：？！“”‘’（）《》〈〉【】—…·「」『』,.;:?!\"'()<>[]{}~`@#$^&*_+=|\\/-")
# G6：引号/书名号内的内容整体保护
_QUOTE_PAIRS = (("“", "”"), ("‘", "’"), ("《", "》"), ("〈", "〉"),
                ("「", "」"), ("『", "』"), ("【", "】"))
# 删字类改动：只允许删虚词/语气助词，避免把实词删掉
_DELETABLE = set("的了着呢吗吧啊呀嘛哦喔哈嗯把被给让向在对")


class GecError(Exception):
    """L5 层不可用（依赖缺失、模型损坏等）；对外一律降级为空结果。"""


@dataclass
class _Engine:
    session: object
    tokenizer: object                       # tokenizers.Tokenizer
    max_len: int = _WINDOW_DEFAULT
    cls_id: int = 101
    sep_id: int = 102
    pad_id: int = 0
    unk_id: int = 100
    mode: str = "oneshot"                   # oneshot | encdec（自回归 Seq2Seq）
    info: enhance_pack.PackInfo | None = None
    dir_key: str = ""
    np: object = None


_ENGINE: _Engine | None = None
_ENGINE_ERROR = ""
_ENGINE_KEY = ""
_RUNTIME_CACHE: bool | None = None
_SETTING_CACHE: bool | None = None


# ---------------------------------------------------------------- 开关
def runtime_available() -> bool:
    """`tokenizers` 与 onnxruntime 是否都可导入。结果缓存，避免反复试 import。

    用 ``find_spec`` 探测而不是真 import：两者都是重型原生扩展，
    这里只需要知道"在不在"。
    """
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is None:
        try:
            import importlib.util
            _RUNTIME_CACHE = all(
                importlib.util.find_spec(m) is not None
                for m in ("onnxruntime", "tokenizers"))
        except Exception:
            _RUNTIME_CACHE = False
    return _RUNTIME_CACHE


def _setting_on() -> bool:
    global _SETTING_CACHE
    if _SETTING_CACHE is None:
        try:
            from ..db import dao
            _SETTING_CACHE = str(dao.get_setting(SETTING_KEY, "0")).strip() == "1"
        except Exception:
            _SETTING_CACHE = False
    return _SETTING_CACHE


def set_setting(on: bool) -> None:
    global _SETTING_CACHE
    _SETTING_CACHE = bool(on)
    try:
        from ..db import dao
        dao.set_setting(SETTING_KEY, "1" if on else "0")
    except Exception as exc:
        # 开关没写进库 = 重启后回到旧值。用户会看到"我明明开了又关了"，
        # 而界面在本次会话内是生效的，属于最难排查的一类不一致。
        log.warning("L5 开关未能持久化（%s），重启后将回到原值", exc)


def available() -> bool:
    """运行时 + cgec 增强包都就绪。只在开关已打开时才被调用。"""
    return runtime_available() and enhance_pack.verify_installed(_KIND)


def enabled() -> bool:
    """用户是否开启，且条件确实允许（未就绪时直接返回 False）。"""
    if not _setting_on():
        return False
    return available()


def invalidate_cache() -> None:
    """设置变更后调用，丢弃开关缓存（不丢已加载模型）。"""
    global _SETTING_CACHE
    _SETTING_CACHE = None


def status_text() -> str:
    """给设置界面用的一句话状态。"""
    if not runtime_available():
        return ("缺少推理运行时 onnxruntime / tokenizers —— 未就绪、无法启用。"
                "可用随程序附带这些运行时的发行版（重启后生效），"
                "或按 requirements-optional.txt 在有网机器取离线轮子拷入本机安装。")
    info = enhance_pack.installed_pack(_KIND)
    if info is None:
        return "未导入语法纠错增强包（点下方「导入增强包…」选择离线取得的本层 .zip）"
    if not enhance_pack.verify_installed(_KIND):
        return "语法纠错增强包不完整（缺少 model.onnx 或 tokenizer.json），请重新导入"
    state = "已开启" if _setting_on() else "已关闭"
    return f"{state}｜已就绪：{info.name} v{info.version}（{info.license}）"


def last_error() -> str:
    return _ENGINE_ERROR


def reset() -> None:
    """丢弃已加载模型与开关缓存（换包 / 卸载 / 测试后调用）。"""
    global _ENGINE, _ENGINE_ERROR, _ENGINE_KEY, _SETTING_CACHE, _RUNTIME_CACHE
    _ENGINE = None
    _ENGINE_ERROR = ""
    _ENGINE_KEY = ""
    _SETTING_CACHE = None
    _RUNTIME_CACHE = None


# ---------------------------------------------------------------- 加载
def _dir_key() -> str:
    cur = enhance_pack.current_dir(_KIND)
    key = str(cur)
    try:
        if cur.exists():
            key += f"|{cur.stat().st_mtime_ns}"
    except Exception:
        pass
    return key


def _special_ids(tok) -> dict:
    """从 tokenizer 里取特殊 token 的 id；取不到就用 BERT 的惯例值。

    按候选列表依次尝试：BERT 系是 [CLS]/[SEP]，T5/BART 系是
    <s>/</s>/<pad>/<unk>。第一个能取到合法 id 的候选生效——
    这样 BERT 行为与旧版完全一致，Seq2Seq 分词器也能各归其位。
    """
    ids = {"cls_id": 101, "sep_id": 102, "pad_id": 0, "unk_id": 100}
    candidates = {
        "cls_id": ("[CLS]", "<s>", "<bos>"),
        "sep_id": ("[SEP]", "</s>", "<eos>"),
        "pad_id": ("[PAD]", "<pad>"),
        "unk_id": ("[UNK]", "<unk>"),
    }
    for key, names in candidates.items():
        for tokname in names:
            try:
                v = tok.token_to_id(tokname)
            except Exception:
                v = None
            if isinstance(v, int) and v >= 0:
                ids[key] = v
                break
    return ids


def _load_engine() -> _Engine | None:
    global _ENGINE, _ENGINE_ERROR, _ENGINE_KEY
    key = _dir_key()
    if _ENGINE is not None and _ENGINE.dir_key == key:
        return _ENGINE
    if _ENGINE is None and _ENGINE_KEY == key:
        return None                     # 同一指纹已失败/已加载过，不重复尝试

    _ENGINE_KEY = key
    _ENGINE = None
    _ENGINE_ERROR = ""
    try:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except Exception as e:                       # 依赖缺失
        _ENGINE_ERROR = f"缺少推理依赖：{e}"
        log.warning("L5 缺少推理依赖，已降级：%s", e)
        return None

    info = enhance_pack.installed_pack(_KIND)
    if info is None or info.installed_dir is None:
        _ENGINE_ERROR = "未导入语法纠错增强包"
        return None
    mdir: Path = info.installed_dir
    try:
        tok_path = mdir / "tokenizer.json"
        if not tok_path.exists():
            raise ValueError("包内缺少 tokenizer.json")
        tok = Tokenizer.from_file(str(tok_path))

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1     # 桌面端别抢占 CPU
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        sess = ort.InferenceSession(str(mdir / "model.onnx"), opts,
                                    providers=["CPUExecutionProvider"])
        in_names = {i.name for i in sess.get_inputs()}
        # 模式探测：带 decoder_* 输入的是 Seq2Seq（自回归），否则单次前向解码
        mode = "encdec" if any("decoder" in n.lower() for n in in_names) \
            else "oneshot"
        ids = _special_ids(tok)
        eng = _Engine(
            session=sess, tokenizer=tok,
            max_len=int(info.max_len or _WINDOW_DEFAULT),
            cls_id=ids["cls_id"], sep_id=ids["sep_id"],
            pad_id=ids["pad_id"], unk_id=ids["unk_id"],
            mode=mode, info=info, dir_key=key, np=np,
        )
        _ENGINE = eng
        return eng
    except Exception as e:
        _ENGINE_ERROR = f"加载模型失败：{e}"
        log.warning("L5 模型加载失败，已降级：%s", e)
        _ENGINE = None
        return None


def set_session_for_test(session, tokenizer, info=None, *,
                         input_names=None, mode="oneshot",
                         **id_overrides) -> None:
    """测试钩子：注入假的推理会话与分词器，绕开真实 onnxruntime 与模型文件。

    与 L4 的 `set_session_for_test` 同一手法：`dir_key` 必须取当前目录指纹，
    否则 `_load_engine` 会判定缓存失效而重新去磁盘加载（测试环境没有真模型）。
    `mode` 可注入 "encdec" 验证自回归解码路径。
    """
    try:
        import numpy as np
    except Exception:                            # 纯逻辑测试无需 numpy
        np = None
    if info is None:
        info = enhance_pack.PackInfo(name="fake-gec", kind=_KIND, version="9.9.9",
                                     license="Apache-2.0", max_len=_WINDOW_DEFAULT)
    ids = {"cls_id": 101, "sep_id": 102, "pad_id": 0, "unk_id": 100}
    ids.update({k: v for k, v in id_overrides.items() if k in ids})
    key = _dir_key()
    globals()["_ENGINE"] = _Engine(
        session=session, tokenizer=tokenizer,
        max_len=int(getattr(info, "max_len", _WINDOW_DEFAULT) or _WINDOW_DEFAULT),
        info=info, dir_key=key, np=np, mode=mode, **ids)
    globals()["_ENGINE_KEY"] = key
    globals()["_ENGINE_ERROR"] = ""
    globals()["_RUNTIME_CACHE"] = True           # 测试中视为依赖就绪


def _output_names(session) -> list[str]:
    try:
        return [o.name for o in session.get_outputs()]
    except Exception:
        return []


# ---------------------------------------------------------------- 对外入口
def enhance(text: str, existing=None) -> list:
    """返回 L5 新增的 Correction 列表；任何异常都返回空列表。

    `existing` 为 L1–L4 的命中，其覆盖区间会被避让（L4 优先、L5 补漏）。
    """
    global _ENGINE_ERROR
    try:
        if not text or not _setting_on():
            return []
        eng = _load_engine()
        if eng is None:
            return []
        return _run(eng, text, existing or [])
    except Exception as e:                       # 硬约束：绝不外抛
        _ENGINE_ERROR = f"推理异常：{e}"
        log.warning("L5 推理异常，本层结果丢弃：%s", e)
        _ENGINE = None
        return []


# ---------------------------------------------------------------- 分段
def _split_segments(text: str) -> list[tuple[int, int]]:
    """把文本切成 (起, 止) 段：先按换行，再按句读标点，确保每段 ≤ _MAX_SEG_CHARS。

    逗号/分号也作为切点：公文长句里一个逗号往往就是一个小句边界，
    按小句送模型既贴合 GEC 模型的训练粒度，也天然缩短单次推理耗时。
    过短的片段（< _MIN_SEG_CHARS）直接不返回——G10。
    """
    segs: list[tuple[int, int]] = []
    for m in re.finditer(r"[^\n]+", text):
        s, e = m.start(), m.end()
        for piece in _split_long(s, e, m.group(0)):
            if piece[1] - piece[0] >= _MIN_SEG_CHARS:
                segs.append(piece)
    return segs


def _split_long(s: int, e: int, chunk: str) -> list[tuple[int, int]]:
    """按句读标点细分（。！？；，、 均视为切点），过长的小句再硬切。

    无论长短都按标点切：一个逗号前后就是两个独立的语义单元，
    分开送模型既贴合 GEC 模型的训练粒度，也能让 G7 的位置级避让
    真正生效——否则一处被上层覆盖就会拖累整段。
    """
    out: list[tuple[int, int]] = []
    cur = s
    for m in re.finditer(r"[。！？；，、!?;,]", chunk):
        cut = s + m.end()
        if cut - cur >= _MIN_SEG_CHARS:
            out.append((cur, cut))
            cur = cut
    if cur < e:
        while e - cur > _MAX_SEG_CHARS:
            out.append((cur, cur + _MAX_SEG_CHARS))
            cur += _MAX_SEG_CHARS
        if e - cur >= _MIN_SEG_CHARS:
            out.append((cur, e))
    return out


# ---------------------------------------------------------------- 推理
def _encode(eng: _Engine, seg: str) -> list[int]:
    """用 tokenizer 编码；oneshot 包 [CLS]/[SEP]，encdec 用裸序列。"""
    try:
        enc = eng.tokenizer.encode(seg)
        ids = list(getattr(enc, "ids", enc))
    except Exception:
        ids = []
    if eng.mode == "encdec":
        # Seq2Seq 不吃 [CLS]/[SEP]：剥掉分词器自带的头部特殊 token 与
        # 尾部填充；**尾部 sep（eos）必须保留**——T5/BART 的编码器约定
        # 输入以 eos 结尾，剥掉后模型不知何时该停，生成陷入复读循环。
        # 有的 tokenizer（如 mengzi-t5 的 tokenizer.json）自带定长填充，
        # 尾部一长串 pad 也是在这里剥掉。
        if not ids:
            return []
        while ids and ids[0] in (eng.cls_id, eng.sep_id, eng.pad_id):
            ids.pop(0)
        while ids and ids[-1] == eng.pad_id:
            ids.pop()
        return ids[:max(4, eng.max_len)]
    if not ids:
        return [eng.cls_id, eng.sep_id]
    # 有的 tokenizer 自带特殊 token，这里统一剥掉再自己包，避免重复
    while ids and ids[0] in (eng.cls_id, eng.sep_id):
        ids.pop(0)
    while ids and ids[-1] in (eng.cls_id, eng.sep_id):
        ids.pop()
    return [eng.cls_id, *ids, eng.sep_id][:max(4, eng.max_len)]


def _tensor(eng: _Engine, data: list[int]):
    """构造喂给会话的张量；无 numpy 时退化为 list（测试用假会话可吃）。"""
    np = eng.np
    if np is None:
        return [list(data)]
    return np.array([data], dtype=np.int64)


def _decode_one(eng: _Engine, seg: str) -> tuple[str, float]:
    """对一段文本做贪心解码，返回 (新文本, 平均对数概率)。

    失败或无法解码时返回 ("", 0.0)，由调用方按「无改动」处理。
    """
    if eng.mode == "encdec":
        return _decode_one_encdec(eng, seg)
    ids = _encode(eng, seg)
    if len(ids) <= 2:
        return "", 0.0
    try:
        inputs = {i.name for i in eng.session.get_inputs()}
    except Exception:
        inputs = set()
    if not inputs:
        inputs = {"input_ids", "attention_mask", "token_type_ids"}
    feed = {}
    if "input_ids" in inputs:
        feed["input_ids"] = _tensor(eng, ids)
    if "attention_mask" in inputs:
        feed["attention_mask"] = _tensor(eng, [1] * len(ids))
    if "token_type_ids" in inputs:
        feed["token_type_ids"] = _tensor(eng, [0] * len(ids))
    if not feed:
        return "", 0.0

    outputs = eng.session.run(None, feed)
    if not outputs:
        return "", 0.0
    logits = outputs[0][0]                      # [T, vocab]
    out_ids: list[int] = []
    logp_sum = 0.0
    logp_n = 0
    for row in logits:
        best_i, best_v = _argmax(row, np=eng.np)
        # 平均对数概率：把 logits 经 softmax 再取对数，数值稳定版本
        logp_sum += _log_softmax_pick(row, best_v, np=eng.np)
        logp_n += 1
        if best_i in (eng.cls_id, eng.sep_id, eng.pad_id):
            continue
        out_ids.append(int(best_i))

    if not out_ids:
        return "", 0.0
    try:
        new_text = eng.tokenizer.decode(out_ids, skip_special_tokens=True)
    except Exception:
        try:
            new_text = eng.tokenizer.decode(out_ids)
        except Exception:
            return "", 0.0
    new_text = str(new_text or "")
    # 分词器解码会把空格加回来，中文文本按原样去掉（原文本身不含空白）
    new_text = re.sub(r"\s+", "", new_text)
    if not new_text:
        return "", 0.0
    avg_logp = logp_sum / logp_n if logp_n else 0.0
    return new_text, avg_logp


def _decode_one_encdec(eng: _Engine, seg: str) -> tuple[str, float]:
    """encdec 模式：Python 侧贪心解码循环，每次步进一个 token。

    图契约见模块 docstring B 节：decoder_input_ids 的首粒会被图内
    替换为 decoder_start_token，因此这里喂 [pad_id 种子] + 已生成序列，
    每步取 logits 最后一行的 argmax 作为下一个 token，直到句束/步数上限。
    失败时返回 ("", 0.0)。
    """
    ids = _encode(eng, seg)
    if not ids:
        return "", 0.0
    try:
        inames = {i.name for i in eng.session.get_inputs()}
    except Exception:
        inames = set()
    if "decoder_input_ids" not in inames:
        return "", 0.0                        # 图没有解码器输入，无法自回归
    gen: list[int] = [eng.pad_id]             # 种子位，图内会被替换为 decoder_start
    out_ids: list[int] = []
    logp_sum = 0.0
    logp_n = 0
    # 步数上限：G9 的长度比上界是 1.6，生成再长也过不了门控；再留点余量
    max_steps = min(int(len(ids) * 1.6) + 4, 96)
    for _ in range(max_steps):
        feed = {"input_ids": _tensor(eng, ids),
                "attention_mask": _tensor(eng, [1] * len(ids)),
                "decoder_input_ids": _tensor(eng, gen)}
        # 有些导出图还声明 decoder/encoder 的 attention_mask，按声明补齐；
        # 没声明的张量绝不喂（ORT 会拒绝未知输入名）
        if "decoder_attention_mask" in inames:
            feed["decoder_attention_mask"] = _tensor(eng, [1] * len(gen))
        if "encoder_attention_mask" in inames:
            feed["encoder_attention_mask"] = _tensor(eng, [1] * len(ids))
        try:
            outputs = eng.session.run(None, feed)
        except Exception:
            return "", 0.0
        if not outputs:
            return "", 0.0
        logits = outputs[0][0]                # [K, vocab]
        if len(logits) == 0:
            return "", 0.0
        row = logits[-1]                      # 最后一行 = 下一个 token 的分布
        best_i, best_v = _argmax(row, np=eng.np)
        logp_sum += _log_softmax_pick(row, best_v, np=eng.np)
        logp_n += 1
        tok_id = int(best_i)
        # encdec 停机集合只含 sep/pad，绝不含 cls：T5 词表没有 <s>，
        # cls 回退到 BERT 惯例值 101 时会撞上真实 token（id 101=「进行」），
        # 生成中途出现该 id 就停会把纠正截断
        if tok_id in (eng.sep_id, eng.pad_id):
            break                             # 句束/填充即停
        gen.append(tok_id)
        out_ids.append(tok_id)
    if not out_ids:
        return "", 0.0
    try:
        new_text = eng.tokenizer.decode(out_ids, skip_special_tokens=True)
    except Exception:
        try:
            new_text = eng.tokenizer.decode(out_ids)
        except Exception:
            return "", 0.0
    new_text = re.sub(r"\s+", "", str(new_text or ""))
    if not new_text:
        return "", 0.0
    avg_logp = logp_sum / logp_n if logp_n else 0.0
    return new_text, avg_logp


def _argmax(seq, np=None) -> tuple[int, float]:
    """序列上的 argmax（返回 (下标, 最大值)）；对 numpy 与 list 通用。

    有 numpy 时走向量化：真实推理的词表是 3 万+，纯 Python 逐位 float()
    每步要几万次比较，自回归解码步步都做，开销不可接受。
    """
    if np is not None:
        try:
            arr = np.asarray(seq)
            idx = int(arr.argmax())
            return idx, float(arr[idx])
        except Exception:
            pass                          # 落回纯 Python，语义不变
    best_i, best_v = 0, float("-inf")
    for i in range(len(seq)):
        v = float(seq[i])
        if v > best_v:
            best_v, best_i = v, i
    return best_i, (0.0 if best_v == float("-inf") else best_v)


def _log_softmax_pick(row, max_v: float, np=None) -> float:
    """给定一行 logits 与已求出的最大值，返回该最大值的 log-softmax。

    有 numpy 时走向量化（3 万维的 exp 用 Python 循环要几十毫秒，
    自回归解码步步都做）；np 为 None（测试假会话）退化为循环。
    """
    import math
    try:
        if np is not None:
            arr = np.asarray(row, dtype="float64")
            s = float(np.exp(arr - max_v).sum())
            return -math.log(s) if s > 0 else 0.0
        s = 0.0
        for v in row:
            s += math.exp(float(v) - max_v)
        return -math.log(s) if s > 0 else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------- 对齐
def _lcs_len(a: str, b: str) -> int:
    """最长公共子序列长度（滚一维，O(len(a)*len(b))）。段长有上限，开销可控。"""
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    prev = [0] * (len(a) + 1)
    for ch in b:
        cur = [0] * (len(a) + 1)
        for j, ca in enumerate(a, 1):
            cur[j] = prev[j - 1] + 1 if ca == ch else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def _diff_spans(src: str, dst: str) -> list[tuple[int, int, str]]:
    """把 src 改成 dst 的最小编辑脚本，合并成「改动片段」返回。

    返回 [(start, end, 替换文本)]，坐标基于 src：
      - 替换：end > start，替换文本非空
      - 插入：start == end，替换文本非空
      - 删除：end > start，替换文本为空串
    相邻的改动会合并成一片（如「同事」→「同仁」是一处替换，不是两处）。

    实现：标准 DP 求编辑距离，再从头正向走一遍表收集脚本。
    DP 表大小 (len(src)+1)×(len(dst)+1)，段长已由 _MAX_SEG_CHARS 限住。
    """
    n, m = len(src), len(dst)
    if n == 0 and m == 0:
        return []
    if n == 0:
        return [(0, 0, dst)]
    if m == 0:
        return [(0, n, "")]

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        si = src[i - 1]
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            if si == dst[j - 1]:
                row[j] = prev[j - 1]
            else:
                row[j] = 1 + min(prev[j - 1], prev[j], row[j - 1])

    # 正向重建路径：每一步在「匹配 / 替换 / 删 / 增」里选一个仍然最优的动作。
    # 把连续的非匹配动作合并成一片改动。每轮必须至少推进一个下标，
    # 用 `moved` 兜底防死循环。
    # 反向回溯（从 (n,m) 走到 (0,0)）。这是标准做法且无歧义：
    # 每个格子直接读 dp 值决定上一步动作，不需要"猜测"路径是否最优。
    ops: list[tuple[str, int, int, str]] = []      # (op, i, j, dst_char)
    i, j = n, m
    while i > 0 or j > 0:
        cur = dp[i][j]
        if i > 0 and j > 0 and src[i - 1] == dst[j - 1] \
                and cur == dp[i - 1][j - 1]:
            # 字符相同且走对角线不涨代价 —— 一定是匹配
            ops.append(("match", i - 1, j - 1, ""))
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and cur == dp[i - 1][j - 1] + 1:
            ops.append(("sub", i - 1, j - 1, dst[j - 1]))
            i -= 1
            j -= 1
        elif j > 0 and cur == dp[i][j - 1] + 1:
            ops.append(("ins", i, j - 1, dst[j - 1]))
            j -= 1
        elif i > 0 and cur == dp[i - 1][j] + 1:
            ops.append(("del", i - 1, -1, ""))
            i -= 1
        else:
            break                                  # 理论不可达，保险
    ops.reverse()

    # 合并连续的非 match 动作成一片。片的 src 区间 = 该片内所有
    # sub/del 覆盖的 src 范围；ins 不消费 src，只往替换文本里加字。
    spans: list[tuple[int, int, str]] = []
    k = 0
    while k < len(ops):
        if ops[k][0] == "match":
            k += 1
            continue
        s_i = ops[k][1]
        end_i = s_i
        buf: list[str] = []
        while k < len(ops) and ops[k][0] != "match":
            op, oi, _oj, ch = ops[k]
            if op in ("sub", "del"):
                end_i = oi + 1          # 只由 sub/del 推进 src 终点
            if op in ("sub", "ins"):
                buf.append(ch)
            k += 1
        spans.append((s_i, end_i, "".join(buf)))
    return spans


def _normalize_insert(src: str, span: tuple[int, int, str]) -> tuple[int, int, str]:
    """把零宽/负宽片段规范化为**邻字替换**，让现有消费点无需改动。

    规则：
      - 纯插入 (s==e)：挂到前一个字符 → wrong=前一字, suggestion=前一字+插入字。
        段首插入无前字可挂，挂到后一个字符上。
      - 纯删除 (替换文本为空)：挂到被删片段的前一个字符；
        删到段首则挂到后一个字符。
    返回 (start, end, suggestion)；start==end 的退化情况由调用方再兜一层。
    """
    s, e, rep = span
    if s == e:                                   # 插入
        if s > 0:
            return s - 1, s, src[s - 1] + rep
        if e < len(src):
            return 0, 1, rep + src[0]
        return s, e, rep
    if rep == "":                                # 删除
        if s > 0:
            return s - 1, e, src[s - 1]
        if e < len(src):
            return s, e + 1, src[e]
        return s, e, rep
    return s, e, rep


# ---------------------------------------------------------------- 门控
def _protected_mask(src: str) -> bytearray:
    """标出不得改动的字符位：数字（G4）、标点（G5）、引号内（G6）。"""
    mask = bytearray(len(src))

    def mark(a: int, b: int) -> None:
        for k in range(max(0, a), min(b, len(src))):
            mask[k] = 1

    for m in _NUM_RE.finditer(src):
        mark(m.start(), m.end())
    for k, ch in enumerate(src):
        if ch in _PUNCT_SET or unicodedata.category(ch).startswith("P"):
            mask[k] = 1
    for lo, hi in _QUOTE_PAIRS:
        a = src.find(lo)
        while a >= 0:
            b = src.find(hi, a + 1)
            if b < 0:
                break
            mark(a, b + 1)
            a = src.find(lo, b + 1)
    return mask


def _gate(src: str, dst: str, spans: list[tuple[int, int, str]],
          mask: bytearray, conf: float) -> list[tuple[int, int, str]]:
    """G1–G10 门控；返回被接受的片段（可能为空 = 整段丢弃）。"""
    if not spans:
        return []
    # G1 置信度
    if conf < _MIN_CONF:
        return []
    # G9 长度比
    ls, ld = len(src), len(dst)
    if ls == 0:
        return []
    ratio = ld / ls
    if not (_MIN_LEN_RATIO <= ratio <= _MAX_LEN_RATIO):
        return []
    # G2 改动幅度
    changed = sum(max(e - s, len(rep)) for s, e, rep in spans)
    if changed > ls * _MAX_CHANGE_RATIO:
        return []
    # G3 保留率（防整句重写）
    if _lcs_len(src, dst) < ls * _MIN_LCS_RATIO:
        return []
    # G4/G5/G6 保护位不得被触碰
    kept: list[tuple[int, int, str]] = []
    for s, e, rep in spans:
        if any(mask[k] for k in range(max(0, s), min(e, len(src)))):
            continue                             # 触碰保护位 → 丢这一处，不丢整段
        # 插入位置紧邻保护位也丢弃（避免在数字/标点边上加字）
        if s == e and s < len(src) and (mask[s] or (s > 0 and mask[s - 1])):
            continue
        kept.append((s, e, rep))
    if not kept:
        return []
    # 删字类：只允许删虚词（G6 的延伸，防把实词删掉）
    out: list[tuple[int, int, str]] = []
    for s, e, rep in kept:
        if rep == "" and any(src[k] not in _DELETABLE
                             for k in range(s, min(e, len(src)))):
            continue
        out.append((s, e, rep))
    return out


# ---------------------------------------------------------------- 主流程
def _free_chunks(s: int, e: int, occupied) -> list[tuple[int, int]]:
    """把 [s, e) 按 occupied 切成若干「未被上层覆盖」的连续片段。

    为什么不能整段跳过：一段几百字的公文里只要有一处被 L1 命中，
    整段就不送模型 —— 等于一个 L1 命中把整段的 L5 检查全部关掉。
    G7 的本意是「已覆盖的位置不要重复报」，是位置级的避让，不是段落级的。
    """
    chunks: list[tuple[int, int]] = []
    cur = -1
    for k in range(s, e):
        if occupied[k]:
            if cur >= 0:
                chunks.append((cur, k))
                cur = -1
        elif cur < 0:
            cur = k
    if cur >= 0:
        chunks.append((cur, e))
    return chunks


def _run(eng: _Engine, text: str, existing) -> list:
    import math

    from .corrector import Correction

    n = min(len(text), _MAX_TEXT_CHARS)
    occupied = bytearray(len(text) + 1)
    for c in existing:
        for k in range(max(0, c.start), min(c.end, len(text))):
            occupied[k] = 1

    out: list[Correction] = []
    for s0, e0 in _split_segments(text[:n]):
        # G7：位置级避让 —— 把段里被上层覆盖的位置挖掉，剩下的碎片单独送模型
        for s, e in _free_chunks(s0, e0, occupied):
            if e - s < _MIN_SEG_CHARS:
                continue                         # G10：碎片太短不送
            seg = text[s:e]
            new_seg, avg_logp = _decode_one(eng, seg)
            if not new_seg or new_seg == seg:
                continue
            bare = re.sub(r"\s+", "", new_seg)
            if bare == seg:
                continue
            spans = _diff_spans(seg, bare)
            if not spans:
                continue
            mask = _protected_mask(seg)
            conf = max(0.55, min(0.95, math.exp(max(-20.0, min(0.0, avg_logp)))))
            accepted = _gate(seg, bare, spans, mask, conf)
            if not accepted:
                continue
            for sp in accepted:
                ns, ne, rep = _normalize_insert(seg, sp)
                if ne <= ns and rep == "":
                    continue                     # 规范化后仍不可表达，丢弃
                gs, ge = s + ns, s + ne
                if ge <= gs:
                    continue
                if any(occupied[k] for k in range(gs, ge)):
                    continue                     # G7 逐位复核
                wrong = text[gs:ge]
                if not wrong or wrong == rep:
                    continue
                kind = "replace"
                orig = sp
                if orig[0] == orig[1]:
                    kind = "insert"
                elif orig[2] == "":
                    kind = "delete"
                out.append(Correction(
                    start=gs, end=ge, wrong=wrong, suggestion=rep,
                    category=_MARK_CATEGORY,
                    reason="语法模型判定为疑似语病，请人工确认",
                    confidence=round(conf, 3), kind=kind))
                for k in range(gs, ge):
                    occupied[k] = 1
    return out
