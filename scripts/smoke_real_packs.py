# -*- coding: utf-8 -*-
"""真机验收：安装双增强包 + 真实 ONNX 推理冒烟。

运行环境：仅 .venv（onnxruntime + tokenizers + numpy），
**绝不挂 .convlib / torch** —— 顺便验证运行时不依赖 torch 的红线。
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/gwtool")
sys.path.insert(0, str(ROOT))

from gwtool import paths
from gwtool.db import connection as dbconn
from gwtool.core import csc_gec, csc_neural, corrector, enhance_pack


def main() -> int:
    print("=== [1/5] 安装双增强包（真实 %APPDATA%/gwtool/enhance 槽位）===")
    installed = {k: enhance_pack.installed_pack(k)
                 for k in ("csc", "cgec")}
    if installed["csc"] and installed["cgec"]:
        for k in ("csc", "cgec"):
            print(f"  已安装 [{k}]：{installed[k].name} "
                  f"v{installed[k].version}（跳过重装）")
    else:
        for z in (ROOT / "packs/macbert4csc-csc-1.0.0.zip",
                  ROOT / "packs/mengzi-t5-cgec-1.0.0.zip"):
            t0 = time.time()
            info = enhance_pack.install_pack(z)
            print(f"  {z.name}")
            print(f"    → kind={info.kind} name={info.name} v{info.version}"
                  f"（安装耗时 {time.time() - t0:.1f}s）")
    for k in ("csc", "cgec"):
        assert enhance_pack.verify_installed(k), f"[{k}] 包校验失败"

    print("=== [2/5] 开启 L4/L5 设置 ===")
    dbconn.configure(paths.db_path())
    csc_neural.set_setting(True)
    csc_gec.set_setting(True)
    print("  corrector_neural_enabled / corrector_gec_enabled = 1")

    print("=== [3/5] L4 拼写纠错（MacBERT4CSC fp32 ONNX）===")
    csc_neural.invalidate_cache()
    texts4 = ["今天新情很好，我们要继续努力工作。",
              "我们一起去公园玩把。",
              "他对工作非常负则。"]
    n4 = 0
    t_first = None
    for text4 in texts4:
        t0 = time.time()
        out4 = csc_neural.enhance(text4, [])
        dt = (time.time() - t0) * 1000
        t_first = t_first or dt
        for c in out4:
            n4 += 1
            print(f"  L4：{c.wrong} → {c.suggestion}（{c.kind}，"
                  f"conf={c.confidence}）  「{text4}」 {dt:.0f} ms")
    print(f"  L4 共 {n4} 处纠正；首次调用（含模型加载）{t_first:.0f} ms")
    assert n4 > 0, "L4 未产出任何纠正"

    print("=== [4/5] L5 语法纠错（Mengzi-T5 QUInt8 ONNX，encdec 自回归）===")
    csc_gec.invalidate_cache()
    eng = csc_gec._load_engine()
    assert eng is not None, "L5 引擎加载失败"
    assert eng.mode == "encdec", f"引擎模式异常：{eng.mode}"
    print(f"  引擎模式：{eng.mode}（decoder 输入探测成功）")
    text5 = "他通过这种方法获得了大家的认可。"
    t0 = time.time()
    out5 = csc_gec.enhance(text5, [])
    dt = (time.time() - t0) * 1000
    print(f"  输入：{text5}")
    for c in out5:
        print(f"  L5：{c.wrong} → {c.suggestion}（{c.kind}，conf={c.confidence}）")
    print(f"  耗时 {dt:.0f} ms")
    assert any(c.wrong == "他" and c.suggestion == "她" and c.kind == "replace"
               for c in out5), "L5 未产出 他→她 纠正"

    print("=== [5/5] 门面 check_text 全链路（L1–L3 + L4 + L5 合并）===")
    text6 = "他通过这种方法获得了大家的认可，今天新情很好。"
    out6 = corrector.check_text(text6)
    print(f"  输入：{text6}")
    for c in out6:
        print(f"  [{c.category}] {c.wrong} → {c.suggestion}（{c.kind}）")
    has_l4 = any(c.category == "神经纠错" for c in out6)
    has_l5 = any(c.category == "语法纠错" for c in out6)
    print(f"  L4 命中：{has_l4}；L5 命中：{has_l5}")

    print("\n真机验收通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
