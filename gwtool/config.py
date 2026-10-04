# -*- coding: utf-8 -*-
"""汇编相关的用户偏好设置。

为什么单独起一个小模块而不散落在 `compile_wizard` 里：写入设置有很多陷阱
（库不可写、值非法、重启后回到旧值），集中在一处才好统一处理，也好被测试
直接调用而不用构造整个向导。本模块只做"读写 + 归一 + 静默降级"。
"""
from __future__ import annotations

from gwtool import logs
from gwtool.db import dao

log = logs.get_logger("config")

SETTING_COMPILE_CATEGORY = "compile_result_category"
SETTING_COMPILE_ASK_REGISTER = "compile_ask_register"

# 段落参考写作功能的三个能力开关（分级交付 / 逐级可回滚）。
#
# 为什么要开关、而且**默认全开**：本功能是新增能力，但"新增"本身不该让
# 用户在出问题时无处可退。任一环节出状况，用户（或我们远程指导）都能把对应
# 开关关掉，界面立刻回到改动前的样子 —— 不需要回滚数据库、不需要换版本。
# 默认全开是因为功能已通过门禁与验收；开关是应急预案，不是灰度策略。
SETTING_PARA_REF = "para_ref_enabled"
SETTING_PARA_ALIGN = "para_align_enabled"
SETTING_PARA_GEN = "para_gen_enabled"

# 「汇编成果」标签：产物入库时打上，便于用户在资料库里一眼找出所有汇编成品，
# 也便于体检/统计按标签聚合。用中文标签是因为它**直接展示给用户**。
COMPILE_PRODUCT_TAG = "汇编成果"


def compile_result_category() -> int:
    """汇编产物入库后归入哪个分类：0 = 不分类（默认）。

    默认必须是 0 而不是"某个顺手挑的分类"：把产物自动塞进用户的既有分类，
    会让"我明明没动过分类，怎么多出来这些"变成一次小型困惑。缺省不干预。
    """
    try:
        value = int(dao.get_setting(SETTING_COMPILE_CATEGORY) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def set_compile_result_category(category_id: int) -> bool:
    """写入产物分类。返回**是否真的落库成功**。"""
    try:
        value = str(max(0, int(category_id)))
    except (TypeError, ValueError):
        return False
    try:
        dao.set_setting(SETTING_COMPILE_CATEGORY, value)
        return True
    except Exception as exc:
        log.warning("汇编产物分类未能持久化（%s），重启后将回到原值", exc)
        return False


def compile_ask_register() -> bool:
    """汇编完成后是否询问「登记到发文台账」。默认**询问但不自动写**。

    刻意不做"自动登记"：台账是**对外发出公文**的正式记录，一件都没发出去
    就不能凭空多出一行。询问式让用户每一次都明确表态。
    """
    try:
        return dao.get_setting(SETTING_COMPILE_ASK_REGISTER, "1") != "0"
    except Exception:
        return True


def set_compile_ask_register(flag: bool) -> bool:
    """写入"是否询问登记"。返回**是否真的落库成功**。"""
    try:
        dao.set_setting(SETTING_COMPILE_ASK_REGISTER, "1" if flag else "0")
        return True
    except Exception as exc:
        log.warning("汇编登记询问开关未能持久化（%s），重启后将回到原值", exc)
        return False


# ------------------------------------------------ 段落参考功能开关
def _flag(key: str, default: bool = True) -> bool:
    """读一个布尔设置。读不到/读坏了都返回 default（静默降级）。

    为什么"读坏了"要返回**默认值**而不是 False：设置读取失败是环境问题
    （库只读、锁冲突），不该被解释成"用户关掉了这个功能"。返回 False 会让
    "库有点小问题"表现为"功能自己消失了"，用户完全无法理解。
    """
    try:
        return dao.get_setting(key, "1" if default else "0") != "0"
    except Exception:
        return default


def _set_flag(key: str, flag: bool) -> bool:
    try:
        dao.set_setting(key, "1" if flag else "0")
        return True
    except Exception as exc:
        log.warning("开关 %s 未能持久化（%s），重启后将回到原值", key, exc)
        return False


def para_ref_enabled() -> bool:
    """段落级检索与引用定位（L1）。关掉后面板只剩"整篇"粒度。"""
    return _flag(SETTING_PARA_REF)


def set_para_ref_enabled(flag: bool) -> bool:
    return _set_flag(SETTING_PARA_REF, flag)


def para_align_enabled() -> bool:
    """内容对齐（L2）。关掉后对齐入口隐藏。"""
    return _flag(SETTING_PARA_ALIGN)


def set_para_align_enabled(flag: bool) -> bool:
    return _set_flag(SETTING_PARA_ALIGN, flag)


def para_gen_enabled() -> bool:
    """骨架生成（L3）。关掉后生成入口隐藏，仍可用内置 12 文种骨架。"""
    return _flag(SETTING_PARA_GEN)


def set_para_gen_enabled(flag: bool) -> bool:
    return _set_flag(SETTING_PARA_GEN, flag)
