# -*- coding: utf-8 -*-
"""校验 L4/L5 可选推理栈是否真的就绪（跨平台，Windows x64 与麒麟 ARM64 通用）。

为什么需要这个脚本
------------------
L4（神经精排）与 L5（语法纠错）依赖 `onnxruntime` + `tokenizers`（L5 另需），
它们列在 `requirements-optional.txt`，**不进主 requirements**。这就留下一个
很容易被漏掉的死角：

    代码在，依赖不在 → 功能不可达，而所有日志看起来都"正常"。

历史上 CI 的可选栈步骤写成 `pip install ... || echo warning`（静默降级），
于是"ARM64 是否具备 L4/L5"长期是个未知量。本脚本把这件事变成**可断言**的：
装完必须真的能 import，缺任何一个就以非零退出码失败。

用法
----
    python scripts/check_inference_stack.py            # 必需全部就绪，缺则退出码 1
    python scripts/check_inference_stack.py --optional # 只在缺失时告警，仍退出 0

退出码：0 全部就绪（或 --optional 下允许降级）；1 有模块缺失。
"""
from __future__ import annotations

import importlib.util
import platform
import sys

# L4 需要 onnxruntime；L5 额外需要 tokenizers；两者都经 onnxruntime 传递引入 numpy。
REQUIRED = ("onnxruntime", "tokenizers", "numpy")

# 模块 -> 用途说明，缺失时帮助使用者判断影响面（而不是只看到"缺个包"）。
PURPOSE = {
    "onnxruntime": "L4 神经精排 / L5 语法纠错的推理运行时",
    "tokenizers": "L5 语法纠错的分词器（仅 L4 不需要）",
    "numpy": "onnxruntime 的张量输入（本项目其余代码不需要）",
}

# 英文区域设置的 Windows 控制台默认 charmap，print 中文会 UnicodeEncodeError，
# 让校验脚本先崩掉，故强制 UTF-8。（与 smoke_dist.py 同一处理。）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    allow_degrade = "--optional" in sys.argv[1:]

    print("== 可选推理栈自检（L4/L5）")
    print(f"   平台：{platform.machine()} / {sys.platform} / "
          f"Python {platform.python_version()}")

    missing: list[str] = []
    for mod in REQUIRED:
        # 用 find_spec 探测"在不在"，不真的 import：探测本身零副作用、零耗时，
        # 且不会因为 onnxruntime 的初始化开销拖慢构建脚本。
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
            print(f"   [缺失] {mod:12s} —— {PURPOSE[mod]}")
            continue
        try:
            version = getattr(__import__(mod), "__version__", "?")
        except Exception as exc:
            missing.append(mod)
            print(f"   [异常] {mod:12s} 可找到但导入失败："
                  f"{type(exc).__name__}: {exc}")
            continue
        print(f"   [就绪] {mod:12s} {version}")

    if not missing:
        print("== 结论：L4/L5 推理栈就绪，增强层在本平台可用。")
        return 0

    if allow_degrade:
        print(f"== 结论：缺失 {'、'.join(missing)} —— 已按 --optional 降级放行；"
              "本次构建的产物不含 L4/L5 运行时（纠错退化为三级流水线）。")
        return 0

    print(f"== 结论：缺失 {'、'.join(missing)} —— L4/L5 将不可启用。", file=sys.stderr)
    print("   若某平台确实无法提供这些包，请显式改调 --optional 并在日志中说明；"
          "不要让此处静默通过，否则功能面会被悄悄削弱。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
