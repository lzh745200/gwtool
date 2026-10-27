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

from .. import logs
from . import enhance_pack

log = logs.get_logger("csc.l4")

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
    # v1.20 P2：模型是否要求在输入两侧补 [CLS]/[SEP]。
    # 不同导出约定不一致：有的图内补（输出 L+2），有的要求应用侧补。
    # 加载时用一句干净短句实测两种约定，取"预测=原文"一致率更高的那种。
    add_special: bool = False


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
    """onnxruntime 可导入？（结论缓存，见 _RUNTIME_CACHE）

    用 ``find_spec`` 探测而不是 ``import onnxruntime``：这里只想知道"在不在"，
    而真正 import 一个几十 MB 的原生扩展代价很高（且失败时会留下半初始化状态）。
    """
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is None:
        try:
            import importlib.util
            _RUNTIME_CACHE = importlib.util.find_spec("onnxruntime") is not None
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


def set_setting(on: bool) -> bool:
    """写入 L4 开关。返回**是否真的落库成功**（契约与 csc_gec.set_setting 一致）。

    为什么必须返回布尔：设置页按 `if not saved:` 区分"已保存"与"本次生效、
    重启后回退"。此前本函数返回 `None`（恒为假），于是**每次**拨动「拼写纠错
    增强」开关都会弹出"未能保存到数据库，重启程序后会恢复为原来的设置" ——
    而设置其实写成功了。用户会据此怀疑数据目录权限或磁盘，白白排查一场。

    （同族的 csc_gec / reminder 已按此契约返回布尔；只有本函数被漏掉。）
    """
    global _SETTING_CACHE
    _SETTING_CACHE = bool(on)
    try:
        from ..db import dao
        dao.set_setting(SETTING_KEY, "1" if on else "0")
        return True
    except Exception as exc:
        # 没写进库 = 重启后回到旧值，必须留痕（内存态已在本次会话生效）
        log.warning("L4 开关未能持久化（%s），重启后将回到原值", exc)
        return False


def status_text() -> str:
    """给设置界面用的一句话状态。"""
    if not runtime_available():
        return ("缺少推理运行时 onnxruntime —— 未就绪、无法启用。"
                "可用随程序附带该运行时的发行版（重启后生效），"
                "或按 requirements-optional.txt 在有网机器取离线轮子拷入本机安装。")
    info = enhance_pack.installed_pack(_KIND)
    if info is None:
        return "未导入精度增强包（点下方「导入增强包…」选择离线取得的本层 .zip）"
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


def _parse_vocab(text: str) -> list[str]:
    """词表解析：只按 "\\n" 切，保留 U+2028/U+2029 等 token 内部字符。

    v1.20 P2：旧实现用 splitlines()，会把词表里作为 token 一部分的 Unicode
    行分隔符额外切成多行（MacBERT 词表实测多 2 行），导致整表 token→id
    整体错位、干净句被改成乱码。这里只去掉行尾 \\r，空行仍保留占位。
    """
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return [v if v else "\u0000" for v in lines]


def _probe_add_special(sess, vocab, token_to_id, input_names, np) -> bool:
    """实测模型是否需要应用侧补 [CLS]/[SEP]。

    不同导出约定不一致：`build_csc_pack.py` 的包装器在图内补，而现成
    MacBERT4CSC 导出要求**调用方**补。用一句干净短句分别按两种约定前向，
    比较"预测字=原字"的一致率；应用侧补显著更好才返回 True。
    任何异常都退回 False（保持旧行为，绝不因探测失败拒绝加载）。
    """
    if np is None:
        return False
    text = "会议在北京召开"
    chars = list(text)
    char_ids = [token_to_id.get(ch, 1) for ch in chars]

    def _feed(seq):
        feed = {}
        names = input_names or {"input_ids"}
        if "input_ids" in names:
            feed["input_ids"] = np.array([seq], dtype=np.int64)
        if "attention_mask" in names:
            feed["attention_mask"] = np.ones((1, len(seq)), dtype=np.int64)
        if not feed:      # 只认位置的非标准导出
            feed[sorted(names)[0]] = np.array([seq], dtype=np.int64)
        return feed

    def _rate(wrapped: bool) -> float:
        try:
            ids = ([101] + char_ids + [102]) if wrapped else char_ids
            outs = sess.run(None, _feed(ids))
            cor = outs[1][0] if len(outs) >= 2 else outs[0][0]
            shift = max(0, len(cor) - len(char_ids) - 1)
            ok = 0
            for i, old in enumerate(char_ids):
                j = i + shift
                if j < len(cor) and _argmax_seq(cor[j])[0] == old:
                    ok += 1
            return ok / max(1, len(char_ids))
        except Exception:
            return -1.0

    raw = _rate(False)
    wrapped = _rate(True)
    return wrapped > raw + 0.2


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
    # ① 增强包存在性检查不依赖重依赖：没导入包的用户不该看到
    #    「缺少 numpy」这种运维向报错——不装包是完全正常的关闭态。
    info = enhance_pack.installed_pack(_KIND)
    if info is None or info.installed_dir is None:
        _ENGINE_ERROR = "未导入精度增强包"
        return None
    mdir: Path = info.installed_dir
    # ② 词汇表文件级校验同样不依赖 numpy/onnxruntime：坏包在创建
    #    推理会话之前就该被拒（纯文件读取，零重依赖）。
    try:
        vocab_path = mdir / "vocab.txt"
        vocab = _parse_vocab(vocab_path.read_text(encoding="utf-8"))
        if len(vocab) < 100:
            raise ValueError(f"词汇表过小（{len(vocab)} 行），疑似文件错误")
    except Exception as e:
        _ENGINE_ERROR = f"加载模型失败：{e}"
        log.warning("L4 词汇表校验失败，已降级到三级流水线：%s", e)
        return None
    # ③ 真正的重依赖：只有前两级都通过才需要 numpy/onnxruntime。
    try:
        import numpy as np
        import onnxruntime as ort
    except Exception as e:                       # 依赖缺失
        _ENGINE_ERROR = f"缺少推理依赖：{e}"
        log.warning("L4 缺少推理依赖，已降级到三级流水线：%s", e)
        return None
    try:
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

        add_special = _probe_add_special(sess, vocab, token_to_id, in_names, np)
        _ENGINE = _Engine(
            session=sess, vocab=vocab, token_to_id=token_to_id,
            max_len=int(info.max_len or _WINDOW_DEFAULT),
            input_names=in_names,
            det_name=_first_match(outs, ("detect",)),
            cor_name=_first_match(outs, ("correct", "logits")),
            info=info, np=np, dir_key=key, add_special=add_special,
        )
        return _ENGINE
    except Exception as e:
        _ENGINE_ERROR = f"加载模型失败：{e}"
        log.warning("L4 模型加载失败，已降级到三级流水线：%s", e)
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


# v1.20 P2 门控阈值：检测支路 softmax 概率 + 纠正支路 softmax 概率双阈值。
# 旧实现只看检测 argmax、置信度直接取检测 logits 原始值，等于没有闸门：
# 真实语料新增误报 2.43/千字（目标 ≤1.0）。这两个值在 220 篇语料上标定：
# 0.95/0.60 能把"空格/标点被改字"和低置信臆测挡掉，同时保留确定性错字。
_L4_DET_MIN = 0.50
# 纠正支路用"top1 与 top2 的 logit 间隔"而不是 softmax 绝对概率：
# 假会话/小词表用 one-hot 输出时 softmax 会被词表大小稀释（25 词表只有 0.58），
# 但间隔 1.0 仍明确表示"模型很确定"；真实模型同理。
_L4_COR_MARGIN = 1.0


def _softmax_prob(row, idx: int) -> float:
    """一行 logits 在 idx 处的 softmax 概率（数值稳定版）。"""
    try:
        n = len(row)
        if not (0 <= idx < n):
            return 0.0
        m = float(row[0])
        for k in range(1, n):
            v = float(row[k])
            if v > m:
                m = v
        s = 0.0
        for k in range(n):
            s += __import__("math").exp(float(row[k]) - m)
        return float(__import__("math").exp(float(row[idx]) - m) / s) if s else 0.0
    except Exception:
        return 0.0


def _decode_window(eng: _Engine, ids: list[int], orig_chars: list[str],
                   det, cor):
    """单窗口解码，返回 [(局部下标, 新字符, 置信度)]。

    v1.20 P2 修复：导出图可能在图内补 [CLS]/[SEP]，correction/detection
    输出长度会是 L+2，第 i+1 行才对应输入第 i 个字；按"输出比输入多几行"
    自动求偏移，避免整句预测左移一位（真实语料误报 57.6/千字的主因）。

    门控（v1.20）：
      * 只改 CJK 汉字（空格/数字/拉丁交给前三层，避免"空格→（"这类误改）；
      * 检测支路 softmax 概率 ≥ _L4_DET_MIN；
      * 纠正支路 softmax 概率（新字）≥ _L4_COR_MIN；
      * 只做单字替换，标点不动。
    """
    out = []
    if cor is None:
        return out
    shift = max(0, len(cor) - len(ids) - 1)
    L = min(len(ids), max(0, len(cor) - shift))
    for i in range(L):
        j = i + shift
        orig = orig_chars[i]
        # 只处理汉字：空格、数字、英文、标点都不是 L4 的目标
        if not ("\u4e00" <= orig <= "\u9fff"):
            continue
        # --- 检测支路 ---
        if det is not None and j < len(det):
            d = det[j]
            if len(d) >= 2:
                is_err = _argmax_seq(d)[0] == 1
                conf_d = _softmax_prob(d, 1)
            else:
                v = float(d[0])
                is_err = v > 0.5
                conf_d = _sigmoid(v)
        else:
            is_err, conf_d = True, 0.55
        if not is_err or conf_d < _L4_DET_MIN:
            continue
        # --- 纠正支路 ---
        c = cor[j]
        if len(c) == 0:
            continue
        new_id = _argmax_seq(c)[0]
        old_id = ids[i]
        if new_id == old_id or new_id <= 0 or new_id >= len(eng.vocab):
            continue
        new_ch = eng.vocab[new_id]
        if len(new_ch) != 1 or new_ch == "\u0000":
            continue
        if orig == new_ch:
            continue
        # 标点不动（单字替换也不应把汉字改成标点）
        if unicodedata.category(orig).startswith("P"):
            continue
        if not ("\u4e00" <= new_ch <= "\u9fff"):
            continue
        m1 = float(c[new_id])
        m2 = max((float(v) for k, v in enumerate(c) if k != new_id), default=0.0)
        margin = m1 - m2
        if margin < _L4_COR_MARGIN:
            continue
        conf = max(0.55, min(0.95, min(conf_d, 0.55 + margin / 10.0)))
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
        global _ENGINE, _ENGINE_ERROR
        _ENGINE_ERROR = f"推理异常：{e}"
        log.warning("L4 推理异常，本层结果丢弃：%s", e)
        _ENGINE = None                           # 丢弃坏引擎，下次强制重载
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
    names = _output_names(eng.session)       # 输出名跨窗口不变，循环外取一次
    start = 0
    while start < n:
        seg = chars[start:start + win]
        char_ids = [eng.token_to_id.get(ch, 1) for ch in seg]
        ids = char_ids
        if eng.add_special:
            ids = [101] + char_ids + [102]
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
        det = cor = None
        for nm, val in zip(names or [], outputs):
            if eng.det_name and nm == eng.det_name:
                det = val[0]
            elif eng.cor_name and nm == eng.cor_name:
                cor = val[0]
        # 无名称时按位置约定兜底（names 为空时上面 zip 本就不执行）
        if det is None and outputs:
            det = outputs[0][0]
        if cor is None and len(outputs) >= 2:
            cor = outputs[1][0]

        for local_i, new_ch, conf in _decode_window(eng, char_ids, seg, det, cor):
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
