# -*- coding: utf-8 -*-
"""L4 神经精排层（可选 · opt-in）。

它在纠错流水线中的位置
----------------------
    文本 → L1 精确词表 → L2 上下文/标点/数字规则 → L3 词边界保护
         → 【L4 本模块】→ list[Correction]

L4 只做最后一件事：对前三层**没有覆盖**的片段做一次模型级判定，输出同样是
`corrector.Correction`，因此 UI 层（纠错面板、任意文档纠错、标记视图）零改动。

三条硬约束（不可协商）
----------------------
1. **不引入 torch**。本项目要打 Windows x64 与麒麟 V10 ARM64（glibc 2.31、
   CI 容器内 Python 3.9）两个包；torch 在该组合下没有稳定 wheel，且会让安装
   包从几十 MB 膨胀到 GB 级。因此载体只能是 ONNX Runtime（MIT，有
   manylinux2014_aarch64 wheel），且列在 `requirements-optional.txt`，
   **不进主 requirements**。
2. **模型不随主包分发**。见 `paths.enhance_dir`。未导入增强包时，本模块
   全程静默，纠错行为与 v1.5.0 完全一致。
3. **任何异常都必须咽掉**。加载失败、推理报错、依赖缺失——一律回落到三级
   流水线。纠错主流程不允许因为"增强包坏了"而失败或变慢。

模型契约（增强包内 `model.onnx`）
---------------------------------
输入（按需，只喂 session 声明过的）：
    input_ids       int64  [1, L]
    attention_mask  int64  [1, L]
    token_type_ids  int64  [1, L]
输出（按名称关键字匹配）：
    *detect*        检测分支，[1, L, 2]（argmax==1 视为错）或 [1, L]（>0.5 视为错）
    *correct*/*logits*  纠正分支，[1, L, vocab]（argmax 给出替换字 id）
这对应 MacBERT4CSC 一类的「检测+纠正」双头结构。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import enhance_pack

SETTING_KEY = "corrector_neural_enabled"     # 设置表键名（值为 "1"/"0"）
_MARK_CATEGORY = "神经纠错"
_KIND = "csc"            # 本层使用 enhance_pack 的 csc 槽位（current/csc）
# 单次调用最多处理的字符数与窗口步长（保证 UI 不卡）
_MAX_TEXT_CHARS = 20000
_WINDOW_DEFAULT = 128
_WINDOW_STRIDE_RATIO = 0.5


@dataclass
class _Engine:
    session: object
    vocab: list[str]
    token_to_id: dict[str, int]
    max_len: int
    input_names: set[str]
    det_name: str | None
    cor_name: str | None
    info: enhance_pack.PackInfo
    np: object
    dir_key: str = ""
    stats: dict = field(default_factory=dict)


_ENGINE: _Engine | None = None
_ENGINE_ERROR: str = ""
_ENGINE_KEY: str = ""       # 上次尝试加载的目录指纹（路径 + mtime），用于避免重复尝试

# onnxruntime 是否可导入：`import` 一个**不存在**的模块不会进 sys.modules，
# 每次调用都会重新走一遍模块搜索路径。开关打开后 check_text 是按段落调的，
# 段落一多这点开销会累积，因此结论缓存一次（换环境后由 reset/invalidate 清）。
_RUNTIME_CACHE: bool | None = None

# 开关缓存：check_text 是**按段落**调用的，若每段都去读设置表 + 探测文件，
# 长文档会明显变慢。默认关闭时这里必须是一次内存布尔判断、零 I/O。
_SETTING_CACHE: bool | None = None


def invalidate_cache() -> None:
    """使设置缓存失效（由 corrector.invalidate_cache 统一调用）。"""
    global _SETTING_CACHE, _RUNTIME_CACHE
    _SETTING_CACHE = None
    _RUNTIME_CACHE = None


# ---------------------------------------------------------------- 依赖与状态
def runtime_available() -> bool:
    """onnxruntime 可导入？（结论缓存，见 _RUNTIME_CACHE）"""
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is None:
        try:
            import onnxruntime  # noqa: F401
            _RUNTIME_CACHE = True
        except Exception:
            _RUNTIME_CACHE = False
    return _RUNTIME_CACHE


def available() -> bool:
    """运行时 + 增强包都就绪。只在开关已打开时才会被调用。"""
    return runtime_available() and enhance_pack.verify_installed(_KIND)


def enabled() -> bool:
    """用户是否开启，且条件确实允许（未就绪时直接返回 False）。"""
    if not _setting_on():
        return False
    return available()


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
    from ..db import dao
    dao.set_setting(SETTING_KEY, "1" if on else "0")
    _SETTING_CACHE = bool(on)


def status_text() -> str:
    """给设置界面用的一句话状态。"""
    if not runtime_available():
        return "未安装 onnxruntime —— 需要它才能启用（见 requirements-optional.txt）"
    info = enhance_pack.installed_pack(_KIND)
    if info is None:
        return "未导入精度增强包"
    if not enhance_pack.verify_installed(_KIND):
        return "增强包不完整（缺少 model.onnx 或 vocab.txt），请重新导入"
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
    except Exception as e:                       # 依赖缺失
        _ENGINE_ERROR = f"缺少推理依赖：{e}"
        return None
    info = enhance_pack.installed_pack(_KIND)
    if info is None or info.installed_dir is None:
        _ENGINE_ERROR = "未导入精度增强包"
        return None
    mdir: Path = info.installed_dir
    try:
        vocab_path = mdir / "vocab.txt"
        vocab = vocab_path.read_text(encoding="utf-8").splitlines()
        vocab = [v if v else "\u0000" for v in vocab]
        if len(vocab) < 100:
            raise ValueError(f"词汇表过小（{len(vocab)} 行），疑似文件错误")
        token_to_id = {t: i for i, t in enumerate(vocab)}

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1     # 桌面端别抢占 CPU
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        sess = ort.InferenceSession(str(mdir / "model.onnx"),
                                    sess_options=opts,
                                    providers=["CPUExecutionProvider"])
        in_names = {i.name for i in sess.get_inputs()}
        outs = [o.name for o in sess.get_outputs()]

        _ENGINE = _Engine(
            session=sess, vocab=vocab, token_to_id=token_to_id,
            max_len=int(info.max_len or _WINDOW_DEFAULT),
            input_names=in_names,
            det_name=_first_match(outs, ("detect",)),
            cor_name=_first_match(outs, ("correct", "logits")),
            info=info, np=np, dir_key=key,
        )
        return _ENGINE
    except Exception as e:
        _ENGINE_ERROR = f"加载模型失败：{e}"
        _ENGINE = None
        return None


def set_session_for_test(session, vocab: list[str], info: enhance_pack.PackInfo,
                         input_names=None) -> None:
    """测试钩子：注入一个假的推理会话，绕开真实 onnxruntime 与模型文件。

    注意 dir_key 必须取**当前目录指纹**，否则 `_load_engine` 会判定缓存失效
    而重新去磁盘加载（测试环境没有真实模型，会被丢弃）。
    """
    try:
        import numpy as np
    except Exception:                            # 纯逻辑测试无需 numpy
        np = None
    outs = list(getattr(session, "output_names", ["detection", "correction"]))
    key = _dir_key()
    globals()["_ENGINE"] = _Engine(
        session=session, vocab=vocab, token_to_id={t: i for i, t in enumerate(vocab)},
        max_len=int(info.max_len or _WINDOW_DEFAULT),
        input_names=set(input_names or {"input_ids", "attention_mask", "token_type_ids"}),
        det_name=_first_match(outs, ("detect",)),
        cor_name=_first_match(outs, ("correct", "logits")),
        info=info, np=np, dir_key=key,
    )
    globals()["_ENGINE_KEY"] = key
    globals()["_ENGINE_ERROR"] = ""
    globals()["_RUNTIME_CACHE"] = True           # 测试中视为依赖就绪


def _first_match(names, keys) -> str | None:
    for n in names:
        low = n.lower()
        if any(k in low for k in keys):
            return n
    return None


def _output_names(session) -> list[str]:
    """取会话的输出名；无论真实 ORT 还是测试假会话都走这里。"""
    try:
        return [o.name for o in session.get_outputs()]
    except Exception:
        return []


# ---------------------------------------------------------------- 推理
# 推理输出只按「序列」消费（`len()` + 下标 + `float()`），numpy 数组与原生的
# list-of-list 都满足。刻意不写 `np.asarray/np.argmax`：本模块的**运行**确实
# 需要 numpy（onnxruntime 的输入张量只能是 ndarray，见 `_run`），但**解码逻辑**
# 不该被它绑死——否则单元测试就得为了跑 8 个纯逻辑断言而装一个 200 MB 的包。
def _argmax_seq(seq) -> tuple[int, float]:
    """序列上的 argmax（返回 (下标, 最大值)）。对 numpy 与 list 通用。"""
    best_i, best_v = 0, float("-inf")
    n = len(seq)
    for i in range(n):
        v = float(seq[i])
        if v > best_v:
            best_v, best_i = v, i
    return best_i, (0.0 if best_v == float("-inf") else best_v)


def _sigmoid(x: float) -> float:
    try:
        import math
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, x))))
    except Exception:
        return 0.5


def _decode_window(eng: _Engine, ids: list[int], orig_chars: list[str],
                   det, cor):
    """单窗口解码，返回 [(局部下标, 新字符, 置信度)]。"""
    out = []
    if cor is None:
        return out
    L = min(len(ids), len(cor))
    for i in range(L):
        # --- 检测支路 ---
        if det is not None:
            d = det[i]
            if len(d) >= 2:
                is_err = _argmax_seq(d)[0] == 1
                conf_d = float(d[1])
            else:
                v = float(d[0])
                is_err = v > 0.5
                conf_d = _sigmoid(v)
        else:
            is_err, conf_d = True, 0.55
        if not is_err:
            continue
        # --- 纠正支路 ---
        c = cor[i]
        if len(c) == 0:
            continue
        new_id = _argmax_seq(c)[0]
        old_id = ids[i]
        if new_id == old_id or new_id <= 0 or new_id >= len(eng.vocab):
            continue
        new_ch = eng.vocab[new_id]
        if len(new_ch) != 1 or new_ch == "\u0000":
            continue
        if orig_chars[i] == new_ch:
            continue
        # 只做「单字替换」；插入/删除交给前三层，避免破坏公式与编号
        if unicodedata.category(orig_chars[i]).startswith("P"):
            continue
        conf = max(0.55, min(0.95, conf_d))
        out.append((i, new_ch, conf))
    return out


def enhance(text: str, existing=None) -> list:
    """返回 L4 新增的 Correction 列表；任何异常都返回空列表。

    `existing` 为前三层的命中，用于跳过已被覆盖的位置。
    """
    try:
        if not text or not _setting_on():
            return []
        eng = _load_engine()
        if eng is None:
            return []
        return _run(eng, text, existing or [])
    except Exception as e:                       # 硬约束：绝不外抛
        global _ENGINE_ERROR
        _ENGINE_ERROR = f"推理异常：{e}"
        _ENGINE = None
        return []


def _tensor(eng: _Engine, data: list[int]):
    """构造喂给会话的一个张量。

    真实 onnxruntime 的输入必须是 ndarray；但测试用的假会话吃原生 list 即可，
    因此在没有 numpy 时退化为 list —— 让「解码/合并/降级」这条纯逻辑链路
    可以脱离 numpy 独立测试（真实推理路径永远有 numpy，见 `_load_engine`）。
    """
    np = eng.np
    if np is None:
        return [list(data)]
    return np.array([data], dtype=np.int64)


def _run(eng: _Engine, text: str, existing) -> list:
    from .corrector import Correction

    occupied = bytearray(len(text) + 1)
    for c in existing:
        for k in range(max(0, c.start), min(c.end, len(text))):
            occupied[k] = 1

    chars = list(text)
    n = len(chars)
    if n > _MAX_TEXT_CHARS:
        n = _MAX_TEXT_CHARS
    win = max(16, min(eng.max_len, _WINDOW_DEFAULT))
    stride = max(8, int(win * _WINDOW_STRIDE_RATIO))

    edits: dict[int, tuple[str, float]] = {}
    start = 0
    while start < n:
        seg = chars[start:start + win]
        ids = [eng.token_to_id.get(ch, 1) for ch in seg]
        if eng.input_names:
            feed = {}
            if "input_ids" in eng.input_names:
                feed["input_ids"] = _tensor(eng, ids)
            if "attention_mask" in eng.input_names:
                feed["attention_mask"] = _tensor(eng, [1] * len(ids))
            if "token_type_ids" in eng.input_names:
                feed["token_type_ids"] = _tensor(eng, [0] * len(ids))
        else:
            feed = {"input_ids": _tensor(eng, ids)}
        if not feed:
            break
        outputs = eng.session.run(None, feed)
        names = _output_names(eng.session)
        det = cor = None
        for idx, (nm, val) in enumerate(zip(names or [], outputs)):
            if eng.det_name and nm == eng.det_name:
                det = val[0]
            elif eng.cor_name and nm == eng.cor_name:
                cor = val[0]
            elif not names:                      # 无名称时按位置约定
                if idx == 0:
                    det = val[0]
                elif idx == 1:
                    cor = val[0]
        if det is None and outputs:
            det = outputs[0][0]
        if cor is None and len(outputs) >= 2:
            cor = outputs[1][0]

        for local_i, new_ch, conf in _decode_window(eng, ids, seg, det, cor):
            gpos = start + local_i
            if gpos >= n or occupied[gpos]:
                continue
            if chars[gpos] == new_ch:
                continue
            old = edits.get(gpos)
            if old is None or conf > old[1]:
                edits[gpos] = (new_ch, conf)
        start += stride

    if not edits:
        return []
    return _to_corrections(text, edits, Correction)


def _to_corrections(text, edits: dict, Correction) -> list:
    """把单字改动合并成连续区间，转成 Correction 列表。"""
    positions = sorted(edits)
    out = []
    i = 0
    while i < len(positions):
        s = positions[i]
        j = i
        while (j + 1 < len(positions)
               and positions[j + 1] == positions[j] + 1):
            j += 1
        e = positions[j]
        confs = [edits[p][1] for p in positions[i:j + 1]]
        wrong = text[s:e + 1]
        sug = "".join(edits[p][0] for p in positions[i:j + 1])
        if wrong != sug:
            out.append(Correction(
                start=s, end=e + 1, wrong=wrong, suggestion=sug,
                category=_MARK_CATEGORY,
                reason="神经网络模型判定为疑似错误，请人工确认",
                confidence=round(sum(confs) / len(confs), 3)))
        i = j + 1
    return out
