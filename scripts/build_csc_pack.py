# -*- coding: utf-8 -*-
"""一键构建「拼写纠错增强包」（csc 槽位）：MacBERT4CSC → ONNX → .zip

做什么
------
1. 从 `.models/macbert4csc/` 加载 shibing624/macbert4csc-base-chinese
   （BertForMaskedLM，逐位 MLM 头即纠正头）；
2. 包一层导出包装器：**在图内**补 [CLS]/[SEP]（应用侧引擎按「裸字序列」
   喂入，见 `gwtool/core/csc_neural.py` 的模型契约），并合成检测支路
   （检测语义 = 纠正结果与原字不同），导出双输出 ONNX：
       detection_logits    [1, L, 2]   argmax==1 视为疑似有错
       correction_logits   [1, L, V]   argmax 给出替换字 id
3. **不做量化，发布 fp32**（本机实验结论，2026-09-12）：
   onnxruntime 1.30 的动态量化对这份导出图是内核级坏的——
   QInt8 权重路径全句乱码（一致率 0%，reduce_range 64%，
   仅量化头也 0%）；最优的 per_channel QUInt8 排除头后也只有
   94.3% 一致，且会在干净句上注入伪错字（去→厝、参→叁），
   而 L4 引擎对 det 标记位没有下游闸门兜底。fp32 zip 实测
   ~419MB，在 enhance_pack 单文件 512MB 上限之内，保真度 100%。
4. 用 onnxruntime 做样本自检（ORT fp32 与 torch fp32 前向的
   argmax 必须一致，不一致直接拒绝出包）；
5. 打包成 `packs/macbert4csc-csc-1.0.0.zip`（manifest + model.onnx +
   vocab.txt，带 sha256），可直接在「设置 → 文字纠错」导入。

怎么跑（在一台有网的机器上执行一次即可，产物纯离线）
------------------------------------------------------
    PYTHONPATH=C:/gwtool/.convlib python scripts/build_csc_pack.py

依赖（仅转换期需要）：torch / transformers / onnx / onnxruntime，
全部装在 `.convlib`（`pip install --target .convlib torch transformers onnx onnxruntime tokenizers numpy`）。
**应用运行时不依赖 torch**——本项目红线不破。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / ".models" / "macbert4csc"
OUT_DIR = ROOT / "packs"
NAME = "MacBERT4CSC 拼写纠错包"
VERSION = "1.0.0"
LICENSE = "Apache-2.0"
SOURCE = "https://huggingface.co/shibing624/macbert4csc-base-chinese"
CLS_ID, SEP_ID = 101, 102

SAMPLE = "今天新情很好，我们要继续努力工作。"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_wrapper(torch, mlm):
    """导出包装器：图内补 [CLS]/[SEP] + 合成检测支路。"""

    class CscOnnxWrapper(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.mlm = mlm

        def forward(self, input_ids, attention_mask):
            b = input_ids.shape[0]
            cls = torch.full((b, 1), CLS_ID, dtype=torch.long,
                             device=input_ids.device)
            sep = torch.full((b, 1), SEP_ID, dtype=torch.long,
                             device=input_ids.device)
            ones = torch.ones((b, 1), dtype=torch.long,
                              device=input_ids.device)
            w = torch.cat([cls, input_ids, sep], dim=1)          # [b, L+2]
            am = torch.cat([ones, attention_mask, ones], dim=1)
            tti = torch.zeros_like(w)
            logits = self.mlm(input_ids=w, attention_mask=am,
                              token_type_ids=tti).logits
            inner = logits[:, 1:-1, :]                           # [b, L, V]
            pred = inner.argmax(dim=-1)                          # [b, L]
            eq = (pred == input_ids).to(inner.dtype)
            # 检测语义：纠正结果与原字不同 → 疑似有错。
            # 分数值刻意落在 (0,1)：引擎 [L,2] 分支直接把 d[1] 当置信度用。
            det_ok = 0.2 + 0.6 * eq
            det_err = 0.2 + 0.7 * (1.0 - eq)
            det = torch.stack([det_ok, det_err], dim=-1)         # [b, L, 2]
            return det, inner

    return CscOnnxWrapper()


def export_onnx(torch, wrapper, onnx_path: Path) -> None:
    from transformers import BertTokenizerFast

    tok = BertTokenizerFast.from_pretrained(str(SRC))
    enc = tok(SAMPLE[:32], return_tensors="pt")
    ids, am = enc["input_ids"][:, :32], enc["attention_mask"][:, :32]
    # 按引擎契约：喂「裸字 id、不含 [CLS]/[SEP]」
    ids = ids - 0  # no-op，保持形状语义清晰
    raw = [tok.convert_ids_to_tokens(int(i)) for i in ids[0]]
    # 用词表逐字重编（与引擎 token_to_id.get(ch, 1) 同构）
    vocab = (SRC / "vocab.txt").read_text(encoding="utf-8").splitlines()
    v2i = {t: i for i, t in enumerate(vocab)}
    text = tok.decode(ids[0], skip_special_tokens=True).replace(" ", "")
    raw_ids = [v2i.get(c, 1) for c in text]
    ids = torch.tensor([raw_ids], dtype=torch.long)
    am = torch.ones_like(ids)

    try:
        torch.onnx.export(
            wrapper, (ids, am), str(onnx_path),
            input_names=["input_ids", "attention_mask"],
            output_names=["detection_logits", "correction_logits"],
            dynamic_axes={"input_ids": {1: "L"}, "attention_mask": {1: "L"},
                          "detection_logits": {1: "L"},
                          "correction_logits": {1: "L"}},
            opset_version=17, dynamo=False, do_constant_folding=True)
    except TypeError:
        # 新版 torch 移除 dynamo 参数时走 dynamo 导出
        torch.onnx.export(
            wrapper, (ids, am), str(onnx_path),
            input_names=["input_ids", "attention_mask"],
            output_names=["detection_logits", "correction_logits"],
            dynamic_axes={"input_ids": {1: "L"}, "attention_mask": {1: "L"},
                          "detection_logits": {1: "L"},
                          "correction_logits": {1: "L"}},
            opset_version=17, do_constant_folding=True)

    # 输出名兜底校正（不同导出器对命名的处理不一致）
    import onnx
    m = onnx.load(str(onnx_path))
    for i, want in enumerate(("detection_logits", "correction_logits")):
        if i < len(m.graph.output):
            m.graph.output[i].name = want
    onnx.checker.check_model(m)
    onnx.save(m, str(onnx_path))


def verify(torch, wrapper, model_path: Path) -> None:
    """样本自检：ORT fp32 与 torch fp32 前向的 argmax 必须一致。

    导出图曾被怀疑（SDPA trace / aten::index），量化也被证明会
    在此 ORT 构建上摧毁输出——所以出包前必须硬比对，坏图绝不
    打包。三道硬闸：
      1. ORT 与 torch 的纠正 argmax 逐位一致率 ≥ 99%（同图应 100%）；
      2. 干净句被改动的位数 ≤ 1/4（导出图损坏时全句皆改）；
      3. 检测支路的标记数与改动位数同号（防 det/cor 错位）。
    """
    import numpy as np
    import onnxruntime as ort

    vocab = (SRC / "vocab.txt").read_text(encoding="utf-8").splitlines()
    v2i = {t: i for i, t in enumerate(vocab)}
    text = "我们对这项工作进行了认真的研究。"
    ids = np.array([[v2i.get(c, 1) for c in text]], dtype=np.int64)
    am = np.ones_like(ids)

    # torch fp32 参考前向：走同一个包装器（图内补 CLS/SEP），与引擎同构
    with torch.no_grad():
        t_det, t_cor = wrapper(torch.tensor(ids), torch.tensor(am))
    t_pred = [int(x) for x in t_cor[0].argmax(dim=-1).tolist()]
    t_det_pred = [int(np.argmax(t_det[0, i].numpy())) for i in range(len(text))]

    sess = ort.InferenceSession(str(model_path),
                                providers=["CPUExecutionProvider"])
    det, cor = sess.run(None, {"input_ids": ids, "attention_mask": am})
    q_pred = [int(np.argmax(cor[0, i])) for i in range(cor.shape[1])]
    q_det_pred = [int(np.argmax(det[0, i])) for i in range(det.shape[1])]

    agree = sum(a == b for a, b in zip(t_pred, q_pred)) / len(t_pred)
    n_changed = sum(1 for i in range(len(text)) if t_pred[i] != ids[0][i])
    n_err = sum(1 for i in range(len(text)) if q_det_pred[i] == 1)
    fixed = "".join(vocab[p] if len(vocab[p]) == 1 and p != ids[0][i] else c
                    for i, (c, p) in enumerate(zip(text, q_pred)))
    print(f"  样本：{text}")
    print(f"  模型输出：{fixed}")
    print(f"  改动位：{n_changed}/{len(text)}；det 标记：{n_err}")
    print(f"  ORT 与 torch argmax 一致率：{agree:.1%}")
    if agree < 0.99:
        raise SystemExit(f"自检失败：ORT 与 torch 一致率 {agree:.1%} < 99%，导出图损坏")
    if n_changed > max(2, len(text) // 4):
        raise SystemExit(
            f"自检失败：干净句被大改（{n_changed}/{len(text)}），导出图损坏")
    if abs(n_err - n_changed) > 2:
        raise SystemExit(
            f"自检失败：det 标记（{n_err}）与改动位（{n_changed}）严重不符")


def make_pack(model_path: Path, zip_path: Path) -> None:
    files = {"model.onnx": _sha256(model_path),
             "vocab.txt": _sha256(SRC / "vocab.txt")}
    man = {
        "schema": 1,
        "name": NAME,
        "kind": "csc",
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
        zf.write(model_path, "model.onnx")
        zf.write(SRC / "vocab.txt", "vocab.txt")
    print(f"  包：{zip_path}（{zip_path.stat().st_size >> 20} MB）")


def main() -> int:
    print("[1/5] 加载 MacBERT4CSC（BertForMaskedLM）…")
    from transformers import BertForMaskedLM

    torch = __import__("torch")
    # 必须用 eager 注意力导出：SDPA 的掩码构造里有张量布尔比较，
    # trace 时会被固化为常量（BERT 被切成单向因果注意力 → 全句乱码）。
    mlm = BertForMaskedLM.from_pretrained(str(SRC),
                                          attn_implementation="eager")
    mlm.eval()
    wrapper = build_wrapper(torch, mlm)

    OUT_DIR.mkdir(exist_ok=True)
    fp = OUT_DIR / "_macbert_fp32.onnx"
    zip_path = OUT_DIR / "macbert4csc-csc-1.0.0.zip"

    print("[2/5] 导出 fp32 ONNX（图内补 [CLS]/[SEP] + 双输出）…")
    export_onnx(torch, wrapper, fp)
    print("[3/5] onnxruntime 自检（ORT fp32 vs torch fp32 硬比对）…")
    verify(torch, wrapper, fp)
    print("[4/5] 打包 zip（fp32，不做量化——原因见文件头注释）…")
    make_pack(fp, zip_path)
    print("[5/5] 清理临时文件…")
    try:
        fp.unlink()
    except OSError:
        pass
    print(f"完成：{zip_path}")
    print("导入方式：软件「设置 → 文字纠错 → 拼写纠错包 → 导入」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
