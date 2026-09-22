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
