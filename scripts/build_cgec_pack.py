# -*- coding: utf-8 -*-
"""一键构建「语法纠错增强包」（cgec 槽位）：Mengzi-T5 → ONNX → .zip

做什么
------
1. 从 `.models/mengzi-t5/` 加载 shibing624/mengzi-t5-base-chinese-correction
   （T5ForConditionalGeneration，SIGHAN+Wang271K 27 万句 finetune，SIGHAN2015
   F1≈0.72，Apache-2.0）；
2. 包一层导出包装器：**在图内**把 decoder_input_ids 的首粒替换为模型的
   decoder_start_token（应用侧引擎只喂 [pad 种子] + 已生成序列，见
   `gwtool/core/csc_gec.py` 的 encdec 模式契约），导出单输出 ONNX：
       logits    [1, K, V]   最后一行 = 下一个 token 的分布
   自回归贪心解码循环由引擎在 Python 侧步进；
3. 动态量化（MatMul/Gemm，**per_channel QUInt8**）：~945MB → ~309MB。
   本机实测（2026-09-12）QInt8 权重内核会产出乱码（MacBERT 实验
   一致率 0%），QUInt8 是唯一健康路径，AR 文本与 fp32 全等；
4. 双重自检，任一不过即 SystemExit 拒绝出包：
   fp32 版 ORT 自回归解码必须与 torch greedy generate 逐样本全等；
   量化版 AR 解码文本必须与 fp32 ORT 逐样本全等（AR 的 argmax
   翻转会污染整个后缀，恒等比对是最严判据）；
5. 打包 `packs/mengzi-t5-cgec-1.0.0.zip`（manifest + model.onnx +
   tokenizer.json），在「设置 → 文字纠错」导入。

怎么跑（有网机器一次即可，产物纯离线）
----------------------------------------
    PYTHONPATH=C:/gwtool/.convlib python scripts/build_cgec_pack.py

依赖（仅转换期）：torch / transformers / onnx / onnxruntime（在 .convlib）。
应用运行时只要 onnxruntime + tokenizers，**不依赖 torch**。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / ".models" / "mengzi-t5"
OUT_DIR = ROOT / "packs"
NAME = "Mengzi-T5 语法纠错包（Seq2Seq）"
VERSION = "1.0.0"
LICENSE = "Apache-2.0"
SOURCE = "https://huggingface.co/shibing624/mengzi-t5-base-chinese-correction"

SAMPLES = ["今天新情很好", "我们要在下个月举办运动会。",
           "他通过这种方法获得了大家的认可。",
           "这样能够更加有效的加强两家公司之间的合作。",
           "我们不应该让父母担心我们的生活和未来。"]


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_onnx(torch, model, onnx_path: Path) -> None:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(SRC))
    dec_start = int(model.config.decoder_start_token_id)
    print(f"  decoder_start_token_id = {dec_start}（烘焙进图内）")

    class T5OnnxWrapper(torch.nn.Module):
        """图内把 decoder_input_ids 首粒替换为 decoder_start_token。

        引擎契约：喂 [pad 种子] + 已生成序列 → 输出 logits 最后一行
        是「下一个 token」分布。种子位被替换，因此引擎无需知道
        start token 是什么（T5=pad，BART=eos，各模型不同）。
        """

        def __init__(self):
            super().__init__()
            self.model = model
            self.start = dec_start

        def forward(self, input_ids, attention_mask, decoder_input_ids):
            b = decoder_input_ids.shape[0]
            start = torch.full((b, 1), self.start, dtype=torch.long,
                               device=decoder_input_ids.device)
            gen = decoder_input_ids[:, 1:]               # 剥掉种子位
            dec_in = torch.cat([start, gen], dim=1)
            out = self.model(input_ids=input_ids,
                             attention_mask=attention_mask,
                             decoder_input_ids=dec_in, use_cache=False)
            return out.logits

    enc = tok(SAMPLES[0], return_tensors="pt")
    wrapper = T5OnnxWrapper()
    dummy_dec = torch.tensor([[dec_start, enc["input_ids"].shape[1] + 5]],
                             dtype=torch.long)

    kw = dict(
        input_names=["input_ids", "attention_mask", "decoder_input_ids"],
        output_names=["logits"],
        dynamic_axes={"input_ids": {1: "L"}, "attention_mask": {1: "L"},
                      "decoder_input_ids": {1: "K"}, "logits": {1: "K"}},
        opset_version=17, do_constant_folding=True)
    try:
        torch.onnx.export(wrapper,
                          (enc["input_ids"], enc["attention_mask"], dummy_dec),
                          str(onnx_path), dynamo=False, **kw)
    except TypeError:
        torch.onnx.export(wrapper,
                          (enc["input_ids"], enc["attention_mask"], dummy_dec),
                          str(onnx_path), **kw)

    import onnx
    m = onnx.load(str(onnx_path))
    if len(m.graph.output):
        m.graph.output[0].name = "logits"
    onnx.checker.check_model(m)
    onnx.save(m, str(onnx_path))


def _ort_ar_decode(sess, np, tok, text: str, sep_id: int, pad_id: int,
                   cap: int = 64) -> str:
    """与引擎 `_decode_one_encdec` 同构的自回归贪心解码（验证用）。"""
    enc = tok.encode(text)
    ids = list(enc.ids) if hasattr(enc, "ids") else list(enc)
    # 引擎侧剥掉分词器自带的头部特殊 token 与尾部填充；
    # **尾部 sep（eos）保留** —— T5 编码器约定输入以 eos 结尾，
    # 剥掉后模型不停机（与 gwtool/core/csc_gec.py 的 _encode 同契约）
    while ids and ids[0] in (sep_id, pad_id):
        ids.pop(0)
    while ids and ids[-1] == pad_id:
        ids.pop()
    gen = [pad_id]                      # 种子位（图内被替换为 decoder_start）
    out_ids = []
    for _ in range(min(len(ids) * 2 + 8, cap)):
        feed = {"input_ids": np.array([ids], dtype=np.int64),
                "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
                "decoder_input_ids": np.array([gen], dtype=np.int64)}
        logits = sess.run(None, feed)[0][0]           # [K, V]
        nxt = int(np.argmax(logits[-1]))
        if nxt in (sep_id, pad_id):
            break
        gen.append(nxt)
        out_ids.append(nxt)
    return re.sub(r"\s+", "", tok.decode(out_ids, skip_special_tokens=True))


def verify(torch, model, tok, fp_path: Path) -> dict[str, str]:
    """fp32 ORT 自回归输出必须与 torch greedy generate 完全一致。

    返回各样本的 fp32 ORT 解码文本，供量化后的全等比对复用。
    """
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(str(fp_path), providers=["CPUExecutionProvider"])
    pad_id = model.config.pad_token_id
    sep_id = model.config.eos_token_id
    refs: dict[str, str] = {}
    for text in SAMPLES:
        enc = tok([text], return_tensors="pt")
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=48, do_sample=False,
                                 num_beams=1)
        want = re.sub(r"\s+",
                      "", tok.decode(gen[0], skip_special_tokens=True))
        # T5 解码器输出就是完整纠正句（decoder_start 与 eos 均为特殊
        # token，skip 后无残留），不存在 prompt 回显，不做前缀剥离——
        # 恒等纠错（输出==输入）会被剥成空串造成假 MISMATCH
        got = _ort_ar_decode(sess, np, tok, text, sep_id, pad_id)
        flag = "OK " if got == want else "MISMATCH"
        print(f"  [{flag}] {text!r} → torch={want!r} ort={got!r}")
        if got != want:
            raise SystemExit(f"fp32 导出自检失败：{text!r}")
        refs[text] = got
    return refs


def quantize(fp_path: Path, quant_path: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    # per_channel QUInt8 是本机实测唯一健康的动态量化路径（2026-09-12）：
    # QInt8 权重内核在此环境产出乱码（MacBERT 实验一致率 0%，
    # reduce_range 也仅 64%）；QUInt8 在 T5 上 AR 文本与 fp32 全等
    # （5/5 样本），体积 945MB → 309MB，满足 512MB 包体上限
    quantize_dynamic(str(fp_path), str(quant_path),
                     op_types_to_quantize=["MatMul", "Gemm"],
                     per_channel=True, weight_type=QuantType.QUInt8)


def verify_quant(tok, fp_path: Path, quant_path: Path,
                 refs: dict[str, str], sep_id: int, pad_id: int) -> None:
    """量化硬闸：AR 解码文本必须与 fp32 ORT 逐样本全等。

    AR 生成的 argmax 翻转会污染整个后缀，且引擎的停机集合只认
    sep/pad——量化若破坏停机行为会直接让 L5 全段拒绝，必须在此
    拦下，坏包绝不入应用。
    """
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(str(quant_path),
                                providers=["CPUExecutionProvider"])
    ok = 0
    for text, want in refs.items():
        got = _ort_ar_decode(sess, np, tok, text, sep_id, pad_id)
        flag = "OK " if got == want else "DIFF"
        print(f"  [{flag}] {text!r} → {got!r}")
        ok += got == want
    print(f"  量化模型 AR 文本全等率：{ok}/{len(refs)}")
    if ok != len(refs):
        raise SystemExit("量化自检失败：AR 文本与 fp32 不全等，拒绝出包")


def make_pack(quant_path: Path, zip_path: Path) -> None:
    files = {"model.onnx": _sha256(quant_path),
             "tokenizer.json": _sha256(SRC / "tokenizer.json")}
    man = {
        "schema": 1,
        "name": NAME,
        "kind": "cgec",
        "version": VERSION,
        "license": LICENSE,
        "source": SOURCE,
        "backend": "onnx",
        "max_len": 128,
        "files": files,
    }
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(man, ensure_ascii=False,
                                                indent=2))
        zf.write(quant_path, "model.onnx")
        zf.write(SRC / "tokenizer.json", "tokenizer.json")
    print(f"  包：{zip_path}（{zip_path.stat().st_size >> 20} MB）")


def main() -> int:
    print("[1/6] 加载 Mengzi-T5 纠错模型…")
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    torch = __import__("torch")
    # eager 注意力导出：SDPA 掩码构造里的张量布尔比较会被 trace 固化
    # 为常量，导出图在其他输入上可能完全错乱（与 csc 包同一教训）。
    model = AutoModelForSeq2SeqLM.from_pretrained(str(SRC),
                                                  attn_implementation="eager")
    model.eval()
    model.config.use_cache = False
    tok = AutoTokenizer.from_pretrained(str(SRC))

    OUT_DIR.mkdir(exist_ok=True)
    fp = OUT_DIR / "_t5_fp32.onnx"
    quant = OUT_DIR / "_t5_u8.onnx"
    zip_path = OUT_DIR / "mengzi-t5-cgec-1.0.0.zip"

    if fp.exists():
        # fp32 中间产物幂等复用：导出一次约 2-3 分钟 + 近 1GB 临时
        # 空间，重跑（如量化参数调整）时无需重导
        print("[2/6] fp32 ONNX 已存在，跳过导出")
    else:
        print("[2/6] 导出 fp32 ONNX（图内烘焙 decoder_start）…")
        export_onnx(torch, model, fp)
    print("[3/6] fp32 自检（ORT 自回归 vs torch generate）…")
    refs = verify(torch, model, tok, fp)
    print("[4/6] 动态量化（per_channel QUInt8，实测唯一健康路径）…")
    quantize(fp, quant)
    print("[5/6] 量化自检（AR 文本与 fp32 逐样本全等）…")
    verify_quant(tok, fp, quant, refs,
                 sep_id=int(model.config.eos_token_id),
                 pad_id=int(model.config.pad_token_id))
    print("[6/6] 打包 zip…")
    make_pack(quant, zip_path)
    for tmp in (fp, quant):
        try:
            tmp.unlink()
        except OSError:
            pass
    print(f"完成：{zip_path}")
    print("导入方式：软件「设置 → 文字纠错 → 语法纠错包 → 导入」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
