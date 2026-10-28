# -*- coding: utf-8 -*-
"""落盘文件名的 Windows 陷阱处理：附件存储与批量产物共用的**唯一**实现。

Windows 上有两类名字「看着完全合法、写进去却不是那个文件」：

  · **保留设备名** —— ``CON`` / ``PRN`` / ``AUX`` / ``NUL`` /
    ``COM1``~``COM9`` / ``LPT1``~``LPT9``，带扩展名同样命中（``CON.txt``）。
    实测：``path.write_text()`` 对 ``CON.txt`` **返回成功、文件却不存在**
    （内容进了控制台设备，``stat().st_size == 0``）；``PRN`` / ``LPT1``
    则直接抛 ``FileNotFoundError``。
  · **以点或空格结尾** —— 系统会静默截掉尾部，调用方记下的路径与实际文件
    不一致（``报告.`` 落盘后是 ``报告``）。

这两条规则原先各写了一份：``core.batch.safe_filename`` 正确，
``core.attachments.safe_stored_name`` 漏了设备名。后果不是"标题写不出来"
那么简单 —— `safe_stored_name` 是**外部名字的清洗闸**（`backup._restore_attachments`
直接拿它清洗备份包里的条目名），漏掉设备名时 ``open("…/attachments/CON.txt","wb")``
**不报错但写 0 字节**：恢复计数照样 +1，附件目录里却没有文件；而
``Path.exists()`` 又因设备语义返回 True，连"缺失附件清单"都不会报出来
（实测：包内 1 个附件 → 恢复成功、目录为空、无任何告警）。
收口到本模块后，两侧只保留各自的「怎么切出单段名」差异。
"""
from __future__ import annotations

# Windows 保留设备名（不区分大小写，带扩展名同样命中）
RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def fix_windows_traps(name: str) -> str:
    """修掉「尾点/尾空格」与「保留设备名」，返回可直接落盘的单段名字。

    幂等：对已经处理过的名字再调用一次结果不变（``_CON.txt`` 不再加前缀）。
    只做这两件事，不改分隔符与非法字符 —— 那属于调用方「怎么取单段名」的差异。
    """
    s = str(name or "").rstrip(" .")
    if not s:
        return s
    if s.split(".")[0].upper() in RESERVED_DEVICE_NAMES:
        s = "_" + s
    return s
