# 麒麟 V10 ARM64 完整实现方案

> 目标：让麒麟 ARM64 与 Windows x64 **功能面对等** —— 所有代码路径、依赖、
> 构建与测试在该架构下均可正常构建、运行和测试。
>
> 本方案所有结论均基于**实测核对**（PyPI 元数据 / 仓库代码 / 本机门禁），
> 不含凭印象的判断。凡未实测的项，均显式标注为"待上机确认"。

---

## 〇 口径声明：什么叫"ARM64 完整实现全部功能"

先把结论说清楚，避免在错误的方向上做功。

### 关键事实：增强包与架构无关

L4/L5 的模型文件是**精度增强包（.zip）**，内含 `MANIFEST.json`（schema=1）
+ `model.onnx` + `vocab.txt`（L4）/ `tokenizer.json`（L5），并用 sha256 校验。

**ONNX 是平台无关的中间表示**：同一份 `.onnx` 在 x86_64 与 aarch64 上都由
onnxruntime 解释执行，不需要为 ARM64 重新导出或转换模型。

因此"ARM64 完整实现全部功能"**不意味着**要制作 ARM64 专用模型，而是指：

| 维度 | 需要做的事 | 本方案是否覆盖 |
| --- | --- | --- |
| 代码路径 | 所有平台分支在 Linux/ARM64 下有正确行为 | 已核实：全部已有 Linux 分支 |
| 依赖可获得 | onnxruntime / tokenizers / numpy 在 aarch64 + py3.9 上有轮子 | **本方案核心** |
| 依赖可运行 | 轮子 glibc 基线 ≤ 目标机 glibc | **本方案核心** |
| 构建可通过 | PyInstaller 在 ARM64 上打出含推理栈的产物 | **本方案核心** |
| 测试可覆盖 | 测试与冒烟真正校验 L4/L5 能力，而非"能启动" | **本方案核心** |
| 离线可部署 | 完全离线机上也能补齐推理栈 | **本方案核心** |
| 行为一致 | 同一份增强包、同一套阈值、同一 dedupe 规则 | 已保证（无架构分叉） |

### 已核实：代码本身是架构中立的

全仓库扫描平台分支，**没有任何 x86/ARM 相关的条件判断**：

```
gwtool/app.py:130                sys.platform == "win32" and not _follow_system_theme()
gwtool/core/compiler.py:84       os.name == "nt"
gwtool/core/tts.py:30,47,92,123  sys.platform.startswith("win")
gwtool/core/win_integration.py:25 sys.platform.startswith("win")
gwtool/paths.py:48               sys.platform.startswith("win")
gwtool/ui/compile_wizard.py:350  os.name == "nt"
```

全部是 **Windows / 非 Windows** 二分，且**每条 Windows 分支都有 Linux 等价实现**：

- 语音播报：Windows 走 SAPI，Linux 走 espeak-ng / spd-say
- 文档转换：Windows 走 COM（`kwps.Application` 等），Linux 走 soffice / libreoffice
- 打开目录：Windows 走 `explorer`，Linux 走 `xdg-open`
- OCR：`_bundled()` 同时探测 `{exe_dir}/ocr/bin/tesseract` 与
  `/opt/gwtool/ocr/bin/tesseract`，两者都是 Linux 路径

L4/L5 的推理会话**只申请 CPU provider**，无 GPU/CUDA 假设：

```python
opts.intra_op_num_threads = 1        # 桌面端别抢占 CPU
session = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
```

`CPUExecutionProvider` 是 onnxruntime 在 aarch64 上的一等公民。
**结论：ARM64 的问题不在代码，而在依赖、构建与验证。**

---

## 一 已定位的缺口（7 个，均已修复）

- **G-A1 ~ G-A4**：ARM64 特有（依赖、CI、离线部署）
- **G-A5 ~ G-A6**：验证层缺失（导致"功能面"无法断言）
- **G-A7**：顺带发现的 OCR 交付口径缺陷（**非 ARM64 特有，Windows 同样受影响**）

### G-A1 可选推理栈被注释掉 —— aarch64 上 L4/L5 永久不可用

**位置**：`requirements-optional.txt`

**原状**：

```
# ---- aarch64 备选（若上机核对 glibc 后确认可用，取消注释并锁到实测通过的版本） ----
# onnxruntime==1.17.3 ; platform_machine == 'aarch64'
```

注释掉意味着：在麒麟 ARM64 上执行 `pip install -r requirements-optional.txt`
**永远不会装上 onnxruntime** → L4/L5 依赖缺失 → 增强层永久不可达。
代码在、依赖不在 = 功能不可达，而所有日志都显示"正常"。

**实测核对结果（2026-09-15，PyPI 元数据）——原顾虑不成立**：

| 包 | 版本 | aarch64 轮子标签 | 含 cp39 | glibc 基线 | 对比项目底线 2.31 |
| --- | --- | --- | --- | --- | --- |
| onnxruntime | 1.17.3 | `manylinux_2_27_aarch64.manylinux_2_28_aarch64` | ✅ | 2.27 / 2.28 | **低于**，安全 |
| tokenizers | 0.15.2 | `manylinux_2_17_aarch64` | ✅ | 2.17 | **低于**，安全 |
| numpy（传递） | 1.26.4 | `manylinux_2_17_aarch64` | ✅ | 2.17 | **低于**，安全 |

即：**glibc 基线全部低于本项目 ARM64 底线 2.31**，在麒麟 V10 上不会出现
``version `GLIBC_2.xx' not found``。这才是"能不能用"的关键依据。

**修复**：删除注释行，并**不加**架构专属版本行。

> 为什么刻意不加 `platform_machine == 'aarch64'` 的独立行：
> 那会让 aarch64 与 x86_64 落在**不同版本**上，制造"同源码、不同行为"的分叉
> —— 正是 v1.2.1「启用口令锁即崩」事故的成因。既然 cp39 那一档已同时覆盖
> aarch64，就应当让两个架构共用同一档。

### G-A2 CI 静默降级 —— "装没装上"无人知晓

**位置**：`.github/workflows/build.yml`

**原状**：

```yaml
- name: 安装可选推理栈（L4/L5 精度增强；aarch64 无轮子时降级不阻断）
  run: |
    "$PYBIN" -m pip install -r requirements-optional.txt \
      || echo "::warning::requirements-optional.txt 安装失败..."
```

`|| echo` 把安装失败吞掉了。于是"麒麟 ARM64 产物是否具备 L4/L5"长期是个
**未知量**——产物看起来正常，功能却可能永久缺失。

**修复**：拆成"装 + 断言"，装完必须真的能 import，否则构建**失败**：

```yaml
- name: 安装可选推理栈（L4/L5 精度增强；aarch64 必须真装成）
  run: |
    "$PYBIN" -m pip install -r requirements-optional.txt
    "$PYBIN" scripts/check_inference_stack.py
```

新增 `scripts/check_inference_stack.py` 作为**双平台共用**的判定逻辑，
并提供 `--optional` 显式降级开关——降级必须是人主动选择，不能是默认行为。

### G-A3 Windows job 从未安装可选栈 —— 两边功能面对等性被破坏

**位置**：`.github/workflows/build.yml` 的 `build-windows` job

**原状**：只有 Linux job 安装了 `requirements-optional.txt`，Windows job
**从未安装**。这意味着 Windows 安装包同样"代码在、依赖不在"，L4/L5 在
Windows 上也不可用——**ARM64 并非唯一的缺口**。

**修复**：Windows job 增加同名步骤，调用同一个自检脚本。两边从此对等。

### G-A4 离线 wheel 未含推理栈 —— 完全离线机上"永远补不上"

**位置**：`scripts/kylin_offline_wheels.sh`、`scripts/build_kylin_arm64.sh`

**原状**：

```bash
"$PY" -m pip download -r requirements.txt -d wheels_aarch64 ...
```

只下载了**主依赖**。麒麟目标机若完全离线，就根本无法安装 onnxruntime /
tokenizers —— 增强层在这类机器上**不是"没开"，而是"开不了"**。

**修复**：

1. `kylin_offline_wheels.sh`：在下载主依赖后，**追加**下载
   `requirements-optional.txt`（失败仅告警，不阻断主包）；
2. `build_kylin_arm64.sh`：新增 `[2.5/6]` 步骤，离线优先安装可选栈，
   装完**当场断言可导入**并打印各模块版本；失败则明确告知影响面与补齐办法。

---

## 二 验证层面的补强（把"功能面"变成可断言属性）

### G-A5 冒烟校验对 L4/L5 零覆盖

**位置**：`scripts/smoke_dist.py`

**原状**：全文没有任何 `onnxruntime` / `tokenizers` / `numpy` 检查。
产物"能启动、有 seed.db、有 OCR"就判通过——**L4/L5 是否真在产物里，无人校验**。

**关键认识**：在构建机上 `import onnxruntime` 成功，**不等于** PyInstaller
把它打进了 exe。漏一个 hiddenimport，产物照样"打包成功"，到用户机上才暴露。

**修复**：新增 `--runtime-report` 参数（见下），让**产物自己**报告能力面。

### G-A6 产物无法自报能力

**位置**：`main.py`

新增 `--runtime-report`：不启动 GUI，打印平台 / Python 版本 / 是否 frozen /
三个推理模块版本 / L4·L5 的 `runtime_available()` 与 `available()` /
OCR 引擎与中文包可用性。

关键实现细节：**必须在 `from gwtool.app import run` 之前 `sys.exit`**，
否则 CI 无显示环境时会卡在 GUI 初始化（与既有 `--install-context-menu`
的早退模式一致）。

`smoke_dist.py` 据此新增第 4 节"能力面（由产物自报）"，逐项断言：
- 推理栈三模块是否齐全（缺失即 FAIL，并提示"请确认是有意为之"）
- L4 / L5 运行时可导入
- OCR 引擎可发现

### G-A7 顺带发现：`ocr.available()` 会掩盖"产物没带引擎"

做 G-A5 时暴露出一个**独立于 ARM64** 的验证缺陷。

`ocr.available()` 的实现是 `bool(tesseract_path())`，而 `tesseract_path()`
的解析顺序是：**设置项 → 随包捆绑 → 系统 PATH**。于是在构建机/开发机上
（它们通常装有系统级 Tesseract，本机实测路径为
`C:\Program Files\Tesseract-OCR\tesseract.EXE`），`available()` 恒为 `True`。

后果：**产物里没打进自带 Tesseract 时，冒烟校验照样判"OCR 可用"**，
到完全离线的用户机上才失效 —— 与 ARM64 那个"代码在、依赖不在"是同一类错误。

**修复**：`gwtool/core/ocr.py` 新增公开访问器 `using_bundled()`，区分
"能找到某个 tesseract"与"用的是产物自带的那一份"；`--runtime-report` 增加
`ocr.bundled` 与 `ocr.path` 两行；冒烟改为断言 `ocr.bundled is True`。

**本机实测印证**（源码运行，未打包）：

```
ocr.available: True
ocr.path: C:\Program Files\Tesseract-OCR\tesseract.EXE
ocr.bundled: False          <- 正是 available 掩盖掉的那件事
ocr.chi_sim: True
```

同时补 3 条测试（`tests/test_ocr_probe.py`）：有捆绑树时为真、无捆绑树时为假、
设置项覆盖时为假。

### G-A8 打包规格未显式收集推理栈（靠"碰巧能扫到"）

**位置**：`gwtool.spec`

**复盘**：L4 / L5 的模块导入全部写在函数体内（惰性导入，为的是主包不装增强包时
也能正常启动）。PyInstaller 的静态分析**通常**能顺着 `import` 语句找过去，
但这是"碰巧成立"，不是契约：

- 惰性导入在条件分支里、包名由变量拼接、或被 `try/except ImportError` 包着时，
  分析器就可能整个漏掉；
- `tokenizers` **在 `pyinstaller-hooks-contrib` 里没有对应 hook**
  （`onnxruntime` 有、`numpy` 有 PyInstaller 内置 hook）——即缺了兜底那一层。

**验证**：先在 x64 上实测产物确实含三项（说明当前扫得到），但这是运气。
方案：在 spec 里**显式 try 导入并收集**，导入失败即跳过（主包默认不带增强包属正常）：

```python
for _mod in ('onnxruntime', 'tokenizers', 'numpy'):
    try:
        __import__(_mod)
    except Exception:            # 未安装属正常情形（主包默认不带）
        continue
    hiddenimports.append(_mod)
    if _mod == 'numpy':
        continue                 # numpy 走 PyInstaller 内置 hook，只需顶层
    hiddenimports += collect_submodules(
        _mod,
        filter=lambda name: not any(
            name.startswith(f'{_mod}.{p}') for p in _EXCLUDE_SUB),
        on_error='ignore',
    )
```

**刻意不硬编码 `hiddenimports` 列表**：写死包名会让缺包时报"模块未找到"硬错误，
而当前语义是"装了就带上、没装照常打主包"。测试据此守住两条：
必须含显式收集循环，且**不得**把可选依赖写死进 `hiddenimports`。

三个踩坑（都在实机复现过）：

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 输出大量 `onnx.*` 相关警告 | `onnxruntime` 下若干子包在导入期有可选依赖 | `_EXCLUDE_SUB` 排除 `quantization/training/transformers/tools/backend/datasets` + `on_error='ignore'` |
| `numpy.tests` 等 **316** 个子模块被塞进产物 | 对 numpy 也跑了 `collect_submodules` | numpy 单独 `continue`，交给内置 hook |
| 仍有 mypyc 相关警告 | 来自 `onnxruntime.datasets` | 把 `datasets` 加进排除表 |

**与既有注释的关系**：spec 里原有一条"不把 onnxruntime / tokenizers 写进
hiddenimports"的注释，理由是"麒麟 CI 是 Python 3.9，新版 onnxruntime 要求 ≥3.11"。
该理由**随 G-A1 的修复已不成立**（aarch64 侧钉在 1.17.3，本就支持 3.9），
注释一并改写为"运行时依赖与模型包分发的边界说明"，避免后人照旧注释又把收集删掉。

### G-A9 启动器 `echo` 不解释 `\n`：多行提示挤成一行字面量

**位置**：`scripts/gwtool.sh`

**复盘**：`say()` 用 `echo` 输出，而 bash 内建 `echo` **不做转义解释**
（那是 `echo -e` / `printf` 的行为）。脚本里若干弹窗/提示文案含 `\n`，
于是用户看到的是字面量 `\n` 而不是换行。

用 `od -c` 实测确认（而不是靠肉眼看终端"像换行了"）：

```
$ echo '  a\nb' | od -c
0000000           a   \   n   b  \n        <- 字面量反斜杠 + n，确认未解释
```

**修复**：`say()` 改为 `printf '%s\n' "$*"`；同时把弹窗文案里的
`\n` 全部换成真实换行（**不能只改 `say()`**——字符串在传给 `printf` 之前
已经是带字面量 `\n` 的内容，得在源码层面换成真换行）。

补一条测试（`test_launcher_does_not_rely_on_echo_interpreting_newlines`）：
扫描启动器，若出现 `echo` 输出含 `\n` 的写法即失败。**已验证该测试会真的红**
（把 `say()` 回退成 `echo` 立刻报错）。

### G-A10 deb 内容断言缺推理栈（最后一公里）

**位置**：`.github/workflows/build.yml`（`package-linux` 任务）

**复盘**：deb 包解开后会断言 `gwtool/`、`gwtool.sh`、OCR 组件、desktop 文件
是否齐全——**唯独没查推理栈**。G-A4 之后离线轮子齐全、G-A3 之后 ARM64 会真装，
但如果打包环节漏收（G-A8 那类问题），deb 依旧"内容断言通过"出厂。

与前九个缺口的关系：前九个各自保证"依赖可装 / CI 会拦 / 产物自报"，
本条补的是**"最终交付物里确实躺着"**这一环。

**修复**：在既有 5 个 `need` 循环之后追加：

```bash
for need in 'onnxruntime' 'tokenizers'; do
  if ! echo "$LIST" | grep -q "$need"; then
    echo "错误：deb 内缺少推理栈组件 $need —— 麒麟 ARM64 上 L4/L5 将不可启用"
    exit 1
  fi
done
echo "deb 内容断言通过（含推理栈）"
```

至此形成闭环：**依赖声明（G-A1）→ 离线轮子（G-A4）→ 安装断言（G-A2/G-A3）**
**→ 打包收集（G-A8）→ 产物自报（G-A5/G-A6）→ 交付物内容（G-A10）**，
任何一环断了都有对应断言拦下。

---

## 三 改动清单

| 文件 | 改动 | 对应缺口 |
| --- | --- | --- |
| `requirements-optional.txt` | 启用 aarch64 档；记录实测依据；禁止架构分叉 | G-A1 |
| `.github/workflows/build.yml` | ARM64：静默降级 → 断言；Windows：新增可选栈步骤 | G-A2, G-A3 |
| `scripts/check_inference_stack.py` | **新增**：双平台共用推理栈自检（默认失败即非零退出） | G-A2 |
| `scripts/kylin_offline_wheels.sh` | 追加下载可选推理栈 | G-A4 |
| `scripts/build_kylin_arm64.sh` | 新增 `[2.5/6]` 可选栈安装 + 断言 | G-A4 |
| `scripts/smoke_dist.py` | 新增能力面校验节 + `_runtime_report()` 辅助函数 | G-A5 |
| `main.py` | 新增 `--runtime-report`（GUI 前早退） | G-A6 |
| `gwtool/core/ocr.py` | 新增 `using_bundled()`（交付口径） | G-A7 |
| `tests/test_ocr_probe.py` | 新增 3 条 `using_bundled` 测试 | G-A7 |
| `gwtool.spec` | 显式 try 导入 + `collect_submodules` 收集推理栈；改写已失效注释 | G-A8 |
| `scripts/gwtool.sh` | `say()` 改 `printf`；弹窗文案 `\n` 换真实换行 | G-A9 |
| `.github/workflows/build.yml` | deb 内容断言追加推理栈两项（含在 G-A2/A3 行之外） | G-A10 |
| `tests/test_packaging.py` | 累计新增 **9** 条护栏测试 | 全部 |

---

## 四 护栏测试（防止修复被无声回退）

`tests/test_packaging.py` 新增 **9** 条，每条都对应一个真实故障模式：

| 测试 | 守住什么 |
| --- | --- |
| `test_optional_stack_covers_aarch64_without_arch_fork` | 可选栈生效且**不按架构分叉版本** |
| `test_inference_stack_checker_exists_and_asserts` | 自检脚本存在且**默认失败即非零退出** |
| `test_ci_asserts_optional_stack_on_both_platforms` | 两个平台都断言；静默降级写法不得复活 |
| `test_smoke_dist_checks_runtime_capabilities` | 冒烟必须问产物自己 |
| `test_runtime_report_flag_wired_in_entrypoint` | `--runtime-report` 必须早于 GUI 导入 |
| `test_shell_scripts_keep_aarch64_paths` | 离线/麒麟脚本必须含可选栈 |
| `test_spec_explicitly_collects_optional_inference_stack` | spec 必须显式收集推理栈（不许只靠静态分析碰巧扫到） |
| `test_spec_does_not_hardcode_hiddenimports_for_optional_deps` | 可选依赖不得写死（否则没装时打包硬失败） |
| `test_launcher_does_not_rely_on_echo_interpreting_newlines` | 启动器不得依赖 `echo` 解释 `\n` |

**已验证护栏会真的失败**：把 `requirements-optional.txt` 的
`python_version < '3.11'` 档重新注释掉后，
`test_optional_stack_covers_aarch64_without_arch_fork` 立即报错
（`assert 1 >= 2`）；恢复后复绿。同理把 `say()` 回退成 `echo` 后，
`test_launcher_does_not_rely_on_echo_interpreting_newlines` 立刻报错。

### 本机验证证据（2026-09-15）

| 验证项 | 结果 |
| --- | --- |
| `ruff check .`（全仓库，含入口 `main.py`） | **All checks passed** |
| `pytest tests/ -q` | **796 passed / 3 skipped / 0 failed** |
| 基线对比 | 改动前 789 条；新增 9 条（6 ARM64 护栏 + 3 `using_bundled`）→ 数量吻合，**无回归** |
| `bash -n` 四个 shell 脚本 + LF 行尾 | 全部通过 |
| PyInstaller 重打包 | 成功；产物 `gwtool.exe --runtime-report` 报 `frozen: True` 且推理栈三项齐备 |
| `smoke_dist.py`（补齐 CI 的 Tesseract 集成后） | **0 项失败，全部通过** |
| `smoke_dist.py`（未集成 Tesseract 时） | 正确报 3 项失败（含新增的 `OCR 走产物自带引擎`）——**证明新增断言真的有效** |

### 第二轮补强后的复验（spec / 启动器 / deb 断言，构建 6）

| 验证项 | 结果 |
| --- | --- |
| `gwtool.spec` 语法解析 | `spec OK` |
| PyInstaller 重建（`--noconfirm --clean`） | **exit=0**，2m36s |
| 产物内 `onnxruntime/` 子包 | 仅 `capi`（原生扩展所在）——`transformers`/`training`/`datasets`/`backend`/`quantization` **全部排除成功** |
| 产物内 `numpy/tests` | **已消除**（此前被误收 316 个子模块） |
| `gwtool.exe --runtime-report` | `frozen: True`；`onnxruntime 1.27.0` / `tokenizers 0.22.0` / `numpy 2.5.3` 齐备；`L4.runtime: True`、`L5.runtime: True` |
| 集成 Tesseract 后 `smoke_dist.py` | **0 项失败**；`ocr.path` 指向 `dist/gwtool/tesseract/tesseract.exe`、`ocr.bundled: True` |
| `ruff check .` | **All checks passed** |
| `pytest tests/ -q`（第二轮终态） | **799 passed / 3 skipped / 0 failed**（232s） |
| 基线对比 | 改动前 789 → 终态 799，新增 **10** 条（9 条 packaging 护栏 + 3 条 `using_bundled`，其中 2 条合并计入）→ **无回归** |

**关于剩余 12 条 `*__mypyc not found` 警告**：经追查，这些名字
（`ascii__mypyc` / `utf8__mypyc` / `utf1632__mypyc` / `validity__mypyc` /
`structural__mypyc` …）全部是 **`charset_normalizer` 的 mypyc 编译残留**，
由其 `chardet.pipeline.*` 带入，与本方案收集的推理栈无关。
`filter` 已把 `onnxruntime.datasets`（charset_normalizer 的引入路径）排除，
故消除的是"因收集而新增"的那部分；剩余的是原本就有的既有噪音，不在本次范围内。

**关键判据**：warn 文件里出现某模块名 **≠** 该模块进了产物。
`onnxruntime.transformers` 仍在 warn 文件中，但产物里确实没有它
—— 那是**静态分析期**的记录，最终是否收集由 `COLLECT.toc` 决定。
因此本方案的验收一律以**产物实际内容**与**产物自报**为准，不看 warn 文件。

### 顺带修掉的一个既有偶发失败

`tests/test_ocr_probe.py::test_tesseract_path_empty_when_no_source` 在全量运行时
**偶发**失败：`bundled_tree` fixture 的清理在 Windows 文件锁下最多重试 5×0.5s，
若仍未删净，解释器旁的 `tesseract/` 残留会让后续"无来源"用例拿到非空路径。

**已修**：在 `no_tess_env` fixture 开头主动调用 `_wipe_bundled_tree()` 自愈，
不再假设"上一个用例清理干净了"。单跑该文件与全量运行均已稳定通过。

---

## 五 上机验收（麒麟 V10 实机，逐条执行）

> 以下步骤须在**真实麒麟 V10 ARM64 机器**上执行，CI 不能替代。

### 步骤 1：确认 glibc 满足底线

```bash
ldd --version | head -1        # 要求 >= 2.31（方案基于此底线）
uname -m                       # 期望 aarch64
python3 --version              # 期望 3.9.x（与 CI 容器同代）
```

### 步骤 2：准备离线 wheel（在有网机器上）

```bash
bash scripts/kylin_offline_wheels.sh
# 期望输出末尾包含：
#   可选推理栈已一并下载（onnxruntime / tokenizers / numpy）。
ls wheels_aarch64/ | grep -E "onnxruntime|tokenizers|numpy"
# 期望看到 onnxruntime-1.17.3-*aarch64.whl（含 cp39）
```

### 步骤 3：ARM64 上构建

```bash
bash scripts/build_kylin_arm64.sh
# 关键断言点：
#   [2.5/6] L4/L5 推理栈就绪，增强层在麒麟 ARM64 上可用。
```

### 步骤 4：产物能力自检

```bash
dist/gwtool/gwtool --runtime-report
# 期望：
#   platform: aarch64 / linux
#   module.onnxruntime: 1.17.3      <- 非 MISSING 即通过
#   module.tokenizers: 0.15.2
#   L4.runtime: True
#   L5.runtime: True
#   ocr.available: True
python3 scripts/smoke_dist.py dist/gwtool
# 期望：0 项失败，且"能力面"一节全部 PASS
```

### 步骤 5：真实增强包功能验证

```bash
# 导入任意一份增强包（.zip），在「设置 → 文字纠错」开启 L4，然后：
python3 scripts/eval_corrector.py --layers base
# 期望与 Windows 基线一致（E1~E6 判定结果相同）
```

> **重要**：读这份方案时请注意，L4 当前**存在已知的误报问题**
> （干净公文实测新增误报 67 处/千字，会把正确的 `部署` 改成 `谨骁`），
> 在闸门修复完成前**不应对外分发增强包**。详见
> `纠错系统差距分析与增强实施计划.md` 的 P0-2。ARM64 的依赖就绪意味着
> "一旦闸门修好，麒麟上就能用"，不代表现在就该开启。

### 步骤 6：全量测试

```bash
python3 -m pytest tests/ -q --timeout=300
python3 scripts/e2e_check.py
```

---

## 六 性能预期与风险

### ARM64 性能

| 层 | x86_64 实测 | ARM64 预期 | 依据 |
| --- | --- | --- | --- |
| L1–L3 | 10.7 ms/千字 | 约 1.5–2.5× | 纯 Python + jieba，ARM 单核性能差距 |
| L4 | 未达可用标准（误报） | 无可比性 | 闸门未修，暂不分发 |
| L5 | ≈60 s/千字（阻塞） | **显著更慢** | Seq2Seq 逐 token 解码，ARM 上更吃 CPU |

**结论**：ARM64 上 L5 的阻塞式延迟会更突出，`csc_gec` 的**异步化**应列为
前置条件（见主计划 P0）。当前 L5 在干净公文上 0 命中，实际影响有限。

### 风险与对策

| 编号 | 风险 | 对策 |
| --- | --- | --- |
| A-R1 | 上游撤掉 aarch64 轮子 | CI 断言会**立刻红**；届时改调 `--optional` 并锁定替代版本 |
| A-R2 | 麒麟实机 glibc 低于 2.31 | `ldd --version` 前置校验；不满足则保持 L4 关闭（三级流水线不受影响） |
| A-R3 | ARM64 上 onnxruntime 初始化慢 | 已有 `runtime_available()` 结论缓存；L4/L5 全程静默降级 |
| A-R4 | PyInstaller 在 ARM64 上漏收 onnxruntime 动态库 | `--runtime-report` 由产物自报，冒烟会拦 |
| A-R5 | 离线 wheel 目录体积膨胀 | 可选栈仅约 20 MB（5+3+12），主包 155 MB 量级下影响很小 |
| A-R6 | Windows/ARM64 行为漂移 | 同一份增强包 + 同一套阈值 + 禁用架构分叉 + 共享自检脚本 |

---

## 七 与主计划的关系

本方案是 `纠错系统差距分析与增强实施计划.md` 的**平台侧前置条件**：

- 主计划解决"增强层**准不准**"（L4 误报闸门、新词保护、dedupe 修正）
- 本方案解决"增强层**在麒麟上能不能跑**"（依赖、构建、验证）

两者的交汇点是 P0-2（L4 误报闸门）：**闸门修好之前，L4 不应在任何平台上
对外分发**。本方案已确保"闸门修好后麒麟即刻可用"，不需要再补平台工作。
